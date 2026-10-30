#!/usr/bin/env python3
"""Simulated shoppers that hold realistic, unscripted conversations with standalone Sparky.

A second (cheaper) Bedrock model role-plays a shopper with a persona and a goal, for example "a commuter who
wants noise-cancelling headphones under $400" or "a customer chasing a late order who forgot their email". The
shopper reads each Sparky reply and writes the next message, until the goal is reached or MAX_TURNS is hit.
Order scenarios use the demo orders seeded in Sparky's embedded store (`{"action": "demo_data"}`), fetched in
the same runtime session, so order numbers and emails are always real.

Targets (same /invocations contract either way):
  --runtime-arn arn:aws:bedrock-agentcore:...:runtime/...   Sparky on Amazon Bedrock AgentCore Runtime
  --url http://127.0.0.1:8080                                 the standalone container (services/sparky-standalone.Dockerfile)

Usage:
  scripts/sparky_shopper.py --url http://127.0.0.1:8080 --conversations 3 --verbose
  scripts/sparky_shopper.py --runtime-arn "$ARN" --conversations 0 --interval 120   # forever, one every 2 min
Also runs as an AWS Lambda function (handler: sparky_shopper.handler), see cloudformation/sparky-standalone.yaml.

BOTH SIDES OF EVERY TURN CALL AMAZON BEDROCK. Requires boto3 with the bedrock-agentcore client for --runtime-arn.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

SHOPPER_MODEL_ID = os.getenv("SHOPPER_MODEL_ID", "global.anthropic.claude-haiku-5-5")
MAX_TURNS = int(os.getenv("MAX_TURNS", "6"))
DONE = "[DONE]"

# Each persona: who the shopper is, what they want, and (optionally) which seeded demo order they own.
PERSONAS = [
    {"name": "commuter_headphones", "persona": "a daily train commuter, a bit impatient",
     "goal": "find noise-cancelling headphones under $400, compare two options, and add the better one to your cart"},
    {"name": "video_editor_laptop", "persona": "a freelance video editor who knows specs",
     "goal": "find the best laptop for 4K video editing, ask about RAM and battery, and check it's in stock"},
    {"name": "phone_comparison", "persona": "an indecisive shopper upgrading an old phone",
     "goal": "understand the difference between two VoltVerse phones and pick one for photography"},
    {"name": "teen_gift", "persona": "a parent buying a birthday gift for a 15-year-old gamer",
     "goal": "get 2-3 gift ideas around $100 and add one to your cart"},
    {"name": "cart_builder", "persona": "an organized shopper setting up a home office",
     "goal": "add a charger and a pair of earbuds to your cart, change the earbud quantity to 2, then ask for the "
             "total with express shipping"},
    {"name": "smart_home_starter", "persona": "a first-time smart-home buyer, not technical",
     "goal": "find out what you need to start a smart home on a $200 budget and whether it's in stock"},
    {"name": "policy_questions", "persona": "a careful shopper who reads the fine print",
     "goal": "learn the return window, the warranty on drones, and whether VoltVerse price-matches"},
    {"name": "track_order", "persona": "a customer waiting for a package", "order": "shipped",
     "goal": "find out where your order is and when it will arrive"},
    {"name": "track_order_forgot_email", "persona": "a busy customer who first forgets to give their email",
     "order": "out_for_delivery", "hide_email_first": True,
     "goal": "check the status of your order; only give your email when asked"},
    {"name": "cancel_order", "persona": "a customer who ordered by mistake", "order": "processing",
     "goal": "cancel your order and confirm you'll be refunded"},
    {"name": "cancel_too_late", "persona": "a customer who changed their mind", "order": "shipped",
     "goal": "try to cancel your order; if that's not possible, ask what your options are"},
    {"name": "return_item", "persona": "a customer unhappy with a camera", "order": "delivered_returnable",
     "goal": "start a return for your delivered order and find out how the refund works"},
    {"name": "return_window_closed", "persona": "a customer who waited too long", "order": "delivered_window_closed",
     "goal": "try to return your order"},
    {"name": "refund_check", "persona": "a customer checking their bank statement", "order": "cancelled_refunded",
     "goal": "confirm whether you were refunded for your cancelled order and which card was used"},
    {"name": "off_topic", "persona": "a bored visitor",
     "goal": "ask Sparky for help with something unrelated to shopping (like a poem or homework), then ask one "
             "real question about a product"},
]

SHOPPER_PROMPT = """You are role-playing a customer chatting with Sparky, the AI shopping assistant of VoltVerse, \
an online electronics store.

Who you are: {persona}.
Your goal: {goal}.
{facts}
Rules:
- Write ONLY your next chat message: 1-3 short, natural sentences, like a real person typing in a chat widget.
- Stay in character and stay consistent. Use only the order number and email given above; never invent others.
- Don't repeat questions Sparky already answered. Answer Sparky's questions when it asks.
- When your goal is done (or Sparky clearly can't help), write a short closing line and end it with {done}.
- Never mention that you are an AI, a simulation or a test."""


class HttpTarget:
    """The standalone container: POST {url}/invocations, same contract as AgentCore Runtime."""

    def __init__(self, url: str):
        self.url = url.rstrip("/") + "/invocations"

    def invoke(self, session_id: str, payload: dict) -> dict:
        req = urllib.request.Request(self.url, method="POST", data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from e


class AgentCoreTarget:
    def __init__(self, runtime_arn: str):
        self.arn = runtime_arn
        region = runtime_arn.split(":")[3]
        self.client = boto3.client("bedrock-agentcore", region_name=region,
                                   config=Config(read_timeout=180, retries={"max_attempts": 3, "mode": "adaptive"}))

    def invoke(self, session_id: str, payload: dict) -> dict:
        resp = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.arn, runtimeSessionId=session_id, payload=json.dumps(payload).encode(),
            contentType="application/json", accept="application/json")
        return json.loads(resp["response"].read())


class Shopper:
    def __init__(self, model_id: str = SHOPPER_MODEL_ID, region: str | None = None):
        self.model_id = model_id
        self.bedrock = boto3.client("bedrock-runtime", region_name=region or os.getenv("AWS_REGION"),
                                    config=Config(retries={"max_attempts": 4, "mode": "adaptive"}))

    def next_message(self, system: str, transcript: list[tuple[str, str]]) -> str:
        # From the shopper model's point of view, Sparky is the "user" and the shopper is the "assistant".
        messages = [{"role": "user", "content": [{"text": "(The chat window is open. Send your first message.)"}]}]
        for who, text in transcript:
            messages.append({"role": "assistant" if who == "shopper" else "user", "content": [{"text": text}]})
        resp = self.bedrock.converse(modelId=self.model_id, system=[{"text": system}], messages=messages,
                                     inferenceConfig={"maxTokens": 200})
        return "".join(c.get("text", "") for c in resp["output"]["message"]["content"]).strip()


def run_conversation(target, shopper: Shopper, persona: dict | None = None, max_turns: int = MAX_TURNS,
                     verbose: bool = False) -> dict:
    session_id = f"shopper-{uuid.uuid4()}"  # AgentCore session ids must be at least 33 characters
    started = time.monotonic()
    result = {"event": "conversation", "sessionId": session_id, "turns": 0, "tools": [], "outcome": "max_turns"}
    try:
        orders = {}
        if persona is None or persona.get("order"):
            try:
                orders = {o["scenario"]: o for o in target.invoke(session_id, {"action": "demo_data"})["orders"]}
            except Exception as e:  # e.g. Sparky with the http backend: no demo data, skip order personas
                if persona:
                    raise RuntimeError(f"persona needs demo orders: {e}") from e
        candidates = [p for p in PERSONAS if not p.get("order") or p["order"] in orders]
        persona = persona or random.choice(candidates)
        result["persona"] = persona["name"]
        facts = ""
        if persona.get("order"):
            o = orders[persona["order"]]
            facts = (f"Your order number is {o['orderId']} and the email you used is {o['email']}. "
                     f"You ordered: {', '.join(o['items'])}.\n")
            if persona.get("hide_email_first"):
                facts += "Don't give your email in your first message.\n"
        system = SHOPPER_PROMPT.format(persona=persona["persona"], goal=persona["goal"], facts=facts, done=DONE)
        transcript: list[tuple[str, str]] = []
        tools: set[str] = set()
        for _ in range(max_turns):
            message = shopper.next_message(system, transcript)
            done = DONE in message
            message = message.replace(DONE, "").strip()
            if done and transcript:
                result["outcome"] = "goal_reached"
                break
            if not message:
                result["outcome"] = "shopper_empty"
                break
            reply = target.invoke(session_id, {"prompt": message[:2000]})
            transcript += [("shopper", message), ("sparky", reply["response"])]
            tools.update(reply.get("toolsUsed", []))
            result["turns"] += 1
            if verbose:
                short = re.sub(r"\s+", " ", reply["response"])[:240]
                print(f"  [{persona['name']}] shopper: {message}\n  [{persona['name']}] sparky:  {short}", flush=True)
        result["tools"] = sorted(tools)
    except Exception as e:
        result.update(outcome="error", error=f"{type(e).__name__}: {str(e)[:300]}")
    result["seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(result), flush=True)  # one line per conversation (CloudWatch Logs Insights friendly)
    return result


def _target(runtime_arn: str | None, url: str | None):
    if runtime_arn:
        return AgentCoreTarget(runtime_arn)
    if url:
        return HttpTarget(url)
    raise SystemExit("set --runtime-arn / SPARKY_RUNTIME_ARN or --url / SPARKY_URL")


def handler(event, context):
    """AWS Lambda entry point: run event["conversations"] conversations (default CONVERSATIONS_PER_RUN)."""
    target = _target(os.getenv("SPARKY_RUNTIME_ARN"), os.getenv("SPARKY_URL"))
    shopper = Shopper()
    wanted = int((event or {}).get("conversations") or os.getenv("CONVERSATIONS_PER_RUN", "1"))
    results = []
    for _ in range(wanted):
        if context and context.get_remaining_time_in_millis() < 240_000:
            break  # leave room for one more full conversation
        results.append(run_conversation(target, shopper))
    return {"conversations": len(results), "outcomes": [r["outcome"] for r in results]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime-arn", default=os.getenv("SPARKY_RUNTIME_ARN"))
    ap.add_argument("--url", default=os.getenv("SPARKY_URL"))
    ap.add_argument("--conversations", type=int, default=1, help="how many conversations (0 = forever)")
    ap.add_argument("--interval", type=float, default=0, help="seconds to wait between conversations")
    ap.add_argument("--persona", choices=[p["name"] for p in PERSONAS], help="always use this persona")
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--shopper-model", default=SHOPPER_MODEL_ID)
    ap.add_argument("--region", default=os.getenv("AWS_REGION"), help="region for the shopper model")
    ap.add_argument("--verbose", action="store_true", help="print each exchange")
    args = ap.parse_args()

    target = _target(args.runtime_arn, args.url)
    shopper = Shopper(args.shopper_model, args.region)
    persona = next((p for p in PERSONAS if p["name"] == args.persona), None)
    n = 0
    while args.conversations == 0 or n < args.conversations:
        run_conversation(target, shopper, persona, args.max_turns, args.verbose)
        n += 1
        if args.interval and (args.conversations == 0 or n < args.conversations):
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
