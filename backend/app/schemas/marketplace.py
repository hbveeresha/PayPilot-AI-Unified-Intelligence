import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuthRegister(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=10, max_length=128)
    role: Literal["customer", "merchant"] = "customer"

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Name must be text")
        cleaned = " ".join(value.split())
        if len(cleaned) < 2 or any(ord(char) < 32 for char in cleaned):
            raise ValueError("Enter a valid name")
        return cleaned

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Enter a valid email address")
        normalized = value.strip().lower()
        if len(normalized) > 160 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class AuthLogin(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Enter a valid email address")
        normalized = value.strip().lower()
        if len(normalized) > 160 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class ProductSearch(BaseModel):
    search: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=96, pattern=r"^[a-z0-9-]+$")
    min_price: Decimal | None = Field(default=None, ge=0, le=100000)
    max_price: Decimal | None = Field(default=None, ge=0, le=100000)
    sort: Literal["featured", "price_asc", "price_desc", "rating", "newest"] = "featured"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=24, ge=1, le=60)

    model_config = ConfigDict(extra="forbid")

    @field_validator("search", mode="before")
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("Search must be text")
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("Search contains unsupported characters")
        return cleaned or None

    @model_validator(mode="after")
    def check_price_range(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("Minimum price cannot exceed maximum price")
        return self


class ProductCreate(BaseModel):
    name: str = Field(min_length=3, max_length=140)
    description: str = Field(min_length=12, max_length=2000)
    category_slug: str = Field(min_length=2, max_length=96, pattern=r"^[a-z0-9-]+$")
    price: Decimal = Field(gt=0, le=100000)
    stock_quantity: int = Field(ge=0, le=100000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Product text must be a string")
        cleaned = " ".join(value.split())
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("Control characters are not allowed")
        return cleaned


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=140)
    description: str | None = Field(default=None, min_length=12, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, le=100000)
    stock_quantity: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Product text must be a string")
        cleaned = " ".join(value.split())
        if any(ord(char) < 32 for char in cleaned):
            raise ValueError("Control characters are not allowed")
        return cleaned

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one product field must be supplied")
        return self


class CartItemAdd(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=99)

    model_config = ConfigDict(extra="forbid")


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)

    model_config = ConfigDict(extra="forbid")


class CheckoutRequest(BaseModel):
    """No price fields are accepted: the server calculates the cart total."""

    model_config = ConfigDict(extra="forbid")
