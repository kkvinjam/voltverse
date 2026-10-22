"""Shared helpers for VoltVerse microservices.

Telemetry is provided by OpenTelemetry auto-instrumentation (`opentelemetry-instrument`)
which is configured entirely through standard OTEL_* environment variables. This package
only adds business metrics, structured logging and a few conveniences on top.
"""

from .app import create_app, ServiceError
from .aws import dynamodb_table, to_jsonable
from .http import service_client, service_url

__all__ = [
    "create_app",
    "ServiceError",
    "dynamodb_table",
    "to_jsonable",
    "service_client",
    "service_url",
]
