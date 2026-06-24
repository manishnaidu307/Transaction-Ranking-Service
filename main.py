import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import DEFAULT_RANKING_PAGE_SIZE, MAX_RANKING_PAGE_SIZE
from db import init_db
from schemas import (
    RankingResponse,
    SummaryResponse,
    TransactionRequest,
    TransactionResponse,
)
from service import (
    IdempotencyConflict,
    RateLimitExceeded,
    UserNotFound,
    get_ranking,
    get_summary,
    process_transaction,
)

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Transaction Ranking Service",
    description=(
        "POST /transaction, GET /summary/{user_id}, GET /ranking -- with "
        "idempotency, rate limiting, atomic aggregate updates and a "
        "two-factor fairness-aware ranking score."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# Exception handlers -- map domain errors to clean, predictable HTTP responses



@app.exception_handler(IdempotencyConflict)
def _handle_idempotency_conflict(request: Request, exc: IdempotencyConflict):
    return JSONResponse(
        status_code=409,
        content={"error": "idempotency_conflict", "message": str(exc)},
    )


@app.exception_handler(RateLimitExceeded)
def _handle_rate_limit(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded", "message": str(exc)},
    )


@app.exception_handler(UserNotFound)
def _handle_user_not_found(request: Request, exc: UserNotFound):
    return JSONResponse(
        status_code=404,
        content={"error": "user_not_found", "message": str(exc)},
    )


@app.exception_handler(RequestValidationError)
def _handle_validation_error(request: Request, exc: RequestValidationError):
    messages = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"] if p != "body")
        messages.append(f"{loc}: {e['msg']}" if loc else e["msg"])
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "message": "; ".join(messages)},
    )


# API routes



@app.post("/transaction", response_model=TransactionResponse, status_code=201)
def create_transaction(payload: TransactionRequest):
    body, status_code, _is_duplicate = process_transaction(payload)
    return JSONResponse(content=body, status_code=status_code)


@app.get("/summary/{user_id}", response_model=SummaryResponse)
def read_summary(user_id: str):
    if not USER_ID_PATTERN.match(user_id):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "user_id may only contain letters, numbers, underscores and hyphens",
            },
        )
    body = get_summary(user_id)
    return JSONResponse(content=body, status_code=200)


@app.get("/ranking", response_model=RankingResponse)
def read_ranking(
    limit: int = Query(DEFAULT_RANKING_PAGE_SIZE, ge=1, le=MAX_RANKING_PAGE_SIZE),
    offset: int = Query(0, ge=0),
):
    body = get_ranking(limit=limit, offset=offset)
    return JSONResponse(content=body, status_code=200)


@app.get("/health")
def health():
    return {"status": "ok"}


# Static demo frontend


FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
