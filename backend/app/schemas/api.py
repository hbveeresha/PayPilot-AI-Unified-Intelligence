from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SimulationCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=120)
    customer_email: str = Field(min_length=5, max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    amount: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")
    description: str = Field(default="Simulated payment", min_length=2, max_length=240)
    outcome: Literal["approved", "failed", "pending"] = "approved"

    model_config = ConfigDict(extra="forbid")

    @field_validator("customer_name", "description", mode="before")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("A text value is required")
        cleaned = " ".join(value.split())
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("Control characters are not allowed")
        if len(cleaned) < 2:
            raise ValueError("At least two non-whitespace characters are required")
        return cleaned

    @field_validator("customer_email", mode="before")
    @classmethod
    def clean_email(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("A valid email address is required")
        return value.strip().lower()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, pattern=r"^chat_[a-f0-9]{18,32}$")

    model_config = ConfigDict(extra="forbid")

    @field_validator("message", mode="before")
    @classmethod
    def clean_message(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Message must be text")
        message = value.strip()
        if not message:
            raise ValueError("Message must contain non-whitespace text")
        return message


class TransactionSearch(BaseModel):
    search: str | None = Field(default=None, max_length=160)
    status: Literal["successful", "pending", "failed"] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")

    @field_validator("search", mode="before")
    @classmethod
    def clean_search(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("Search must be text")
        return value.strip() if value and value.strip() else None


class SettingsUpdate(BaseModel):
    default_currency: Literal["USD", "EUR", "GBP", "INR"]


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    order_id: str | None
    customer_name: str
    customer_email: str
    amount: float
    currency: str
    status: str
    payment_method: str
    description: str
    created_at: datetime
    risk_score: int = 0
    risk_level: str = "low"


class SimulationOut(BaseModel):
    simulated: bool = True
    transaction: TransactionOut
    risk: dict



class PayPalOrderCreate(BaseModel):
    amount: float = Field(gt=0, le=100000)
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")
    description: str = Field(default="PayPilot AI Sandbox payment", min_length=2, max_length=240)

    model_config = ConfigDict(extra="forbid")

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Description must be text")
        return " ".join(value.split())
