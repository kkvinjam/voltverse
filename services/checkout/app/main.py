"""Checkout service: orchestrates cart -> pricing -> inventory -> payment -> order (a simple saga).

Every step is a separate downstream call, which makes the checkout trace the most interesting
one to explore in X-Ray / any OpenTelemetry backend. Failures trigger compensating actions
(release reservation, refund payment) so stock and money never leak.
"""

from __future__ import annotations

import logging
import random
import string

import httpx
from fastapi import Query
from opentelemetry import metrics, trace
from pydantic import BaseModel, EmailStr, Field

from voltcommon import ServiceError, create_app, service_client

SERVICE = "checkout"
app = create_app(SERVICE)
log = logging.getLogger(SERVICE)
tracer = trace.get_tracer("voltverse.checkout")
meter = metrics.get_meter("voltverse.checkout")
checkout_counter = meter.create_counter("voltverse.checkout.attempts", description="Checkout attempts by outcome")
revenue_counter = meter.create_counter("voltverse.checkout.revenue", unit="USD", description="Revenue from completed orders")
order_value = meter.create_histogram("voltverse.checkout.order_value", unit="USD", description="Order value distribution")

TAX_RATE = 0.08
FREE_SHIPPING_THRESHOLD = 50.0
SHIPPING_RATES = {"standard": 5.99, "express": 14.99}


class Address(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postalCode: str = Field(min_length=2, max_length=20)
    country: str = Field("US", min_length=2, max_length=2)


class Card(BaseModel):
    number: str = Field(min_length=12, max_length=23)
    expMonth: int = Field(ge=1, le=12)
    expYear: int = Field(ge=2000, le=2100)
    cvc: str = Field(pattern=r"^\d{3,4}$")
    name: str = Field(min_length=1, max_length=100)


class CheckoutRequest(BaseModel):
    cartId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    email: EmailStr
    shippingAddress: Address
    shippingMethod: str = Field("standard", pattern="^(standard|express)$")
    card: Card


def _shipping_cost(method: str, subtotal: float) -> float:
    if method == "standard" and subtotal >= FREE_SHIPPING_THRESHOLD:
        return 0.0
    return SHIPPING_RATES[method]


def compute_totals(lines: list[dict], method: str) -> dict:
    subtotal = round(sum(l["price"] * l["quantity"] for l in lines), 2)
    shipping = _shipping_cost(method, subtotal)
    tax = round(subtotal * TAX_RATE, 2)
    return {"subtotal": subtotal, "shipping": shipping, "tax": tax,
            "total": round(subtotal + shipping + tax, 2), "currency": "USD"}


async def _priced_lines(cart_id: str) -> list[dict]:
    """Load the cart and re-price every line from the catalog (never trust cart prices)."""
    with tracer.start_as_current_span("checkout.load_and_price_cart"):
        async with service_client("cart") as cart:
            r = await cart.get(f"/carts/{cart_id}")
        r.raise_for_status()
        items = r.json()["items"]
        if not items:
            raise ServiceError(400, "empty_cart", "Your cart is empty")
        lines = []
        async with service_client("catalog") as catalog:
            for item in items:
                p = await catalog.get(f"/products/{item['productId']}")
                if p.status_code == 404:
                    raise ServiceError(409, "product_unavailable", f"{item['name']} is no longer available")
                p.raise_for_status()
                product = p.json()
                lines.append({"productId": product["productId"], "name": product["name"], "category": product["category"],
                              "price": product["price"],
                              "quantity": item["quantity"], "art": product.get("art", {})})
        return lines


@app.get("/quote")
async def quote(cart_id: str = Query(..., alias="cartId", pattern=r"^[0-9a-fA-F-]{36}$"),
                shipping_method: str = Query("standard", alias="shippingMethod", pattern="^(standard|express)$")):
    lines = await _priced_lines(cart_id)
    return {"items": lines, "shippingMethod": shipping_method, **compute_totals(lines, shipping_method),
            "shippingOptions": [
                {"id": "standard", "name": "Standard (3-5 days)", "price": SHIPPING_RATES["standard"],
                 "freeOver": FREE_SHIPPING_THRESHOLD},
                {"id": "express", "name": "Express (1-2 days)", "price": SHIPPING_RATES["express"]},
            ]}


def _order_id() -> str:
    return "VV-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _fail(reason: str, status: int, code: str, message: str) -> ServiceError:
    checkout_counter.add(1, {"outcome": reason})
    trace.get_current_span().set_attribute("voltverse.checkout.outcome", reason)
    return ServiceError(status, code, message)


@app.post("/checkout", status_code=201)
async def checkout(req: CheckoutRequest):
    span = trace.get_current_span()
    lines = await _priced_lines(req.cartId)
    totals = compute_totals(lines, req.shippingMethod)
    order_id = _order_id()
    span.set_attribute("voltverse.order.id", order_id)
    span.set_attribute("voltverse.order.total", totals["total"])
    span.set_attribute("voltverse.order.items", sum(l["quantity"] for l in lines))

    async with service_client("inventory") as inventory, service_client("payment", timeout=10) as payment, \
            service_client("orders") as orders, service_client("cart") as cart:
        # 1. Reserve stock
        with tracer.start_as_current_span("checkout.reserve_inventory"):
            r = await inventory.post("/reservations", json={
                "orderId": order_id, "items": [{"productId": l["productId"], "quantity": l["quantity"]} for l in lines]})
        if r.status_code == 409:
            raise _fail("out_of_stock", 409, "insufficient_stock", r.json().get("message", "Some items are out of stock"))
        r.raise_for_status()

        # 2. Authorize payment (compensate: release stock)
        with tracer.start_as_current_span("checkout.authorize_payment"):
            try:
                p = await payment.post("/payments/authorize", json={
                    "orderId": order_id, "amount": totals["total"], "currency": "USD", "card": req.card.model_dump()})
            except httpx.HTTPError:
                p = None
        if p is None or p.status_code != 201:
            await inventory.post(f"/reservations/{order_id}/release")
            if p is not None and p.status_code in (400, 402):
                body = p.json()
                raise _fail("payment_declined", 402, body.get("error", "payment_declined"),
                            body.get("message", "Payment was declined"))
            log.error("payment failed for %s: %s", order_id, p.status_code if p is not None else "timeout")
            raise _fail("payment_error", 502, "payment_unavailable",
                        "We couldn't reach the payment processor. You have not been charged - please try again.")
        payment_id = p.json()["paymentId"]

        # 3. Record the order (compensate: refund + release)
        with tracer.start_as_current_span("checkout.create_order"):
            o = await orders.post("/orders", json={
                "orderId": order_id, "email": req.email, "items": lines,
                "shippingAddress": req.shippingAddress.model_dump(), "shippingMethod": req.shippingMethod,
                "totals": totals, "paymentId": payment_id, "cartId": req.cartId})
        if o.status_code != 201:
            await payment.post(f"/payments/{payment_id}/refund", json={"reason": "order_creation_failed"})
            await inventory.post(f"/reservations/{order_id}/release")
            raise _fail("order_error", 502, "order_failed", "We couldn't create your order. You have not been charged.")

        # 4. Finalize: capture funds, commit stock, empty the cart. Failures here are logged, not fatal.
        for name, call in (("capture", payment.post(f"/payments/{payment_id}/capture")),
                           ("commit", inventory.post(f"/reservations/{order_id}/commit")),
                           ("clear_cart", cart.delete(f"/carts/{req.cartId}"))):
            try:
                res = await call
                if res.status_code >= 400:
                    log.warning("post-order step %s failed for %s: %s", name, order_id, res.status_code)
            except httpx.HTTPError:
                log.warning("post-order step %s failed for %s", name, order_id, exc_info=True)

    checkout_counter.add(1, {"outcome": "success", "shipping_method": req.shippingMethod})
    revenue_counter.add(totals["total"])
    order_value.record(totals["total"])
    span.set_attribute("voltverse.checkout.outcome", "success")
    log.info("order %s placed, total %.2f", order_id, totals["total"])
    return o.json()
