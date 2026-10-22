"""DynamoDB helpers."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config


@lru_cache(maxsize=None)
def _resource():
    kwargs: dict[str, Any] = {
        "config": Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=2, read_timeout=5),
    }
    endpoint = os.getenv("DYNAMODB_ENDPOINT")  # set for local development (DynamoDB Local)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-west-2"), **kwargs)


def dynamodb_table(env_var: str):
    """Return a DynamoDB Table resource whose name is read from `env_var`."""
    name = os.environ.get(env_var)
    if not name:
        raise RuntimeError(f"Environment variable {env_var} must be set to a DynamoDB table name")
    return _resource().Table(name)


def to_jsonable(value: Any) -> Any:
    """Convert DynamoDB Decimals (recursively) into int/float for JSON responses."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def to_decimal(value: Any) -> Any:
    """Convert floats (recursively) into Decimals for DynamoDB writes."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_decimal(v) for v in value]
    return value
