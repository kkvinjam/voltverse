"""Shared helpers for VoltVerse microservices.

Telemetry is provided by ADOT Python auto-instrumentation, configured entirely through standard
OTEL_* environment variables. On EKS it is injected by the Amazon CloudWatch Observability add-on
(CloudWatch Application Signals); elsewhere it runs under `opentelemetry-instrument`. This package
only uses the OpenTelemetry API to add business metrics, structured logging and a few conveniences.
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
