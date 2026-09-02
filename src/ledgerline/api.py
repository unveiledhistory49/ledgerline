"""FastAPI application factory. Routes live in ledgerline.routers; shared deps in deps."""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ledgerline.deps import get_db  # re-exported so tests can override via api_mod.get_db
from ledgerline.ledger import (
    AccountLockedError,
    AlreadyReversedError,
    LedgerError,
    PeriodClosedError,
    UnbalancedJournalError,
)
from ledgerline.money import MoneyError
from ledgerline.routers import accounts, admin, journals, reports

__all__ = ["create_app", "get_db", "app"]


def create_app() -> FastAPI:
    app = FastAPI(title="LedgerLine", version="1.0.0")

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id", str(uuid.uuid4()))
        start = time.perf_counter()
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        resp.headers["X-Elapsed-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        return resp

    @app.exception_handler(LedgerError)
    async def ledger_err(_: Request, exc: LedgerError):  # type: ignore[no-untyped-def]
        if isinstance(exc, UnbalancedJournalError):
            code = 422
        elif isinstance(exc, (PeriodClosedError, AlreadyReversedError, AccountLockedError)):
            code = 409
        else:
            code = 422
        return JSONResponse(
            status_code=code, content={"error": str(exc), "type": type(exc).__name__}
        )

    @app.exception_handler(MoneyError)
    async def money_err(_: Request, exc: MoneyError):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=422, content={"error": str(exc), "type": "MoneyError"})

    @app.get("/health")
    def health():  # type: ignore[no-untyped-def]
        return {"ok": True, "version": "1.0.0", "time": datetime.utcnow().isoformat()}

    app.include_router(accounts.router)
    app.include_router(journals.router)
    app.include_router(reports.router)
    app.include_router(admin.router)
    return app


app = create_app()
