

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from config import (
    PER_TRANSACTION_SCORE_CAP,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
)
from db import get_connection
from schemas import TransactionRequest
from scoring import compute_score


class IdempotencyConflict(Exception):
    """Same idempotency_key reused with a materially different payload."""


class RateLimitExceeded(Exception):
    """Too many transactions from this user within the rate-limit window."""


class UserNotFound(Exception):
    """No transactions have ever been recorded for this user_id."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hash_payload(payload: TransactionRequest) -> str:
    
    canonical = json.dumps(
        {
            "user_id": payload.user_id,
            "amount": payload.amount,
            "description": payload.description,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def process_transaction(payload: TransactionRequest) -> Tuple[dict, int, bool]:
    
    req_hash = _hash_payload(payload)
    now_iso = _utc_now_iso()
    today = _utc_today_str()

    with get_connection() as conn:
        cur = conn.cursor()

        # 1. Idempotency fast path
        cur.execute(
            "SELECT request_hash, response_json, status_code "
            "FROM idempotency_records WHERE idempotency_key = ?",
            (payload.idempotency_key,),
        )
        existing = cur.fetchone()
        if existing:
            if existing["request_hash"] != req_hash:
                raise IdempotencyConflict(
                    "This idempotency_key was already used with a different "
                    "user_id/amount/description. Use a new idempotency_key "
                    "for a genuinely new transaction."
                )
            body = json.loads(existing["response_json"])
            body["duplicate"] = True
            return body, 200, True

        attempt = 0
        while True:
            attempt += 1
            try:
                cur.execute("BEGIN IMMEDIATE")

                window_start_iso = datetime.fromtimestamp(
                    time.time() - RATE_LIMIT_WINDOW_SECONDS, tz=timezone.utc
                ).isoformat()
                cur.execute(
                    "SELECT COUNT(*) AS c FROM transactions "
                    "WHERE user_id = ? AND created_at >= ?",
                    (payload.user_id, window_start_iso),
                )
                if cur.fetchone()["c"] >= RATE_LIMIT_MAX_REQUESTS:
                    conn.rollback()
                    raise RateLimitExceeded(
                        f"Rate limit exceeded: a user may submit at most "
                        f"{RATE_LIMIT_MAX_REQUESTS} transactions per "
                        f"{RATE_LIMIT_WINDOW_SECONDS} seconds. Please retry shortly."
                    )

                cur.execute(
                    "INSERT OR IGNORE INTO users "
                    "(user_id, total_amount, transaction_count, score_amount_total, "
                    " active_days_count, first_transaction_at, last_transaction_at) "
                    "VALUES (?, 0, 0, 0, 0, ?, ?)",
                    (payload.user_id, now_iso, now_iso),
                )

                cur.execute(
                    "INSERT INTO transactions "
                    "(user_id, amount, description, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        payload.user_id,
                        payload.amount,
                        payload.description,
                        payload.idempotency_key,
                        now_iso,
                    ),
                )
                transaction_id = cur.lastrowid

                cur.execute(
                    "INSERT OR IGNORE INTO user_active_days (user_id, active_date) "
                    "VALUES (?, ?)",
                    (payload.user_id, today),
                )
                day_is_new = cur.rowcount == 1

                capped_amount = min(payload.amount, PER_TRANSACTION_SCORE_CAP)

                cur.execute(
                    "UPDATE users SET "
                    "  total_amount = total_amount + ?, "
                    "  transaction_count = transaction_count + 1, "
                    "  score_amount_total = score_amount_total + ?, "
                    "  active_days_count = active_days_count + ?, "
                    "  last_transaction_at = ? "
                    "WHERE user_id = ?",
                    (
                        payload.amount,
                        capped_amount,
                        1 if day_is_new else 0,
                        now_iso,
                        payload.user_id,
                    ),
                )

                cur.execute(
                    "SELECT total_amount, transaction_count, score_amount_total, "
                    "active_days_count FROM users WHERE user_id = ?",
                    (payload.user_id,),
                )
                u = cur.fetchone()
                score = compute_score(u["score_amount_total"], u["active_days_count"])

                response_body = {
                    "transaction_id": transaction_id,
                    "user_id": payload.user_id,
                    "amount": payload.amount,
                    "description": payload.description,
                    "created_at": now_iso,
                    "duplicate": False,
                    "user_summary": {
                        "total_amount": round(u["total_amount"], 2),
                        "transaction_count": u["transaction_count"],
                        "ranking_score": score,
                    },
                }

                cur.execute(
                    "INSERT INTO idempotency_records "
                    "(idempotency_key, request_hash, response_json, status_code, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        payload.idempotency_key,
                        req_hash,
                        json.dumps(response_body),
                        201,
                        now_iso,
                    ),
                )

                conn.commit()
                return response_body, 201, False

            except sqlite3.IntegrityError:
                conn.rollback()
                
                cur.execute(
                    "SELECT request_hash, response_json, status_code "
                    "FROM idempotency_records WHERE idempotency_key = ?",
                    (payload.idempotency_key,),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["request_hash"] != req_hash:
                        raise IdempotencyConflict(
                            "This idempotency_key was already used with a "
                            "different user_id/amount/description."
                        )
                    body = json.loads(existing["response_json"])
                    body["duplicate"] = True
                    return body, 200, True
                if attempt >= 5:
                    raise
                time.sleep(0.05 * attempt)


def _all_ranked_users(cur) -> List[dict]:
    cur.execute(
        "SELECT user_id, total_amount, transaction_count, score_amount_total, "
        "active_days_count, first_transaction_at, last_transaction_at FROM users"
    )
    rows = cur.fetchall()
    ranked = []
    for r in rows:
        score = compute_score(r["score_amount_total"], r["active_days_count"])
        ranked.append(
            {
                "user_id": r["user_id"],
                "total_amount": round(r["total_amount"], 2),
                "transaction_count": r["transaction_count"],
                "active_days_count": r["active_days_count"],
                "first_transaction_at": r["first_transaction_at"],
                "last_transaction_at": r["last_transaction_at"],
                "ranking_score": score,
            }
        )

    ranked.sort(key=lambda x: (-x["ranking_score"], x["first_transaction_at"] or ""))
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return ranked


def get_ranking(limit: int, offset: int) -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        ranked = _all_ranked_users(cur)
    page = ranked[offset : offset + limit]
    return {
        "total_users": len(ranked),
        "limit": limit,
        "offset": offset,
        "rankings": page,
    }


def get_summary(user_id: str) -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        ranked = _all_ranked_users(cur)

    entry: Optional[dict] = next((r for r in ranked if r["user_id"] == user_id), None)
    if entry is None:
        raise UserNotFound(f"No transactions found for user '{user_id}'")

    avg = (
        round(entry["total_amount"] / entry["transaction_count"], 2)
        if entry["transaction_count"]
        else 0.0
    )
    return {
        "user_id": entry["user_id"],
        "total_amount": entry["total_amount"],
        "transaction_count": entry["transaction_count"],
        "average_transaction_amount": avg,
        "active_days_count": entry["active_days_count"],
        "first_transaction_at": entry["first_transaction_at"],
        "last_transaction_at": entry["last_transaction_at"],
        "ranking_score": entry["ranking_score"],
        "rank": entry["rank"],
        "total_ranked_users": len(ranked),
    }
