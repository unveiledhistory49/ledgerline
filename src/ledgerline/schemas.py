"""Pydantic request/response schemas (v1). Amounts are decimal strings in requests."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    type: str
    currency: str = Field(min_length=3, max_length=3)


class JournalLineIn(BaseModel):
    account_id: str
    debit: str = "0"
    credit: str = "0"
    memo: str = ""


class JournalCreate(BaseModel):
    currency: str
    description: str = ""
    reference: str = ""
    lines: list[JournalLineIn]


class TransferCreate(BaseModel):
    debit_account_id: str
    credit_account_id: str
    amount: str
    currency: str
    description: str = ""
    reference: str = ""


class FxRateCreate(BaseModel):
    base: str
    quote: str
    rate: str


class WebhookEndpointCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    secret: str = Field(min_length=16, max_length=128)


class PeriodCloseCreate(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
