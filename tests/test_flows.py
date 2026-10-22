"""End-to-end flows across the microservices, plus the agent tools (without calling Bedrock)."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "assistant")]

ADDRESS = {"name": "Ada Lovelace", "line1": "1 Analytical Way", "city": "Seattle", "state": "WA",
           "postalCode": "98101", "country": "US"}
GOOD_CARD = {"number": "4242 4242 4242 4242", "expMonth": 12, "expYear": 2035, "cvc": "123", "name": "Ada Lovelace"}


def _stock(stack, pid):
    return httpx.get(f"{stack['inventory']}/stock/{pid}").json()["available"]


def test_catalog(stack):
    cats = httpx.get(f"{stack['catalog']}/categories").json()["categories"]
    assert sum(c["productCount"] for c in cats) == 36
    res = httpx.get(f"{stack['catalog']}/products", params={"q": "noise cancelling"}).json()
    assert res["products"][0]["productId"] in {"vv-audio-001", "vv-audio-002"}
    assert httpx.get(f"{stack['catalog']}/products/nope").status_code == 404
    cheap = httpx.get(f"{stack['catalog']}/products", params={"maxPrice": 100, "sort": "price_asc"}).json()
    prices = [p["price"] for p in cheap["products"]]
    assert prices == sorted(prices) and max(prices) <= 100


def test_cart_and_checkout_happy_path(stack):
    cart_id = str(uuid.uuid4())
    before = _stock(stack, "vv-audio-002")
    r = httpx.post(f"{stack['cart']}/carts/{cart_id}/items", json={"productId": "vv-audio-002", "quantity": 2})
    assert r.status_code == 200, r.text
    assert r.json()["itemCount"] == 2

    quote = httpx.get(f"{stack['checkout']}/quote", params={"cartId": cart_id}).json()
    assert quote["subtotal"] == 398.0 and quote["shipping"] == 0 and quote["tax"] == 31.84

    r = httpx.post(f"{stack['checkout']}/checkout", timeout=20, json={
        "cartId": cart_id, "email": "Ada@Example.com", "shippingAddress": ADDRESS,
        "shippingMethod": "express", "card": GOOD_CARD})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["orderId"].startswith("VV-") and order["totals"]["shipping"] == 14.99
    assert _stock(stack, "vv-audio-002") == before - 2
    assert httpx.get(f"{stack['cart']}/carts/{cart_id}").json()["itemCount"] == 0

    # email is required and case-insensitive
    assert httpx.get(f"{stack['orders']}/orders/{order['orderId']}", params={"email": "x@y.com"}).status_code == 404
    got = httpx.get(f"{stack['orders']}/orders/{order['orderId']}", params={"email": "ada@example.com"}).json()
    assert got["cancellable"] is True
    pay = httpx.get(f"{stack['payment']}/payments/{got['paymentId']}").json()
    assert pay["status"] == "CAPTURED" and pay["card"]["last4"] == "4242" and "number" not in pay["card"]

    # cancel -> stock returned, payment refunded
    r = httpx.post(f"{stack['orders']}/orders/{order['orderId']}/cancel", json={"email": "ada@example.com"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"
    assert _stock(stack, "vv-audio-002") == before
    assert httpx.get(f"{stack['payment']}/payments/{got['paymentId']}").json()["status"] == "REFUNDED"
    by_order = httpx.get(f"{stack['payment']}/payments", params={"orderId": order["orderId"]}).json()
    assert len(by_order["payments"]) == 1


def test_declined_card_releases_stock(stack):
    cart_id = str(uuid.uuid4())
    httpx.post(f"{stack['cart']}/carts/{cart_id}/items", json={"productId": "vv-acc-001", "quantity": 1})
    before = _stock(stack, "vv-acc-001")
    r = httpx.post(f"{stack['checkout']}/checkout", timeout=20, json={
        "cartId": cart_id, "email": "a@b.co", "shippingAddress": ADDRESS,
        "card": {**GOOD_CARD, "number": "4000000000000002"}})
    assert r.status_code == 402 and r.json()["error"] == "card_declined"
    assert _stock(stack, "vv-acc-001") == before
    assert httpx.get(f"{stack['cart']}/carts/{cart_id}").json()["itemCount"] == 1


def test_insufficient_stock(stack):
    cart_id = str(uuid.uuid4())
    r = httpx.post(f"{stack['cart']}/carts/{cart_id}/items", json={"productId": "vv-wear-004", "quantity": 10})
    assert r.status_code == 200
    # drain stock out-of-band so the reservation fails at checkout
    httpx.post(f"{stack['inventory']}/reservations",
               json={"orderId": "VV-DRAIN", "items": [{"productId": "vv-wear-004", "quantity": 20}]})
    r = httpx.post(f"{stack['checkout']}/checkout", timeout=20, json={
        "cartId": cart_id, "email": "a@b.co", "shippingAddress": ADDRESS, "card": GOOD_CARD})
    assert r.status_code == 409, r.text
    httpx.post(f"{stack['inventory']}/reservations/VV-DRAIN/release")


def test_agent_tools(stack):
    from app.tools import build_tools

    cart_id = str(uuid.uuid4())
    used: list[str] = []
    tools = {t.tool_name: t for t in build_tools(cart_id, used)}
    assert len(tools) == 13

    found = json.loads(tools["search_products"](query="drone"))
    assert {p["productId"] for p in found["products"]} == {"vv-drone-001", "vv-drone-002"}
    assert "art" not in found["products"][0]
    assert json.loads(tools["add_to_cart"](product_id="vv-drone-002", quantity=1))["itemCount"] == 1
    assert json.loads(tools["view_cart"]())["items"][0]["productId"] == "vv-drone-002"
    assert json.loads(tools["get_checkout_quote"]())["total"] > 759
    assert json.loads(tools["get_order_status"](order_id="VV-NOPE1234", email="a@b.co"))["error"] == "order_not_found"
    assert json.loads(tools["get_product_details"](product_id="../restock"))["error"] == "invalid_id"
    assert "returns" in json.loads(tools["get_store_policies"](topic="returns"))
    assert "add_to_cart" in used and "search_products" in used


def test_history_conversion():
    from app.main import ChatMessage, _history

    msgs = [ChatMessage(role="assistant", content="Hi! I'm Sparky."), ChatMessage(role="user", content="a"),
            ChatMessage(role="user", content="b"), ChatMessage(role="assistant", content="c"),
            ChatMessage(role="user", content="d")]
    history, prompt = _history(msgs)
    assert prompt == "d"
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"][0]["text"] == "a\n\nb"


def test_chat_stream_sse(monkeypatch):
    """Exercise the SSE plumbing with a fake agent stream (no Bedrock call)."""
    from fastapi.testclient import TestClient

    import app.main as m

    async def fake_stream(self, prompt, **kwargs):
        yield {"data": "Let me check. "}
        yield {"current_tool_use": {"toolUseId": "t1", "name": "search_products"}}
        yield {"current_tool_use": {"toolUseId": "t1", "name": "search_products"}}
        yield {"data": "Found 2 drones."}
        yield {"result": object()}

    monkeypatch.setattr(m.Agent, "stream_async", fake_stream)
    client = TestClient(m.app)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "drones?"}]})
    assert r.status_code == 200
    events = [f.split("\n")[0].removeprefix("event: ") for f in r.text.strip().split("\n\n") if f.startswith("event:")]
    assert events == ["delta", "tool", "delta", "done"]
