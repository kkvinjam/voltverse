"""Orders service: order records, status tracking, cancellations and returns.

Customers are guests, so every customer-facing read or mutation requires the order id AND the
email address used at checkout. Fulfilment progress is simulated from the order age so the
storefront and the AI assistant have realistic tracking data to show.
"""

from __future__ import annotations

import logging
import os
import random
import string
import time

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from fastapi import Query
from opentelemetry import metrics, trace
from pydantic import BaseModel, EmailStr, Field

from voltcommon import ServiceError, create_app, dynamodb_table, service_client, to_jsonable
from voltcommon.aws import to_decimal

SERVICE = "orders"
app = create_app(SERVICE)
log = logging.getLogger(SERVICE)
meter = metrics.get_meter("voltverse.orders")
orders_created = meter.create_counter("voltverse.orders.created", description="Orders created")
orders_cancelled = meter.create_counter("voltverse.orders.cancelled", description="Orders cancelled")
returns_requested = meter.create_counter("voltverse.orders.returns", description="Return requests")

# Minutes after placement at which each fulfilment stage is reached (standard shipping).
# ORDER_TIMELINE_SCALE < 1 speeds things up for live demos.
_SCALE = float(os.getenv("ORDER_TIMELINE_SCALE", "1") or 1)
_TIMELINE = [("PROCESSING", 2), ("SHIPPED", 10), ("OUT_FOR_DELIVERY", 30), ("DELIVERED", 60)]
CANCELLABLE = {"CONFIRMED", "PROCESSING"}
RETURN_WINDOW_DAYS = 30


class Line(BaseModel):
    productId: str
    name: str
    category: str = "accessories"
    price: float
    quantity: int = Field(ge=1)
    art: dict = {}


class Address(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postalCode: str = Field(min_length=2, max_length=20)
    country: str = Field("US", min_length=2, max_length=2)


class CreateOrder(BaseModel):
    orderId: str = Field(min_length=1, max_length=64)
    email: EmailStr
    items: list[Line] = Field(min_length=1)
    shippingAddress: Address
    shippingMethod: str = Field(pattern="^(standard|express)$")
    totals: dict
    paymentId: str
    cartId: str | None = None


class CustomerAction(BaseModel):
    email: EmailStr
    reason: str = Field("", max_length=300)


def _table():
    return dynamodb_table("ORDERS_TABLE")


def _tracking_number() -> str:
    return "VV1Z" + "".join(random.choices(string.ascii_uppercase + string.digits, k=12))


def _fulfilment(order: dict) -> dict:
    """Derive the live fulfilment status from the order age."""
    if order["status"] == "CANCELLED":
        return {"status": "CANCELLED", "history": order.get("history", [])}
    speed = 0.5 if order.get("shippingMethod") == "express" else 1.0
    age_min = (time.time() - order["createdAt"]) / 60
    status = "CONFIRMED"
    history = [{"status": "CONFIRMED", "at": order["createdAt"]}]
    for stage, minutes in _TIMELINE:
        threshold = minutes * speed * _SCALE
        if age_min >= threshold:
            status = stage
            history.append({"status": stage, "at": int(order["createdAt"] + threshold * 60)})
    eta_minutes = _TIMELINE[-1][1] * speed * _SCALE
    return {"status": status, "history": history, "estimatedDelivery": int(order["createdAt"] + eta_minutes * 60)}


def _public(order: dict) -> dict:
    order = to_jsonable(order)
    f = _fulfilment(order)
    order["fulfilmentStatus"] = f["status"]
    order["statusHistory"] = f["history"] + [h for h in order.get("history", []) if h["status"] not in
                                             {x["status"] for x in f["history"]}]
    if "estimatedDelivery" in f:
        order["estimatedDelivery"] = f["estimatedDelivery"]
    order["cancellable"] = order["status"] != "CANCELLED" and f["status"] in CANCELLABLE
    order.pop("history", None)
    order.pop("cartId", None)
    return order


def _load_for_customer(order_id: str, email: str) -> dict:
    span = trace.get_current_span()
    span.set_attribute("voltverse.order.id", order_id)
    item = _table().get_item(Key={"orderId": order_id.strip().upper()}).get("Item")
    # Same error for "not found" and "wrong email" so order ids cannot be probed.
    if not item or item["email"] != email.strip().lower():
        raise ServiceError(404, "order_not_found", "No order matches that order number and email")
    return item


@app.post("/orders", status_code=201)
def create_order(body: CreateOrder):
    """Internal: called by the checkout service once payment is authorized."""
    now = int(time.time())
    order = {
        **body.model_dump(),
        "email": body.email.lower(),
        "status": "CONFIRMED",
        "carrier": "VoltShip Express" if body.shippingMethod == "express" else "VoltShip Ground",
        "trackingNumber": _tracking_number(),
        "createdAt": now,
        "history": [],
    }
    try:
        _table().put_item(Item=to_decimal(order), ConditionExpression=Attr("orderId").not_exists())
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ServiceError(409, "order_exists", f"Order {body.orderId} already exists")
        raise
    orders_created.add(1, {"shipping_method": body.shippingMethod})
    log.info("order %s created (%d items)", body.orderId, len(body.items))
    return _public(order)


@app.get("/orders/{order_id}")
def get_order(order_id: str, email: str = Query(..., max_length=254)):
    return _public(_load_for_customer(order_id, email))


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, body: CustomerAction):
    order = _load_for_customer(order_id, body.email)
    view = _public(order)
    if order["status"] == "CANCELLED":
        return view
    if not view["cancellable"]:
        raise ServiceError(409, "not_cancellable",
                           f"Order is {view['fulfilmentStatus'].replace('_', ' ').lower()} and can no longer be cancelled. "
                           "You can request a return once it is delivered.")

    oid = order["orderId"]
    async with service_client("inventory") as inventory:
        r = await inventory.post(f"/reservations/{oid}/release")
        if r.status_code not in (200, 404):
            raise ServiceError(502, "inventory_release_failed", "Could not release reserved stock, please retry")
    async with service_client("payment") as payment:
        r = await payment.post(f"/payments/{order['paymentId']}/refund", json={"reason": body.reason or "order_cancelled"})
        if r.status_code != 200:
            raise ServiceError(502, "refund_failed", "Could not refund the payment, please retry")

    now = int(time.time())
    resp = _table().update_item(
        Key={"orderId": oid},
        UpdateExpression="SET #s = :c, cancelledAt = :now, cancelReason = :r, "
                         "history = list_append(if_not_exists(history, :empty), :h)",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "CANCELLED", ":now": now, ":r": body.reason or "customer_request",
                                   ":h": [{"status": "CANCELLED", "at": now}], ":empty": []},
        ReturnValues="ALL_NEW",
    )
    orders_cancelled.add(1)
    return _public(resp["Attributes"])


@app.post("/orders/{order_id}/return")
def request_return(order_id: str, body: CustomerAction):
    order = _load_for_customer(order_id, body.email)
    view = _public(order)
    if view["fulfilmentStatus"] != "DELIVERED":
        raise ServiceError(409, "not_returnable", "Returns can be requested once the order has been delivered")
    delivered_at = next(h["at"] for h in view["statusHistory"] if h["status"] == "DELIVERED")
    if time.time() - delivered_at > RETURN_WINDOW_DAYS * 86400:
        raise ServiceError(409, "return_window_closed", f"The {RETURN_WINDOW_DAYS}-day return window has closed")
    if order.get("returnRequest"):
        return view
    rma = "RMA-" + "".join(random.choices(string.digits, k=8))
    resp = _table().update_item(
        Key={"orderId": order["orderId"]},
        UpdateExpression="SET returnRequest = :rr",
        ExpressionAttributeValues={":rr": {"rma": rma, "reason": body.reason or "not specified",
                                           "requestedAt": int(time.time()), "status": "LABEL_SENT"}},
        ReturnValues="ALL_NEW",
    )
    returns_requested.add(1)
    return _public(resp["Attributes"])
