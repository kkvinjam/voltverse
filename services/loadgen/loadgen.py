"""Synthetic shopper traffic so dashboards, traces and service maps always have data.

Runs in-cluster against the storefront gateway. It never calls the AI assistant (no Bedrock cost).
Tunables (env): TARGET_URL, SHOPPERS_PER_MINUTE (default 12), DECLINE_RATE (default 0.1).

It also plays the warehouse: every RESTOCK_INTERVAL_S (default 300) it tops up any product below
RESTOCK_BELOW units (default 20) to its initial stock (PRODUCTS_FILE, data/products.json), through the
inventory service's internal /restock API (INVENTORY_URL). Without it, continuous checkouts sell out the store.
"""

import logging
import os
import random
import re
import time
import uuid

import json

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loadgen")

TARGET = os.getenv("TARGET_URL", "http://storefront.voltverse").rstrip("/")
PER_MINUTE = float(os.getenv("SHOPPERS_PER_MINUTE", "12"))
DECLINE_RATE = float(os.getenv("DECLINE_RATE", "0.1"))
SEARCHES = ["headphones", "noise cancelling", "laptop", "4k", "drone", "gaming", "smart home", "watch", "camera", "usb-c"]
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory:8080").rstrip("/")
RESTOCK_INTERVAL_S = float(os.getenv("RESTOCK_INTERVAL_S", "300"))
RESTOCK_BELOW = int(os.getenv("RESTOCK_BELOW", "20"))
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", "/scripts/products.json")
CARDS_OK = ["4242424242424242"]
CARDS_BAD = ["4000000000000002", "4000000000009995", "4000000000000119"]


def page_view(client: httpx.Client) -> None:
    """Load the SPA shell and its JS/CSS bundles like a browser would (exercises the nginx gateway)."""
    html = client.get("/").text
    for asset in set(re.findall(r'(/assets/[^"\']+)', html)):
        client.get(asset)


def shopper(client: httpx.Client) -> None:
    cart = str(uuid.uuid4())
    page_view(client)
    cats = client.get("/api/catalog/categories").json()["categories"]
    browsed = client.get("/api/catalog/products", params={"category": random.choice(cats)["id"]}).json()["products"]
    if random.random() < 0.6:
        browsed += client.get("/api/catalog/products", params={"q": random.choice(SEARCHES)}).json()["products"]
    if not browsed:
        return
    for p in random.sample(browsed, k=min(len(browsed), random.randint(1, 3))):
        client.get(f"/api/catalog/products/{p['productId']}")
        client.get("/api/inventory/stock", params={"ids": p["productId"]})
    if random.random() > 0.45:
        return  # window shopper
    for p in random.sample(browsed, k=min(len(browsed), random.randint(1, 2))):
        client.post(f"/api/cart/carts/{cart}/items", json={"productId": p["productId"], "quantity": random.randint(1, 2)})
    method = random.choice(["standard", "standard", "express"])
    client.get("/api/checkout/quote", params={"cartId": cart, "shippingMethod": method})
    if random.random() > 0.55:
        return  # abandoned cart
    card = random.choice(CARDS_BAD) if random.random() < DECLINE_RATE else random.choice(CARDS_OK)
    email = f"shopper{random.randint(1, 9999)}@example.com"
    r = client.post("/api/checkout/checkout", json={
        "cartId": cart, "email": email, "shippingMethod": method,
        "shippingAddress": {"name": "Load Test", "line1": "1 Synthetic Way", "city": "Seattle", "state": "WA",
                            "postalCode": "98101", "country": "US"},
        "card": {"number": card, "expMonth": 12, "expYear": 2035, "cvc": "123", "name": "Load Test"}})
    log.info("checkout -> %s", r.status_code)
    if r.status_code == 201:
        order = r.json()
        client.get(f"/api/orders/orders/{order['orderId']}", params={"email": email})
        if random.random() < 0.1:
            client.post(f"/api/orders/orders/{order['orderId']}/cancel", json={"email": email, "reason": "changed mind"})


def initial_stock() -> dict[str, int]:
    try:
        with open(PRODUCTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("no initial stock levels (%s); restocking to 100", e)
        return {}
    products = data["products"] if isinstance(data, dict) else data
    return {p["productId"]: int(p["initialStock"]) for p in products}


def restock(client: httpx.Client, inventory: httpx.Client, targets: dict[str, int]) -> None:
    ids = list(targets) or [p["productId"] for p in client.get("/api/catalog/products", params={"limit": 100}).json()["products"]]
    for i in range(0, len(ids), 100):
        stock = client.get("/api/inventory/stock", params={"ids": ",".join(ids[i:i + 100])}).json()["stock"]
        for s in stock:
            target = targets.get(s["productId"], 100)
            if s["available"] < min(RESTOCK_BELOW, target):
                inventory.post("/restock", json={"productId": s["productId"], "quantity": target - s["available"]}).raise_for_status()
                log.info("restocked %s: %d -> %d", s["productId"], s["available"], target)


def main() -> None:
    log.info("generating ~%.0f shoppers/minute against %s", PER_MINUTE, TARGET)
    targets = initial_stock()
    next_restock = 0.0
    with httpx.Client(base_url=TARGET, timeout=15) as client, httpx.Client(base_url=INVENTORY_URL, timeout=15) as inventory:
        while True:
            start = time.time()
            if start >= next_restock:
                next_restock = start + RESTOCK_INTERVAL_S
                try:
                    restock(client, inventory, targets)
                except Exception as e:
                    log.warning("restock failed: %s", e)
            try:
                shopper(client)
            except Exception as e:  # keep going no matter what
                log.warning("shopper failed: %s", e)
            time.sleep(max(0.0, random.expovariate(PER_MINUTE / 60) - (time.time() - start)))


if __name__ == "__main__":
    main()
