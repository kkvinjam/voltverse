"""Tools the Sparky support agent can use. Each tool calls one VoltVerse microservice.

Tools are built per request by `build_tools(cart_id)` so the shopper's cart id is bound in a
closure. The model never sees or chooses a cart id, so it cannot be steered into reading or
modifying somebody else's cart. Orders are protected by requiring order number + email.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from strands import tool

from voltcommon import service_url

POLICIES = {
    "shipping": "Standard shipping (3-5 business days) is $5.99 and free on orders of $50 or more. "
                "Express shipping (1-2 business days) is $14.99. We ship to the US only. "
                "Orders are processed within ~2 minutes and a tracking number is issued at checkout.",
    "returns": "Returns are accepted within 30 days of delivery for any reason. Items must include all accessories. "
               "Request a return with the order number and email; a prepaid label is emailed with an RMA number. "
               "Refunds are issued to the original payment method within 5 business days of receiving the item.",
    "cancellations": "Orders can be cancelled free of charge until they ship. Cancelled orders are refunded "
                     "immediately to the original payment method.",
    "warranty": "Every product carries the manufacturer warranty listed on its product page (12-36 months). "
                "VoltVerse Care+ extended protection can be added within 60 days of purchase by contacting support.",
    "price_match": "We match the price of major authorized US retailers within 14 days of purchase. "
                   "Marketplace sellers and clearance prices are excluded.",
    "payment": "We accept Visa, Mastercard, American Express and Discover. Card details are processed by our "
               "payment provider and never stored by VoltVerse; we only keep the brand and last four digits.",
    "contact": "Human support is available 24/7 at support@voltverse.example or 1-800-VOLTVERSE.",
}


def _call(service: str, method: str, path: str, **kwargs: Any) -> str:
    """Call a microservice and return a compact JSON string for the model."""
    try:
        with httpx.Client(base_url=service_url(service), timeout=8.0) as client:
            r = client.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        return json.dumps({"error": "service_unavailable", "service": service, "detail": str(e)[:200]})
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:500]}
    if r.status_code >= 400:
        return json.dumps({"error": body.get("error", f"http_{r.status_code}"),
                           "message": body.get("message", ""), "status": r.status_code})
    return json.dumps(body, default=str)


_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _bad_id(value: str) -> str | None:
    """Ids are interpolated into URL paths, so only allow a strict character set."""
    if _ID_RE.match(value.strip()):
        return None
    return json.dumps({"error": "invalid_id", "message": f"'{value[:40]}' is not a valid id"})


def _strip_art(payload: str) -> str:
    """Drop presentation-only fields to save tokens."""
    try:
        data = json.loads(payload)
    except ValueError:
        return payload

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in ("art", "addedAt")}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    return json.dumps(clean(data))


def build_tools(cart_id: str | None, events: list[str]) -> list:
    """Create the tool set for one chat request. `events` collects tool names used (for the UI)."""

    def used(name: str) -> None:
        events.append(name)

    @tool
    def search_products(query: str = "", category: str = "", max_price: float = 0, sort: str = "relevance") -> str:
        """Search the VoltVerse catalog.

        Args:
            query: Free-text keywords, e.g. "noise cancelling headphones". May be empty to browse a category.
            category: Optional category id: smartphones, laptops, audio, wearables, cameras, gaming,
                smart-home, tv-display, drones, accessories.
            max_price: Optional maximum price in USD. 0 means no limit.
            sort: One of relevance, price_asc, price_desc, rating.
        """
        used("search_products")
        params: dict[str, Any] = {"limit": 8, "sort": sort if sort in {"relevance", "price_asc", "price_desc", "rating"} else "relevance"}
        if query:
            params["q"] = query[:100]
        if category:
            params["category"] = category
        if max_price and max_price > 0:
            params["maxPrice"] = max_price
        return _strip_art(_call("catalog", "GET", "/products", params=params))

    @tool
    def get_product_details(product_id: str) -> str:
        """Get full details, specifications, price and warranty for one product.

        Args:
            product_id: The product id, e.g. "vv-phone-001".
        """
        used("get_product_details")
        if err := _bad_id(product_id):
            return err
        return _strip_art(_call("catalog", "GET", f"/products/{product_id}"))

    @tool
    def list_categories() -> str:
        """List all product categories with the number of products in each."""
        used("list_categories")
        return _call("catalog", "GET", "/categories")

    @tool
    def check_stock(product_ids: list[str]) -> str:
        """Check live inventory availability for one or more products.

        Args:
            product_ids: List of product ids, e.g. ["vv-phone-001", "vv-audio-002"].
        """
        used("check_stock")
        product_ids = [p for p in product_ids if not _bad_id(p)]
        return _call("inventory", "GET", "/stock", params={"ids": ",".join(product_ids[:20])})

    @tool
    def view_cart() -> str:
        """Show the items currently in the shopper's cart and the subtotal."""
        used("view_cart")
        if not cart_id:
            return json.dumps({"error": "no_cart", "message": "The shopper has no cart yet."})
        return _strip_art(_call("cart", "GET", f"/carts/{cart_id}"))

    @tool
    def add_to_cart(product_id: str, quantity: int = 1) -> str:
        """Add a product to the shopper's cart. Only do this when the shopper asked for it.

        Args:
            product_id: The product id to add.
            quantity: How many units to add (1-10).
        """
        used("add_to_cart")
        if err := _bad_id(product_id):
            return err
        if not cart_id:
            return json.dumps({"error": "no_cart", "message": "The shopper has no cart session."})
        return _strip_art(_call("cart", "POST", f"/carts/{cart_id}/items",
                                json={"productId": product_id, "quantity": max(1, min(quantity, 10))}))

    @tool
    def update_cart_item(product_id: str, quantity: int) -> str:
        """Change the quantity of an item already in the cart. Quantity 0 removes it.

        Args:
            product_id: The product id in the cart.
            quantity: New quantity (0-10).
        """
        used("update_cart_item")
        if err := _bad_id(product_id):
            return err
        if not cart_id:
            return json.dumps({"error": "no_cart", "message": "The shopper has no cart session."})
        return _strip_art(_call("cart", "PUT", f"/carts/{cart_id}/items/{product_id}",
                                json={"quantity": max(0, min(quantity, 10))}))

    @tool
    def get_checkout_quote(shipping_method: str = "standard") -> str:
        """Calculate the order total for the current cart: subtotal, shipping, tax and total.

        Args:
            shipping_method: "standard" or "express".
        """
        used("get_checkout_quote")
        if not cart_id:
            return json.dumps({"error": "no_cart", "message": "The shopper has no cart yet."})
        method = shipping_method if shipping_method in ("standard", "express") else "standard"
        return _strip_art(_call("checkout", "GET", "/quote", params={"cartId": cart_id, "shippingMethod": method}))

    @tool
    def get_order_status(order_id: str, email: str) -> str:
        """Look up an order's status, items, tracking number and delivery estimate.
        Requires both the order number and the email address used at checkout.

        Args:
            order_id: Order number, e.g. "VV-7K2M9QX4".
            email: Email address used when placing the order.
        """
        used("get_order_status")
        if err := _bad_id(order_id):
            return err
        return _strip_art(_call("orders", "GET", f"/orders/{order_id.strip()}", params={"email": email}))

    @tool
    def cancel_order(order_id: str, email: str, reason: str = "") -> str:
        """Cancel an order that has not shipped yet and refund it. Always confirm with the shopper first.

        Args:
            order_id: Order number.
            email: Email address used when placing the order.
            reason: Short reason given by the shopper.
        """
        used("cancel_order")
        if err := _bad_id(order_id):
            return err
        return _strip_art(_call("orders", "POST", f"/orders/{order_id.strip()}/cancel",
                                json={"email": email, "reason": reason[:300]}))

    @tool
    def request_return(order_id: str, email: str, reason: str = "") -> str:
        """Start a return for a delivered order (within 30 days). Always confirm with the shopper first.

        Args:
            order_id: Order number.
            email: Email address used when placing the order.
            reason: Why the shopper is returning the item.
        """
        used("request_return")
        if err := _bad_id(order_id):
            return err
        return _strip_art(_call("orders", "POST", f"/orders/{order_id.strip()}/return",
                                json={"email": email, "reason": reason[:300]}))

    @tool
    def get_payment_status(order_id: str, email: str) -> str:
        """Get the payment and refund status for an order (card brand, last 4 digits, amount, refunds).

        Args:
            order_id: Order number.
            email: Email address used when placing the order.
        """
        used("get_payment_status")
        if err := _bad_id(order_id):
            return err
        order_json = _call("orders", "GET", f"/orders/{order_id.strip()}", params={"email": email})
        order = json.loads(order_json)
        if "error" in order:
            return order_json
        return _call("payment", "GET", f"/payments/{order['paymentId']}")

    @tool
    def get_store_policies(topic: str) -> str:
        """Get VoltVerse store policies.

        Args:
            topic: One of shipping, returns, cancellations, warranty, price_match, payment, contact, or "all".
        """
        used("get_store_policies")
        if topic in POLICIES:
            return json.dumps({topic: POLICIES[topic]})
        return json.dumps(POLICIES)

    return [search_products, get_product_details, list_categories, check_stock, view_cart, add_to_cart,
            update_cart_item, get_checkout_quote, get_order_status, cancel_order, request_return,
            get_payment_status, get_store_policies]
