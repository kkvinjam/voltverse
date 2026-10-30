"""Embedded VoltVerse store, so Sparky can run on its own (SPARKY_BACKEND=embedded).

Instead of re-implementing the store, this starts the REAL microservices (catalog, inventory, cart, checkout,
payment, orders) as local uvicorn processes on 127.0.0.1, backed by an in-memory DynamoDB emulator (moto).
Sparky's tools then call them exactly as they call the services on EKS, so prices, stock, carts, quotes, order
timelines, cancellations, returns and refunds behave identically. Nothing is persisted: every process start
(for example every Bedrock AgentCore Runtime session) gets a freshly seeded store.

The store is seeded with the 36-product catalog and a set of demo orders in every fulfilment state, so order
conversations ("where is my order?", "cancel it", "was I charged?", "I want to return it") have real data.
`demo_data()` lists those orders; the shopper simulator (scripts/sparky_shopper.py) uses it to pick a scenario.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path

import boto3
import httpx

log = logging.getLogger("assistant.embedded")

SERVICES = ["catalog", "inventory", "cart", "checkout", "payment", "orders"]
TABLES = {  # env var -> (table name, hash key, GSI attribute)
    "PRODUCTS_TABLE": ("embedded-products", "productId", None),
    "INVENTORY_TABLE": ("embedded-inventory", "pk", None),
    "CARTS_TABLE": ("embedded-carts", "cartId", None),
    "ORDERS_TABLE": ("embedded-orders", "orderId", None),
    "PAYMENTS_TABLE": ("embedded-payments", "paymentId", "orderId"),
}
REGION = "us-west-2"  # only used to talk to the local emulator
ADDRESS = {"name": "Demo Customer", "line1": "500 Demo Ave", "city": "Seattle", "state": "WA",
           "postalCode": "98101", "country": "US"}
CARD = {"number": "4242424242424242", "expMonth": 12, "expYear": 2035, "cvc": "123", "name": "Demo Customer"}

# Demo orders: (key, email, items, shipping, age in minutes, cancel?). The orders service derives the fulfilment
# status from the order age: CONFIRMED < 2 min, PROCESSING < 10, SHIPPED < 30, OUT_FOR_DELIVERY < 60, DELIVERED.
DEMO_ORDERS = [
    ("just_placed", "alex.rivera@example.com", [("vv-audio-002", 1)], "standard", 0, False),
    ("processing", "sam.chen@example.com", [("vv-acc-001", 2), ("vv-acc-003", 1)], "standard", 5, False),
    ("shipped", "jordan.lee@example.com", [("vv-laptop-002", 1)], "express", 8, False),
    ("out_for_delivery", "taylor.kim@example.com", [("vv-wear-001", 1)], "standard", 40, False),
    ("delivered_returnable", "alex.rivera@example.com", [("vv-cam-001", 1)], "standard", 3 * 24 * 60, False),
    ("delivered_window_closed", "sam.chen@example.com", [("vv-home-002", 1)], "standard", 45 * 24 * 60, False),
    ("cancelled_refunded", "jordan.lee@example.com", [("vv-game-001", 1)], "standard", 0, True),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _default(env: str, *candidates: Path) -> Path:
    """First existing path: $env, then the image layout (/srv/...), then the repository layout."""
    if os.getenv(env):
        return Path(os.environ[env])
    return next((p for p in candidates if p.exists()), candidates[0])


_HERE = Path(__file__).resolve()
# Repository root when running from a checkout (services/assistant/app/embedded.py); unused inside the image.
_REPO = _HERE.parents[min(3, len(_HERE.parents) - 1)]


class EmbeddedStore:
    def __init__(self) -> None:
        self.services_dir = _default("EMBEDDED_SERVICES_DIR", Path("/srv/services"), _REPO / "services")
        self.common_dir = _default("EMBEDDED_COMMON_DIR", Path("/srv"), _REPO / "services" / "common")
        self.products_file = _default("EMBEDDED_PRODUCTS_FILE", Path("/srv/data/products.json"),
                                      _REPO / "data" / "products.json")
        self.ready = threading.Event()
        self.error: str | None = None
        self.urls: dict[str, str] = {}
        self.orders: list[dict] = []
        self._procs: list[subprocess.Popen] = []
        self._moto = None
        self._lock = threading.Lock()
        self._started = False

    # ------------------------------------------------------------------ lifecycle
    def start_background(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._start, name="embedded-store", daemon=True).start()

    def wait(self, timeout: float = 90) -> None:
        if not self.ready.wait(timeout):
            raise RuntimeError("embedded store did not start in time")
        if self.error:
            raise RuntimeError(f"embedded store failed to start: {self.error}")

    def _start(self) -> None:
        started = time.perf_counter()
        for noisy in ("werkzeug", "httpx"):  # moto's request log and the seeding calls
            logging.getLogger(noisy).setLevel(logging.WARNING)
        try:
            from moto.server import ThreadedMotoServer  # imported here: only the standalone image ships moto

            moto_port = _free_port()
            self._moto = ThreadedMotoServer(ip_address="127.0.0.1", port=moto_port)
            self._moto.start()
            endpoint = f"http://127.0.0.1:{moto_port}"
            self._create_tables(endpoint)
            self._seed_catalog(endpoint)
            self._start_services(endpoint)
            self._seed_orders(endpoint)
            log.info("embedded store ready in %.1fs (%d demo orders)", time.perf_counter() - started, len(self.orders))
        except Exception as e:  # surfaced to callers through wait()
            self.error = f"{type(e).__name__}: {e}"
            log.exception("embedded store failed to start")
        finally:
            self.ready.set()

    def stop(self) -> None:
        for p in self._procs:
            p.terminate()
        for p in self._procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if self._moto:
            self._moto.stop()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _ddb(endpoint: str, kind: str = "client"):
        factory = boto3.client if kind == "client" else boto3.resource
        return factory("dynamodb", endpoint_url=endpoint, region_name=REGION,
                       aws_access_key_id="embedded", aws_secret_access_key="embedded")

    def _create_tables(self, endpoint: str) -> None:
        ddb = self._ddb(endpoint)
        for name, key, gsi in TABLES.values():
            attrs = [{"AttributeName": key, "AttributeType": "S"}]
            extra = {}
            if gsi:
                attrs.append({"AttributeName": gsi, "AttributeType": "S"})
                extra["GlobalSecondaryIndexes"] = [{
                    "IndexName": f"{gsi}-index", "KeySchema": [{"AttributeName": gsi, "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"}}]
            ddb.create_table(TableName=name, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST",
                             KeySchema=[{"AttributeName": key, "KeyType": "HASH"}], **extra)

    def _seed_catalog(self, endpoint: str) -> None:
        """Same records as scripts/seed_data.py."""
        products = json.loads(self.products_file.read_text(encoding="utf-8"), parse_float=Decimal)
        ddb = self._ddb(endpoint, "resource")
        with ddb.Table(TABLES["PRODUCTS_TABLE"][0]).batch_writer() as batch:
            for p in products:
                batch.put_item(Item={k: v for k, v in p.items() if k != "initialStock"})
        with ddb.Table(TABLES["INVENTORY_TABLE"][0]).batch_writer() as batch:
            for p in products:
                batch.put_item(Item={"pk": f"SKU#{p['productId']}", "available": p["initialStock"], "reserved": 0})

    def _child_env(self, endpoint: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OTEL_", "AWS_", "AGENT_OBSERVABILITY")) and k != "PYTHONPATH"}
        env.update({
            "PYTHONPATH": str(self.common_dir),
            # The services are an implementation detail of the standalone agent: Sparky's own spans already show
            # every tool call, so the embedded services don't export telemetry of their own.
            "OTEL_SDK_DISABLED": "true",
            "AWS_REGION": REGION,
            "AWS_ACCESS_KEY_ID": "embedded",  # the emulator accepts any credentials; never pass real ones
            "AWS_SECRET_ACCESS_KEY": "embedded",
            "DYNAMODB_ENDPOINT": endpoint,
            "LOG_LEVEL": os.getenv("EMBEDDED_LOG_LEVEL", "WARNING"),
            **{k: v[0] for k, v in TABLES.items()},
            **{f"{s.upper()}_URL": url for s, url in self.urls.items()},
        })
        return env

    def _start_services(self, endpoint: str) -> None:
        self.urls = {s: f"http://127.0.0.1:{_free_port()}" for s in SERVICES}
        env = self._child_env(endpoint)
        for svc, url in self.urls.items():
            port = url.rsplit(":", 1)[1]
            self._procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port,
                 "--log-level", "warning", "--no-access-log"],
                cwd=self.services_dir / svc, env=env))
        deadline = time.time() + 60
        for svc, url in self.urls.items():
            while True:
                try:
                    if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.time() > deadline:
                    raise RuntimeError(f"embedded {svc} did not become healthy")
                time.sleep(0.2)
        # Sparky's tools resolve sibling services through CATALOG_URL, CART_URL, ... (voltcommon.service_url).
        os.environ.update({f"{s.upper()}_URL": url for s, url in self.urls.items()})

    def _seed_orders(self, endpoint: str) -> None:
        """Place real orders through checkout, then backdate them so they land in each fulfilment state."""
        orders_table = self._ddb(endpoint, "resource").Table(TABLES["ORDERS_TABLE"][0])
        with httpx.Client(timeout=20) as client:
            for key, email, items, shipping, age_min, cancel in DEMO_ORDERS:
                cart_id = str(uuid.uuid4())
                for product_id, qty in items:
                    client.post(f"{self.urls['cart']}/carts/{cart_id}/items",
                                json={"productId": product_id, "quantity": qty}).raise_for_status()
                r = client.post(f"{self.urls['checkout']}/checkout", json={
                    "cartId": cart_id, "email": email, "shippingAddress": ADDRESS, "shippingMethod": shipping,
                    "card": CARD})
                r.raise_for_status()
                order = r.json()
                if age_min:
                    orders_table.update_item(
                        Key={"orderId": order["orderId"]}, UpdateExpression="SET createdAt = :c",
                        ExpressionAttributeValues={":c": int(time.time() - age_min * 60)})
                if cancel:
                    client.post(f"{self.urls['orders']}/orders/{order['orderId']}/cancel",
                                json={"email": email, "reason": "ordered the wrong one"}).raise_for_status()
                view = client.get(f"{self.urls['orders']}/orders/{order['orderId']}", params={"email": email}).json()
                self.orders.append({
                    "scenario": key, "orderId": order["orderId"], "email": email,
                    "status": view.get("fulfilmentStatus"), "items": [i["name"] for i in view.get("items", [])],
                    "total": order["totals"]["total"]})

    # ------------------------------------------------------------------ demo data
    def demo_data(self) -> dict:
        self.wait()
        return {"orders": self.orders}


STORE = EmbeddedStore()
