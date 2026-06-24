
import math
import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from config import (
    MAX_DESCRIPTION_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_TRANSACTION_AMOUNT,
    MAX_USER_ID_LENGTH,
    MIN_TRANSACTION_AMOUNT,
)

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class TransactionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=MAX_USER_ID_LENGTH)
    amount: float
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION_LENGTH)
    idempotency_key: str = Field(..., min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        v = v.strip()
        if not USER_ID_PATTERN.match(v):
            raise ValueError(
                "user_id may only contain letters, numbers, underscores and hyphens"
            )
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if isinstance(v, bool):  
            raise ValueError("amount must be a number")
        if math.isnan(v) or math.isinf(v):
            raise ValueError("amount must be a finite number")
        if v < MIN_TRANSACTION_AMOUNT:
            raise ValueError(f"amount must be at least {MIN_TRANSACTION_AMOUNT}")
        if v > MAX_TRANSACTION_AMOUNT:
            raise ValueError(f"amount may not exceed {MAX_TRANSACTION_AMOUNT}")
        return round(v, 2)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("idempotency_key cannot be blank")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v == "":
                return None
        return v


class UserSummaryInline(BaseModel):
    total_amount: float
    transaction_count: int
    ranking_score: float


class TransactionResponse(BaseModel):
    transaction_id: int
    user_id: str
    amount: float
    description: Optional[str]
    created_at: str
    duplicate: bool
    user_summary: UserSummaryInline


class SummaryResponse(BaseModel):
    user_id: str
    total_amount: float
    transaction_count: int
    average_transaction_amount: float
    active_days_count: int
    first_transaction_at: Optional[str]
    last_transaction_at: Optional[str]
    ranking_score: float
    rank: int
    total_ranked_users: int


class RankingEntry(BaseModel):
    rank: int
    user_id: str
    ranking_score: float
    total_amount: float
    transaction_count: int
    active_days_count: int


class RankingResponse(BaseModel):
    total_users: int
    limit: int
    offset: int
    rankings: List[RankingEntry]


class ErrorResponse(BaseModel):
    error: str
    message: str
