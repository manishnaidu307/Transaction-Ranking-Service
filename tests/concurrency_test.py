import concurrent.futures
import time
import uuid

import httpx

BASE_URL = "http://127.0.0.1:8000"
USER_ID = "concurrency_stress_user"
NUM_REQUESTS = 40
AMOUNT_PER_REQUEST = 7.5


def fire_one(i: int):
    key = f"stress-{uuid.uuid4()}"
    with httpx.Client(timeout=10) as client:
        r = client.post(
            f"{BASE_URL}/transaction",
            json={
                "user_id": USER_ID,
                "amount": AMOUNT_PER_REQUEST,
                "idempotency_key": key,
            },
        )
        return r.status_code


def main():
    print(f"Firing {NUM_REQUESTS} concurrent transactions for '{USER_ID}'...")
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_REQUESTS) as pool:
        statuses = list(pool.map(fire_one, range(NUM_REQUESTS)))
    elapsed = time.time() - start

    accepted = statuses.count(201)
    rate_limited = statuses.count(429)
    other = len(statuses) - accepted - rate_limited

    print(f"Completed in {elapsed:.2f}s")
    print(f"  201 Created       : {accepted}")
    print(f"  429 Rate limited  : {rate_limited}")
    print(f"  other status codes: {other} ({[s for s in statuses if s not in (201, 429)]})")

    expected_total = accepted * AMOUNT_PER_REQUEST

    with httpx.Client(timeout=10) as client:
        r = client.get(f"{BASE_URL}/summary/{USER_ID}")
        r.raise_for_status()
        summary = r.json()

    print(f"\nServer-reported total_amount     : {summary['total_amount']}")
    print(f"Expected total (accepted * amt)  : {expected_total}")
    print(f"Server-reported transaction_count: {summary['transaction_count']}")
    print(f"Expected transaction_count       : {accepted}")

    assert abs(summary["total_amount"] - expected_total) < 1e-6, "MISMATCH: lost or duplicated update detected!"
    assert summary["transaction_count"] == accepted, "MISMATCH: transaction_count is wrong!"
    print("\nPASS: no lost updates, no double counting under concurrency.")


if __name__ == "__main__":
    main()
