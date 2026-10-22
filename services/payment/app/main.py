"""Payment service: a simulated card processor.

No real payment network is contacted and full card numbers are never stored - only brand
and last four digits. Test cards:
  4242 4242 4242 4242  -> approved
  4000 0000 0000 0002  -> declined (card_declined)
  4000 0000 0000 9995  -> declined (insufficient_funds)
  4000 0000 0000 0119  -> processor error (HTTP 502), useful for observability exercises
Any other Luhn-valid number with a future expiry is approved.

Fault injection for workshops: PAYMENT_FAILURE_RATE (0..1) and PAYMENT_LATENCY_MS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import random
import time
import uuid
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import Query
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field

from voltcommon import ServiceError, create_app, dynamodb_table, to_jsonable

SERVICE = "payment"
app = create_app(SERVICE)
log = logging.getLogger(SERVICE)
meter = metrics.get_meter("voltverse.payment")
auth_counter = meter.create_counter("voltverse.payment.authorizations", description="Authorizations by outcome")
amount_hist = meter.create_histogram("voltverse.payment.amount", unit="USD", description="Authorized amounts")
refund_counter = meter.create_counter("voltverse.payment.refunds", description="Refunds issued")

FAILURE_RATE = float(os.getenv("PAYMENT_FAILURE_RATE", "0") or 0)
LATENCY_MS = int(os.getenv("PAYMENT_LATENCY_MS", "0") or 0)
_DECLINES = {"4000000000000002": "card_declined", "4000000000009995": "insufficient_funds"}
_PROCESSOR_ERROR = "4000000000000119"


class Card(BaseModel):
    number: str = Field(min_length=12, max_length=23)
    expMonth: int = Field(ge=1, le=12)
    expYear: int = Field(ge=2000, le=2100)
    cvc: str = Field(pattern=r"^\d{3,4}$")
    name: str = Field(min_length=1, max_length=100)


class AuthorizeRequest(BaseModel):
    orderId: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, le=100000)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")
    card: Card


class RefundRequest(BaseModel):
    reason: str = Field("requested_by_customer", max_length=200)


def _table():
    return dynamodb_table("PAYMENTS_TABLE")


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number][::-1]
    total = sum(digits[0::2]) + sum(sum(divmod(2 * d, 10)) for d in digits[1::2])
    return total % 10 == 0


def _brand(number: str) -> str:
    if number.startswith("4"):
        return "visa"
    if number[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(number[:4]) <= 2720:
        return "mastercard"
    if number[:2] in {"34", "37"}:
        return "amex"
    if number.startswith("6"):
        return "discover"
    return "card"


def _public(p: dict) -> dict:
    return to_jsonable({k: v for k, v in p.items() if k != "ttl"})


@app.post("/payments/authorize", status_code=201)
async def authorize(req: AuthorizeRequest):
    span = trace.get_current_span()
    number = "".join(ch for ch in req.card.number if ch.isdigit())
    span.set_attribute("voltverse.order.id", req.orderId)
    span.set_attribute("voltverse.payment.amount", req.amount)

    # Simulated network round-trip to the card processor.
    await asyncio.sleep((LATENCY_MS or random.randint(40, 160)) / 1000)

    if not _luhn_ok(number):
        auth_counter.add(1, {"outcome": "invalid_card"})
        raise ServiceError(400, "invalid_card_number", "Card number is invalid")
    today = dt.date.today()
    if (req.card.expYear, req.card.expMonth) < (today.year, today.month):
        auth_counter.add(1, {"outcome": "expired_card"})
        raise ServiceError(402, "expired_card", "Card has expired")
    if number == _PROCESSOR_ERROR or (FAILURE_RATE and random.random() < FAILURE_RATE):
        auth_counter.add(1, {"outcome": "processor_error"})
        log.error("payment processor unavailable for order %s", req.orderId)
        raise ServiceError(502, "processor_unavailable", "Payment processor is temporarily unavailable")

    decline = _DECLINES.get(number)
    payment = {
        "paymentId": f"pay_{uuid.uuid4().hex[:20]}",
        "orderId": req.orderId,
        "amount": Decimal(str(round(req.amount, 2))),
        "currency": req.currency,
        "status": "DECLINED" if decline else "AUTHORIZED",
        "declineCode": decline,
        "card": {"brand": _brand(number), "last4": number[-4:], "expMonth": req.card.expMonth,
                 "expYear": req.card.expYear},
        "createdAt": int(time.time()),
        "refunds": [],
    }
    _table().put_item(Item={k: v for k, v in payment.items() if v is not None})
    span.set_attribute("voltverse.payment.status", payment["status"])
    auth_counter.add(1, {"outcome": decline or "approved", "brand": payment["card"]["brand"]})
    if decline:
        raise ServiceError(402, decline, "The card was declined" if decline == "card_declined" else "Insufficient funds")
    amount_hist.record(req.amount, {"currency": req.currency})
    return _public(payment)


def _get(payment_id: str) -> dict:
    item = _table().get_item(Key={"paymentId": payment_id}).get("Item")
    if not item:
        raise ServiceError(404, "payment_not_found", f"Payment {payment_id} not found")
    return item


@app.post("/payments/{payment_id}/capture")
def capture(payment_id: str):
    try:
        resp = _table().update_item(
            Key={"paymentId": payment_id},
            UpdateExpression="SET #s = :cap, capturedAt = :now",
            ConditionExpression=Attr("status").eq("AUTHORIZED"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":cap": "CAPTURED", ":now": int(time.time())},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            current = _get(payment_id)
            if current["status"] == "CAPTURED":
                return _public(current)
            raise ServiceError(409, "invalid_state", f"Payment is {current['status']}")
        raise
    return _public(resp["Attributes"])


@app.post("/payments/{payment_id}/refund")
def refund(payment_id: str, body: RefundRequest | None = None):
    current = _get(payment_id)
    if current["status"] == "REFUNDED":
        return _public(current)
    if current["status"] not in ("AUTHORIZED", "CAPTURED"):
        raise ServiceError(409, "invalid_state", f"Payment is {current['status']} and cannot be refunded")
    refund_entry = {"refundId": f"re_{uuid.uuid4().hex[:16]}", "amount": current["amount"],
                    "reason": (body.reason if body else "requested_by_customer"), "createdAt": int(time.time())}
    resp = _table().update_item(
        Key={"paymentId": payment_id},
        UpdateExpression="SET #s = :ref, refunds = list_append(if_not_exists(refunds, :empty), :r)",
        ConditionExpression=Attr("status").is_in(["AUTHORIZED", "CAPTURED"]),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":ref": "REFUNDED", ":r": [refund_entry], ":empty": []},
        ReturnValues="ALL_NEW",
    )
    refund_counter.add(1)
    return _public(resp["Attributes"])


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    return _public(_get(payment_id))


@app.get("/payments")
def find_by_order(order_id: str = Query(..., alias="orderId", max_length=64)):
    resp = _table().query(IndexName="orderId-index", KeyConditionExpression=Key("orderId").eq(order_id))
    return {"payments": [_public(i) for i in resp.get("Items", [])]}
