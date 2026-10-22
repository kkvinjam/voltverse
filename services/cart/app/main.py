"""Cart service: anonymous shopping carts keyed by a client-generated UUID."""

from __future__ import annotations

import time
import uuid

from fastapi import Path
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field

from voltcommon import ServiceError, create_app, dynamodb_table, service_client, to_jsonable
from voltcommon.aws import to_decimal

SERVICE = "cart"
app = create_app(SERVICE)
meter = metrics.get_meter("voltverse.cart")
cart_ops = meter.create_counter("voltverse.cart.operations", description="Cart mutations by operation")
CART_TTL_SECONDS = 30 * 24 * 3600
MAX_QTY = 10
CartId = Path(..., pattern=r"^[0-9a-fA-F-]{36}$")


class AddItem(BaseModel):
    productId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(1, ge=1, le=MAX_QTY)


class UpdateItem(BaseModel):
    quantity: int = Field(ge=0, le=MAX_QTY)


def _table():
    return dynamodb_table("CARTS_TABLE")


def _validate_cart_id(cart_id: str) -> None:
    try:
        uuid.UUID(cart_id)
    except ValueError:
        raise ServiceError(400, "invalid_cart_id", "Cart id must be a UUID")
    trace.get_current_span().set_attribute("voltverse.cart.id", cart_id)


def _view(cart_id: str, item: dict | None) -> dict:
    items = to_jsonable(item.get("items", {})) if item else {}
    lines = sorted(items.values(), key=lambda l: l.get("addedAt", 0))
    subtotal = round(sum(l["price"] * l["quantity"] for l in lines), 2)
    return {
        "cartId": cart_id,
        "items": lines,
        "itemCount": sum(l["quantity"] for l in lines),
        "subtotal": subtotal,
        "currency": "USD",
    }


def _save(cart_id: str, items: dict) -> dict:
    now = int(time.time())
    _table().put_item(Item={"cartId": cart_id, "items": to_decimal(items), "updatedAt": now,
                            "expiresAt": now + CART_TTL_SECONDS})
    return _view(cart_id, {"items": to_decimal(items)})


def _load(cart_id: str) -> dict:
    item = _table().get_item(Key={"cartId": cart_id}).get("Item")
    return to_jsonable(item.get("items", {})) if item else {}


@app.get("/carts/{cart_id}")
def get_cart(cart_id: str = CartId):
    _validate_cart_id(cart_id)
    return _view(cart_id, _table().get_item(Key={"cartId": cart_id}).get("Item"))


@app.post("/carts/{cart_id}/items")
async def add_item(body: AddItem, cart_id: str = CartId):
    _validate_cart_id(cart_id)
    async with service_client("catalog") as catalog:
        resp = await catalog.get(f"/products/{body.productId}")
    if resp.status_code == 404:
        raise ServiceError(404, "product_not_found", f"Product '{body.productId}' does not exist")
    resp.raise_for_status()
    product = resp.json()

    items = _load(cart_id)
    line = items.get(body.productId)
    new_qty = min((line["quantity"] if line else 0) + body.quantity, MAX_QTY)

    async with service_client("inventory") as inventory:
        stock = await inventory.get(f"/stock/{body.productId}")
    if stock.status_code == 200 and stock.json()["available"] < new_qty:
        raise ServiceError(409, "insufficient_stock",
                           f"Only {stock.json()['available']} units of {product['name']} are available")

    items[body.productId] = {
        "productId": product["productId"],
        "name": product["name"],
        "brand": product["brand"],
        "category": product["category"],
        "price": product["price"],
        "art": product.get("art", {}),
        "quantity": new_qty,
        "addedAt": line["addedAt"] if line else int(time.time() * 1000),
    }
    cart_ops.add(1, {"operation": "add"})
    return _save(cart_id, items)


@app.put("/carts/{cart_id}/items/{product_id}")
def update_item(product_id: str, body: UpdateItem, cart_id: str = CartId):
    _validate_cart_id(cart_id)
    items = _load(cart_id)
    if product_id not in items:
        raise ServiceError(404, "item_not_in_cart", f"'{product_id}' is not in the cart")
    if body.quantity == 0:
        items.pop(product_id)
        cart_ops.add(1, {"operation": "remove"})
    else:
        items[product_id]["quantity"] = body.quantity
        cart_ops.add(1, {"operation": "update"})
    return _save(cart_id, items)


@app.delete("/carts/{cart_id}/items/{product_id}")
def remove_item(product_id: str, cart_id: str = CartId):
    _validate_cart_id(cart_id)
    items = _load(cart_id)
    items.pop(product_id, None)
    cart_ops.add(1, {"operation": "remove"})
    return _save(cart_id, items)


@app.delete("/carts/{cart_id}")
def clear_cart(cart_id: str = CartId):
    _validate_cart_id(cart_id)
    _table().delete_item(Key={"cartId": cart_id})
    cart_ops.add(1, {"operation": "clear"})
    return _view(cart_id, None)
