"""Spin up the whole VoltVerse backend locally for integration tests.

- DynamoDB is emulated by moto's server mode (no AWS account needed).
- Every microservice runs as a real uvicorn process bound to 127.0.0.1.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import boto3
import httpx
import pytest
from moto.server import ThreadedMotoServer

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ["catalog", "inventory", "cart", "checkout", "payment", "orders", "assistant"]
TABLES = {
    "PRODUCTS_TABLE": ("vv-test-products", "productId", None),
    "INVENTORY_TABLE": ("vv-test-inventory", "pk", None),
    "CARTS_TABLE": ("vv-test-carts", "cartId", None),
    "ORDERS_TABLE": ("vv-test-orders", "orderId", None),
    "PAYMENTS_TABLE": ("vv-test-payments", "paymentId", "orderId"),
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy")


def _create_tables(endpoint: str) -> None:
    ddb = boto3.client("dynamodb", endpoint_url=endpoint, region_name="us-west-2")
    for name, key, gsi in TABLES.values():
        attrs = [{"AttributeName": key, "AttributeType": "S"}]
        kwargs = {}
        if gsi:
            attrs.append({"AttributeName": gsi, "AttributeType": "S"})
            kwargs["GlobalSecondaryIndexes"] = [{
                "IndexName": f"{gsi}-index", "KeySchema": [{"AttributeName": gsi, "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}}]
        ddb.create_table(TableName=name, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": key, "KeyType": "HASH"}], **kwargs)


@pytest.fixture(scope="session")
def stack():
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    moto_port = _free_port()
    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=moto_port)
    moto.start()
    endpoint = f"http://127.0.0.1:{moto_port}"
    _create_tables(endpoint)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_data.py"),
                    "--products-table", TABLES["PRODUCTS_TABLE"][0],
                    "--inventory-table", TABLES["INVENTORY_TABLE"][0],
                    "--region", "us-west-2", "--endpoint-url", endpoint], check=True)

    ports = {s: _free_port() for s in SERVICES}
    env = {
        **os.environ,
        "AWS_REGION": "us-west-2",
        "DYNAMODB_ENDPOINT": endpoint,
        "PYTHONPATH": str(ROOT / "services" / "common"),
        "OTEL_SDK_DISABLED": "true",
        **{k: v[0] for k, v in TABLES.items()},
        **{f"{s.upper()}_URL": f"http://127.0.0.1:{p}" for s, p in ports.items()},
    }
    procs = []
    for svc, port in ports.items():
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
             "--log-level", "warning"],
            cwd=ROOT / "services" / svc, env=env))
    try:
        for svc, port in ports.items():
            _wait(f"http://127.0.0.1:{port}/health")
        # make sibling URLs visible to in-process tool tests
        os.environ.update({f"{s.upper()}_URL": f"http://127.0.0.1:{p}" for s, p in ports.items()})
        yield {s: f"http://127.0.0.1:{p}" for s, p in ports.items()} | {"dynamodb": endpoint}
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
        moto.stop()
