"""Standalone Sparky: the embedded store, the AgentCore /invocations contract and the shopper simulator
(none of these call Bedrock)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "common"), str(ROOT / "services" / "assistant")]

URL_VARS = [f"{s.upper()}_URL" for s in ("catalog", "inventory", "cart", "checkout", "payment", "orders")]


@pytest.fixture(scope="module")
def embedded():
    """Start the embedded store once; restore the service URLs the integration-test stack relies on."""
    from app.embedded import EmbeddedStore

    saved = {k: os.environ.get(k) for k in URL_VARS}
    store = EmbeddedStore()
    store._start()
    try:
        store.wait(timeout=1)
        yield store
    finally:
        store.stop()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_embedded_store_seeds_orders_in_every_state(embedded):
    status = {o["scenario"]: o["status"] for o in embedded.demo_data()["orders"]}
    assert status == {
        "just_placed": "CONFIRMED", "processing": "PROCESSING", "shipped": "SHIPPED",
        "out_for_delivery": "OUT_FOR_DELIVERY", "delivered_returnable": "DELIVERED",
        "delivered_window_closed": "DELIVERED", "cancelled_refunded": "CANCELLED"}


def test_embedded_store_backs_the_agent_tools(embedded):
    from app.tools import build_tools

    os.environ.update({k: embedded.urls[k.removesuffix("_URL").lower()] for k in URL_VARS})
    orders = {o["scenario"]: o for o in embedded.demo_data()["orders"]}
    tools = {t.tool_name: t for t in build_tools(str(uuid.uuid4()), [])}

    found = json.loads(tools["search_products"](query="drone"))
    assert {p["productId"] for p in found["products"]} == {"vv-drone-001", "vv-drone-002"}
    assert json.loads(tools["add_to_cart"](product_id="vv-drone-002", quantity=1))["itemCount"] == 1
    assert json.loads(tools["get_checkout_quote"](shipping_method="express"))["total"] > 759

    o = orders["delivered_returnable"]
    returned = json.loads(tools["request_return"](order_id=o["orderId"], email=o["email"], reason="too heavy"))
    assert returned["returnRequest"]["rma"].startswith("RMA-")
    closed = orders["delivered_window_closed"]
    assert json.loads(tools["request_return"](order_id=closed["orderId"], email=closed["email"]))["error"] == \
        "return_window_closed"
    shipped = orders["shipped"]
    assert json.loads(tools["cancel_order"](order_id=shipped["orderId"], email=shipped["email"]))["error"] == \
        "not_cancellable"
    refunded = orders["cancelled_refunded"]
    payment = json.loads(tools["get_payment_status"](order_id=refunded["orderId"], email=refunded["email"]))
    assert payment["status"] == "REFUNDED" and payment["card"]["last4"] == "4242"


def test_invocations_remember_the_session(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m

    seen: list[list[dict]] = []

    async def fake_invoke(self, prompt, **kwargs):
        seen.append([*self.messages, {"role": "user", "content": [{"text": prompt}]}])
        return f"reply {len(seen)}"

    monkeypatch.setattr(m.Agent, "invoke_async", fake_invoke)
    client = TestClient(m.app)
    headers = {"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "s" * 40}
    assert client.get("/ping").json() == {"status": "Healthy"}
    first = client.post("/invocations", headers=headers, json={"prompt": "hi"}).json()
    second = client.post("/invocations", headers=headers, json={"prompt": "and drones?"}).json()
    assert first["response"] == "reply 1" and second["response"] == "reply 2"
    assert [msg["content"][0]["text"] for msg in seen[1]] == ["hi", "reply 1", "and drones?"]
    assert second["sessionId"] == "s" * 40
    assert client.post("/invocations", headers=headers, json={"action": "reset"}).json()["status"] == "reset"
    # demo data only exists with the embedded backend (tests run the default http backend)
    assert client.post("/invocations", headers=headers, json={"action": "demo_data"}).status_code == 404
    assert client.post("/invocations", headers=headers, json={}).status_code == 400


def _shopper_module():
    spec = importlib.util.spec_from_file_location("sparky_shopper", ROOT / "scripts" / "sparky_shopper.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shopper_conversation_flow(capsys):
    shopper_mod = _shopper_module()

    class FakeTarget:
        def __init__(self):
            self.payloads = []

        def invoke(self, session_id, payload):
            self.payloads.append(payload)
            if payload.get("action") == "demo_data":
                return {"orders": [{"scenario": "shipped", "orderId": "VV-ABCD1234", "email": "j@example.com",
                                    "items": ["Aether Book 14"], "status": "SHIPPED", "total": 1.0}]}
            return {"response": "It ships tomorrow.", "toolsUsed": ["get_order_status"]}

    class FakeShopper:
        def __init__(self):
            self.systems = []

        def next_message(self, system, transcript):
            self.systems.append(system)
            return "Where is my order VV-ABCD1234?" if not transcript else "Great, thanks! [DONE]"

    target, shopper = FakeTarget(), FakeShopper()
    persona = next(p for p in shopper_mod.PERSONAS if p["name"] == "track_order")
    result = shopper_mod.run_conversation(target, shopper, persona)
    assert result["outcome"] == "goal_reached" and result["turns"] == 1
    assert result["tools"] == ["get_order_status"]
    assert "VV-ABCD1234" in shopper.systems[0] and "j@example.com" in shopper.systems[0]
    assert target.payloads[0] == {"action": "demo_data"}
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["persona"] == "track_order"
