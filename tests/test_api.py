import importlib
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    for mod in ("config", "db", "service", "main"):
        sys.modules.pop(mod, None)
    import config as config_mod
    importlib.reload(config_mod)
    import db as db_mod
    importlib.reload(db_mod)
    import service as service_mod
    importlib.reload(service_mod)
    import main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c


def post_txn(client, **kwargs):
    payload = {
        "user_id": "test_user",
        "amount": 100,
        "idempotency_key": "default-key",
    }
    payload.update(kwargs)
    return client.post("/transaction", json=payload)



# Validation
def test_valid_transaction_returns_201(client):
    r = post_txn(client)
    assert r.status_code == 201
    body = r.json()
    assert body["user_id"] == "test_user"
    assert body["user_summary"]["total_amount"] == 100.0


def test_negative_amount_rejected(client):
    r = post_txn(client, amount=-5, idempotency_key="k-neg")
    assert r.status_code == 422


def test_zero_amount_rejected(client):
    r = post_txn(client, amount=0, idempotency_key="k-zero")
    assert r.status_code == 422


def test_amount_over_max_rejected(client):
    r = post_txn(client, amount=10_000_000, idempotency_key="k-huge")
    assert r.status_code == 422


def test_missing_idempotency_key_rejected(client):
    r = client.post("/transaction", json={"user_id": "test_user", "amount": 10})
    assert r.status_code == 422


def test_invalid_user_id_characters_rejected(client):
    r = post_txn(client, user_id="bad user!", idempotency_key="k-baduser")
    assert r.status_code == 422


def test_malformed_json_rejected(client):
    r = client.post(
        "/transaction", data="not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422


# Idempotency / duplicate prevention
def test_duplicate_key_same_payload_is_not_double_counted(client):
    r1 = post_txn(client, idempotency_key="dup-1")
    assert r1.status_code == 201

    r2 = post_txn(client, idempotency_key="dup-1")
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True

    summary = client.get("/summary/test_user").json()
    assert summary["transaction_count"] == 1
    assert summary["total_amount"] == 100.0


def test_duplicate_key_different_payload_is_conflict(client):
    r1 = post_txn(client, idempotency_key="dup-2", amount=100)
    assert r1.status_code == 201

    r2 = post_txn(client, idempotency_key="dup-2", amount=999)
    assert r2.status_code == 409

# Rate limiting
def test_rate_limit_enforced(client):
    statuses = []
    for i in range(8):
        r = post_txn(client, user_id="rate_user", idempotency_key=f"rate-{i}")
        statuses.append(r.status_code)
    assert statuses.count(201) == 5  
    assert statuses.count(429) == 3


# Summary
def test_summary_for_unknown_user_is_404(client):
    r = client.get("/summary/never_seen")
    assert r.status_code == 404


def test_summary_invalid_user_id_format_is_422(client):
    r = client.get("/summary/bad%20id!")
    assert r.status_code == 422


# Ranking
def test_ranking_orders_by_score_descending(client):
    post_txn(client, user_id="low_user", amount=10, idempotency_key="r-low")
    post_txn(client, user_id="high_user", amount=5000, idempotency_key="r-high")

    r = client.get("/ranking")
    assert r.status_code == 200
    rankings = r.json()["rankings"]
    assert rankings[0]["user_id"] == "high_user"
    assert rankings[0]["rank"] == 1
    assert rankings[1]["user_id"] == "low_user"
    assert rankings[1]["rank"] == 2


def test_ranking_pagination(client):
    for i in range(5):
        post_txn(client, user_id=f"page_user_{i}", amount=10 + i, idempotency_key=f"p-{i}")

    r = client.get("/ranking?limit=2&offset=0")
    body = r.json()
    assert body["total_users"] == 5
    assert len(body["rankings"]) == 2

    r2 = client.get("/ranking?limit=2&offset=4")
    assert len(r2.json()["rankings"]) == 1


def test_single_huge_transaction_does_not_dominate_score(client):
    post_txn(client, user_id="whale", amount=500_000, idempotency_key="w-1")
    post_txn(client, user_id="regular", amount=2_000, idempotency_key="reg-1")
    post_txn(client, user_id="regular", amount=2_000, idempotency_key="reg-2")

    ranking = client.get("/ranking").json()["rankings"]
    scores = {r["user_id"]: r["ranking_score"] for r in ranking}

    raw_amount_ratio = 500_000 / 4_000 
    score_ratio = scores["whale"] / scores["regular"]
    assert score_ratio < raw_amount_ratio / 5, (
        "Per-transaction cap and sqrt scaling should heavily compress the "
        "advantage a single huge transaction gets over modest, repeated ones."
    )
