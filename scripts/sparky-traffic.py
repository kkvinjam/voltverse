#!/usr/bin/env python3
"""Generate realistic conversation traffic for Sparky, the VoltVerse AI assistant.

Runs multi-turn shopper conversations against the public store endpoint (through CloudFront,
WAF and the nginx gateway), so the traffic exercises the full path:
agent -> Bedrock -> tools -> microservices, and shows up in traces, metrics and logs.

Every turn calls Amazon Bedrock and costs money. Defaults are deliberately modest.
The gateway allows ~12 assistant requests/minute per client IP, so keep --rate at or below that.

Usage:
  scripts/sparky-traffic.py https://dxxxx.cloudfront.net                    # 10 min, ~8 turns/min
  scripts/sparky-traffic.py https://dxxxx.cloudfront.net --minutes 30 --rate 10 --concurrency 3
  scripts/sparky-traffic.py https://dxxxx.cloudfront.net --conversations 5  # fixed number, then stop
  scripts/sparky-traffic.py http://storefront.voltverse --minutes 0 --rate 2  # run forever (the sparky-traffic pod)

Standard library only (Python 3.9+).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

EMAILS = ["alex.rivera@example.com", "sam.chen@example.com", "jordan.lee@example.com", "taylor.kim@example.com"]
ADDRESS = {"name": "Traffic Test", "line1": "500 Demo Ave", "city": "Seattle", "state": "WA",
           "postalCode": "98101", "country": "US"}


class Client:
    def __init__(self, base: str, rate_per_min: float):
        self.base = base.rstrip("/")
        self.interval = 60.0 / rate_per_min
        self.lock = threading.Lock()
        self.next_slot = time.monotonic()
        self.stats = Counter()
        self.latencies: deque[float] = deque(maxlen=1000)  # bounded: the pod runs forever
        self.tools = Counter()

    def _pace(self) -> None:
        """Global token pacing across all worker threads (stays under the per-IP rate limit)."""
        with self.lock:
            now = time.monotonic()
            wait = self.next_slot - now
            self.next_slot = max(now, self.next_slot) + self.interval
        if wait > 0:
            time.sleep(wait)

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "User-Agent": "sparky-traffic/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def chat(self, messages: list[dict], cart_id: str, session_id: str) -> str | None:
        """One streamed Sparky turn. Returns the reply text, or None on failure."""
        for attempt in range(4):
            self._pace()
            req = urllib.request.Request(
                f"{self.base}/api/assistant/chat/stream", method="POST",
                data=json.dumps({"messages": messages, "cartId": cart_id, "sessionId": session_id}).encode(),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                         "User-Agent": "sparky-traffic/1.0"})
            start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    text, event, ok = [], None, False
                    for raw in r:
                        line = raw.decode("utf-8", "replace").rstrip("\n")
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            if event == "delta":
                                text.append(data.get("text", ""))
                            elif event == "tool":
                                self.tools[data.get("name", "?")] += 1
                            elif event == "done":
                                ok = True
                            elif event == "error":
                                self.stats["agent_error"] += 1
                                return None
                    if ok:
                        self.stats["turns_ok"] += 1
                        self.latencies.append(time.monotonic() - start)
                        return "".join(text)
                    self.stats["stream_incomplete"] += 1
                    return None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.stats["rate_limited_429"] += 1
                    time.sleep(10 * (attempt + 1))
                    continue
                self.stats[f"http_{e.code}"] += 1
                return None
            except Exception as e:  # timeouts, connection resets
                self.stats[f"exception_{type(e).__name__}"] += 1
                return None
        return None

    def place_order(self, cart_id: str, email: str) -> str | None:
        """Create a real order through the public checkout API (so order questions have data)."""
        status, cats = self.api("GET", "/api/catalog/products?limit=100")
        if status != 200:
            return None
        product = random.choice([p for p in cats["products"] if p["price"] < 600])
        self.api("POST", f"/api/cart/carts/{cart_id}/items", {"productId": product["productId"], "quantity": 1})
        status, order = self.api("POST", "/api/checkout/checkout", {
            "cartId": cart_id, "email": email, "shippingAddress": ADDRESS, "shippingMethod": "standard",
            "card": {"number": "4242424242424242", "expMonth": 12, "expYear": 2035, "cvc": "123", "name": "Traffic Test"}})
        return order.get("orderId") if status == 201 else None


# Each scenario is a list of user turns. "{order}" / "{email}" are filled in for order scenarios.
SCENARIOS: dict[str, list[str]] = {
    "recommend_headphones": ["I commute by train every day. Can you recommend noise cancelling headphones under $400?",
                             "Which of those has the best battery life?"],
    "recommend_laptop": ["I'm a video editor. What's your best laptop for creative work?",
                         "Is it in stock? And how does it compare to the Aether Book 14?"],
    "compare_phones": ["What's the difference between the Nova X1 and the Nova X1 Pro?",
                       "Which one would you pick for photography?"],
    "gift_ideas": ["I need a gift for a teenager who loves gaming, budget around $100. Ideas?"],
    "cart_and_quote": ["Please add the Pulse Buds Pro 2 to my cart.",
                       "Also add a Volt 140W GaN Charger.",
                       "What would my total be with express shipping?"],
    "cart_change": ["Add two Halo Glow Smart Bulb 4-packs to my cart.",
                    "Actually make that just one. What's in my cart now?"],
    "stock_check": ["Do you have the Lumen OLED 65 inch TV and the Orbit Air 3 drone in stock?"],
    "policies": ["What's your return policy? And how long is the warranty on drones?"],
    "price_match": ["I saw the Kinetic Play Console X cheaper elsewhere. Do you price match?"],
    "track_order": ["Where is my order {order}? My email is {email}.",
                    "When will it arrive?"],
    "track_order_missing_email": ["Can you check the status of order {order}?",
                                  "Sure, it's {email}."],
    "cancel_order": ["I want to cancel order {order}, email {email}.",
                     "Yes, please cancel it."],
    "payment_status": ["Was I charged for order {order}? Email is {email}. Which card did I use?"],
    "return_too_early": ["I'd like to return order {order} ({email}), it's not what I expected."],
    "wrong_email": ["Look up order {order} for wrong.person@example.com please."],
    "off_topic": ["Can you write me a poem about the ocean?"],
}
ORDER_SCENARIOS = {k for k, turns in SCENARIOS.items() if any("{order}" in t for t in turns)}


def run_conversation(client: Client, name: str, verbose: bool) -> None:
    cart_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    email = random.choice(EMAILS)
    order = None
    if name in ORDER_SCENARIOS:
        order = client.place_order(str(uuid.uuid4()), email)
        if not order:
            client.stats["order_setup_failed"] += 1
            return
    messages: list[dict] = []
    for turn in SCENARIOS[name]:
        messages.append({"role": "user", "content": turn.format(order=order, email=email)})
        reply = client.chat(messages, cart_id, session_id)
        if reply is None:
            break
        messages.append({"role": "assistant", "content": reply})
        if verbose:
            print(f"  [{name}] user: {messages[-2]['content']}\n  [{name}] sparky: {reply[:160].replace(chr(10), ' ')}...")
        time.sleep(random.uniform(1, 4))  # "reading time" between turns
    client.stats["conversations"] += 1
    client.stats[f"scenario:{name}"] += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="Store URL, e.g. https://dxxxx.cloudfront.net")
    ap.add_argument("--minutes", type=float, default=10, help="how long to run (default 10, 0 = forever)")
    ap.add_argument("--conversations", type=int, default=0, help="stop after N conversations instead")
    ap.add_argument("--rate", type=float, default=8, help="max Sparky turns per minute (default 8, gateway limit ~12)")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--verbose", action="store_true", help="print each exchange")
    args = ap.parse_args()

    client = Client(args.url, args.rate)
    forever = args.minutes <= 0 and not args.conversations
    deadline = float("inf") if forever else time.monotonic() + args.minutes * 60
    started = 0
    counter_lock = threading.Lock()
    t0 = time.monotonic()

    def worker() -> None:
        nonlocal started
        while time.monotonic() < deadline:
            with counter_lock:
                if args.conversations and started >= args.conversations:
                    return
                started += 1
                n = started
            try:
                run_conversation(client, random.choice(list(SCENARIOS)), args.verbose)
            except Exception as e:  # a long-running generator must survive anything
                client.stats[f"exception_{type(e).__name__}"] += 1
                time.sleep(10)
            if forever and n % 20 == 0:
                report(client, t0, f"after {n} conversations")

    duration = "forever" if forever else (
        "%d conversations" % args.conversations if args.conversations else "%g minutes" % args.minutes)
    print(f"Sparky traffic -> {args.url}  ({args.rate:g} turns/min max, {args.concurrency} parallel shoppers, {duration})",
          flush=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for _ in range(args.concurrency):
            pool.submit(worker)
    report(client, t0, "Done")


def report(client: Client, t0: float, title: str) -> None:
    lat = list(client.latencies)
    print(f"\n{title} in {(time.monotonic() - t0) / 60:.1f} min")
    print(f"conversations: {client.stats['conversations']}   turns ok: {client.stats['turns_ok']}")
    if lat:
        print(f"turn latency: p50 {statistics.median(lat):.1f}s  max {max(lat):.1f}s")
    problems = {k: v for k, v in list(client.stats.items()) if k not in ("conversations", "turns_ok") and not k.startswith("scenario:")}
    print(f"problems: {problems or 'none'}")
    print("tool calls:", dict(Counter(client.tools).most_common()))
    print("scenarios:", {k[9:]: v for k, v in sorted(client.stats.items()) if k.startswith("scenario:")}, flush=True)


if __name__ == "__main__":
    main()
