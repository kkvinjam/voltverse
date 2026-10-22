"""Sparky - the VoltVerse AI support assistant, built with Strands Agents on Amazon Bedrock.

The service is stateless: the storefront sends the visible conversation with each request,
so any replica can answer. Strands emits OpenTelemetry spans for every agent cycle, model
call and tool call through the global tracer provider set up by `opentelemetry-instrument`,
so agent traces join the same distributed trace as the microservice calls they trigger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator

from botocore.config import Config
from fastapi.responses import StreamingResponse
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field
from strands import Agent
from strands.models import BedrockModel

from voltcommon import ServiceError, create_app

from .tools import build_tools

SERVICE = "assistant"
app = create_app(SERVICE)
log = logging.getLogger(SERVICE)
meter = metrics.get_meter("voltverse.assistant")
chat_counter = meter.create_counter("voltverse.assistant.chats", description="Assistant requests by outcome")
chat_latency = meter.create_histogram("voltverse.assistant.latency", unit="s", description="End-to-end agent latency")
tool_counter = meter.create_counter("voltverse.assistant.tool_calls", description="Tool invocations by tool")

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
REGION = os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION", "us-west-2")
MAX_TURNS = 20
MAX_CHARS = 2000
AGENT_LIMITS = {"turns": 10}  # cap model<->tool cycles per request (cost and latency guard)
HEARTBEAT_SECONDS = 10

SYSTEM_PROMPT = """You are Sparky, the friendly and knowledgeable support assistant for VoltVerse, an online \
electronics store ("Electronics, energized."). You help shoppers find products, compare options, manage their \
cart, check stock, understand totals, track orders, cancel orders, start returns and check refunds.

How to work:
- Use your tools for every fact about products, prices, stock, carts, orders and payments. Never invent \
product names, prices, specs, order details or policies - if a tool doesn't return it, say you don't know.
- Product ids look like "vv-phone-001". Order numbers look like "VV-7K2M9QX4".
- Order lookups, cancellations, returns and payment checks need BOTH the order number and the email used at \
checkout. Ask for whichever is missing. If a lookup fails, say no order matched those details - don't reveal more.
- Before cancelling an order or starting a return, summarize what will happen and get an explicit "yes".
- Only add items to the cart when the shopper asks. After changing the cart, confirm what changed.
- You cannot place orders or take payment. When the shopper is ready, point them to the Checkout button. \
Never ask for or repeat full card numbers, CVCs or passwords.
- Recommend products by matching the shopper's needs and budget; mention 2-3 options with a one-line reason each.
- Keep replies short, warm and skimmable: plain sentences or short bullet lists, prices like $1,299.00. \
Use **bold** for product names. No headings or tables.
- Stay on topic: VoltVerse shopping and support. Politely decline unrelated requests.
"""


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    cartId: str | None = Field(None, pattern=r"^[0-9a-fA-F-]{36}$")
    sessionId: str | None = Field(None, max_length=64)


def _model() -> BedrockModel:
    return BedrockModel(
        model_id=MODEL_ID,
        region_name=REGION,
        max_tokens=4096,
        boto_client_config=Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=120),
    )


def _history(messages: list[ChatMessage]) -> tuple[list[dict], str]:
    """Convert UI messages into Strands/Bedrock messages: start with user, strictly alternate, end with user."""
    turns = [(m.role, m.content[:MAX_CHARS]) for m in messages][-MAX_TURNS:]
    while turns and turns[0][0] != "user":
        turns.pop(0)
    if not turns or turns[-1][0] != "user":
        raise ServiceError(400, "invalid_conversation", "The last message must come from the user")
    merged: list[list] = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1][1] += "\n\n" + text
        else:
            merged.append([role, text])
    prompt = merged.pop()[1]
    return [{"role": r, "content": [{"text": t}]} for r, t in merged], prompt


def _agent(req: ChatRequest, session_id: str, history: list[dict], tools_used: list[str]) -> Agent:
    return Agent(
        name="sparky",
        model=_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=build_tools(req.cartId, tools_used),
        messages=history,
        callback_handler=None,
        trace_attributes={"session.id": session_id, "voltverse.agent": "sparky"},
    )


def _summary(session_id: str, tools_used: list[str]) -> dict:
    for name in tools_used:
        tool_counter.add(1, {"tool": name})
    return {"sessionId": session_id, "toolsUsed": sorted(set(tools_used)),
            "cartChanged": any(t in ("add_to_cart", "update_cart_item") for t in tools_used)}


def _failed(span, e: Exception) -> None:
    chat_counter.add(1, {"outcome": "error", "error_type": type(e).__name__})
    span.record_exception(e)
    log.exception("agent invocation failed")


UNAVAILABLE = "Sparky is taking a short break. Please try again in a moment."


@app.post("/chat")
async def chat(req: ChatRequest):
    """Non-streaming variant (handy for curl and tests)."""
    session_id = req.sessionId or str(uuid.uuid4())
    span = trace.get_current_span()
    span.set_attribute("session.id", session_id)
    history, prompt = _history(req.messages)
    tools_used: list[str] = []
    agent = _agent(req, session_id, history, tools_used)
    start = time.perf_counter()
    try:
        result = await agent.invoke_async(prompt, limits=AGENT_LIMITS)
    except Exception as e:  # Bedrock throttling, access denied, model not enabled, ...
        _failed(span, e)
        raise ServiceError(503, "assistant_unavailable", UNAVAILABLE) from e
    finally:
        chat_latency.record(time.perf_counter() - start, {"model": MODEL_ID})
    chat_counter.add(1, {"outcome": "success"})
    reply = str(result).strip() or "Sorry, I didn't catch that - could you rephrase?"
    return {"reply": reply, **_summary(session_id, tools_used)}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Server-Sent Events: `tool` when a tool starts, `delta` text chunks, then `done` (or `error`).

    Heartbeat comments keep the connection alive through CloudFront/ALB while tools run.
    """
    session_id = req.sessionId or str(uuid.uuid4())
    span = trace.get_current_span()
    span.set_attribute("session.id", session_id)
    history, prompt = _history(req.messages)
    tools_used: list[str] = []
    agent = _agent(req, session_id, history, tools_used)

    async def events() -> AsyncIterator[str]:
        start = time.perf_counter()
        seen_tools: set[str] = set()
        stream = agent.stream_async(prompt, limits=AGENT_LIMITS).__aiter__()
        pending = asyncio.ensure_future(stream.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    yield ": ping\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = asyncio.ensure_future(stream.__anext__())
                if "data" in event and isinstance(event["data"], str):
                    yield _sse("delta", {"text": event["data"]})
                elif "current_tool_use" in event:
                    tool = event["current_tool_use"] or {}
                    tid = tool.get("toolUseId")
                    if tid and tid not in seen_tools and tool.get("name"):
                        seen_tools.add(tid)
                        yield _sse("tool", {"name": tool["name"]})
            chat_counter.add(1, {"outcome": "success"})
            yield _sse("done", _summary(session_id, tools_used))
        except Exception as e:
            _failed(span, e)
            yield _sse("error", {"message": UNAVAILABLE})
        finally:
            if not pending.done():
                pending.cancel()
            chat_latency.record(time.perf_counter() - start, {"model": MODEL_ID})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
