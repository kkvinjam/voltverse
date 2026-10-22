"""FastAPI application factory shared by every service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace


class ServiceError(Exception):
    """Raise to return a structured JSON error with an HTTP status code."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, enriched with the active trace/span ids."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _configure_logging(service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(service))
    root = logging.getLogger()
    # Keep any OTLP log handler installed by auto-instrumentation; replace plain stream handlers.
    for h in list(root.handlers):
        if type(h) is logging.StreamHandler:
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app(service: str, version: str = "1.0.0") -> FastAPI:
    """Create a FastAPI app with health checks, JSON errors and optional chaos injection.

    Chaos injection (for observability workshops) is controlled by environment variables:
      CHAOS_LATENCY_MS   - extra latency added to every non-health request
      CHAOS_ERROR_RATE   - probability (0..1) of returning HTTP 500
    """
    _configure_logging(service)
    log = logging.getLogger(service)
    app = FastAPI(title=f"VoltVerse {service}", version=version, docs_url="/docs", redoc_url=None)

    latency_ms = int(os.getenv("CHAOS_LATENCY_MS", "0") or 0)
    error_rate = float(os.getenv("CHAOS_ERROR_RATE", "0") or 0)

    @app.middleware("http")
    async def _chaos_and_timing(request: Request, call_next):
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)
        start = time.perf_counter()
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000)
        if error_rate > 0 and random.random() < error_rate:
            trace.get_current_span().set_attribute("voltverse.chaos.injected", True)
            log.error("chaos: injected failure for %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"error": "chaos_injected", "message": "Injected failure"})
        response = await call_next(request)
        response.headers["x-service"] = service
        response.headers["x-response-time-ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        return response

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        span = trace.get_current_span()
        span.set_attribute("voltverse.error.code", exc.code)
        if exc.status_code >= 500:
            log.error("%s: %s", exc.code, exc.message)
        else:
            log.info("%s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        trace.get_current_span().record_exception(exc)
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": "Internal server error"})

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "service": service, "version": version}

    @app.get("/ready", include_in_schema=False)
    async def ready():
        return {"status": "ready"}

    return app
