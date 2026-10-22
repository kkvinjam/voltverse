"""HTTP client for service-to-service calls.

httpx is auto-instrumented by OpenTelemetry, so W3C trace context is propagated on every call
and each downstream request shows up as a client span in the distributed trace.
"""

from __future__ import annotations

import os

import httpx

_DEFAULTS = {
    "catalog": "http://catalog:8080",
    "inventory": "http://inventory:8080",
    "cart": "http://cart:8080",
    "checkout": "http://checkout:8080",
    "payment": "http://payment:8080",
    "orders": "http://orders:8080",
}


def service_url(name: str) -> str:
    """Base URL of a sibling service. Override with e.g. CATALOG_URL=http://localhost:8081."""
    return os.getenv(f"{name.upper()}_URL", _DEFAULTS[name]).rstrip("/")


def service_client(name: str, timeout: float = 5.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=service_url(name), timeout=timeout)
