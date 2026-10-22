"""Catalog service: product browsing and search (read-only, backed by DynamoDB)."""

from __future__ import annotations

import time

from fastapi import Query
from opentelemetry import metrics, trace

from voltcommon import ServiceError, create_app, dynamodb_table, to_jsonable

SERVICE = "catalog"
app = create_app(SERVICE)
tracer = trace.get_tracer("voltverse.catalog")
meter = metrics.get_meter("voltverse.catalog")
search_counter = meter.create_counter("voltverse.catalog.searches", description="Product searches performed")
view_counter = meter.create_counter("voltverse.catalog.product_views", description="Product detail views")

CATEGORIES = [
    {"id": "smartphones", "name": "Smartphones", "icon": "phone"},
    {"id": "laptops", "name": "Laptops & Tablets", "icon": "laptop"},
    {"id": "audio", "name": "Audio", "icon": "headphones"},
    {"id": "wearables", "name": "Wearables", "icon": "watch"},
    {"id": "cameras", "name": "Cameras", "icon": "camera"},
    {"id": "gaming", "name": "Gaming", "icon": "gamepad"},
    {"id": "smart-home", "name": "Smart Home", "icon": "home"},
    {"id": "tv-display", "name": "TVs & Displays", "icon": "tv"},
    {"id": "drones", "name": "Drones", "icon": "drone"},
    {"id": "accessories", "name": "Accessories", "icon": "plug"},
]
_CATEGORY_IDS = {c["id"] for c in CATEGORIES}

_CACHE_TTL = 60.0
_cache: dict = {"at": 0.0, "items": []}


def _all_products() -> list[dict]:
    """The catalog is small, so cache a full scan for a minute."""
    now = time.monotonic()
    if _cache["items"] and now - _cache["at"] < _CACHE_TTL:
        return _cache["items"]
    with tracer.start_as_current_span("catalog.scan_products"):
        table = dynamodb_table("PRODUCTS_TABLE")
        items: list[dict] = []
        kwargs: dict = {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    _cache.update(at=now, items=[to_jsonable(i) for i in items])
    return _cache["items"]


def _summary(p: dict) -> dict:
    keys = ("productId", "name", "brand", "category", "price", "compareAtPrice", "rating",
            "reviewCount", "shortDescription", "art", "featured", "tags")
    return {k: p[k] for k in keys if k in p}


@app.get("/categories")
def categories():
    counts: dict[str, int] = {}
    for p in _all_products():
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    return {"categories": [{**c, "productCount": counts.get(c["id"], 0)} for c in CATEGORIES]}


@app.get("/products")
def list_products(
    category: str | None = None,
    q: str | None = Query(None, max_length=100),
    featured: bool | None = None,
    min_price: float | None = Query(None, alias="minPrice", ge=0),
    max_price: float | None = Query(None, alias="maxPrice", ge=0),
    sort: str = Query("relevance", pattern="^(relevance|price_asc|price_desc|rating|newest)$"),
    limit: int = Query(50, ge=1, le=100),
):
    if category and category not in _CATEGORY_IDS:
        raise ServiceError(400, "invalid_category", f"Unknown category '{category}'")
    products = _all_products()
    if category:
        products = [p for p in products if p["category"] == category]
    if featured is not None:
        products = [p for p in products if bool(p.get("featured")) == featured]
    if min_price is not None:
        products = [p for p in products if p["price"] >= min_price]
    if max_price is not None:
        products = [p for p in products if p["price"] <= max_price]

    scored: list[tuple[float, dict]] = []
    if q:
        search_counter.add(1, {"has_category": bool(category)})
        terms = [t for t in q.lower().split() if t]
        for p in products:
            haystack = " ".join([p["name"], p["brand"], p["category"], p.get("shortDescription", ""),
                                 p.get("description", ""), " ".join(p.get("tags", []))]).lower()
            name = p["name"].lower()
            score = 0.0
            for t in terms:
                if t in name:
                    score += 3
                elif t in haystack:
                    score += 1
                else:
                    score = -1
                    break
            if score > 0:
                scored.append((score, p))
        trace.get_current_span().set_attribute("voltverse.search.results", len(scored))
    else:
        scored = [(p.get("rating", 0), p) for p in products]

    if sort == "price_asc":
        scored.sort(key=lambda s: s[1]["price"])
    elif sort == "price_desc":
        scored.sort(key=lambda s: -s[1]["price"])
    elif sort == "rating":
        scored.sort(key=lambda s: -s[1].get("rating", 0))
    else:
        scored.sort(key=lambda s: (-s[0], -s[1].get("reviewCount", 0)))

    results = [_summary(p) for _, p in scored[:limit]]
    return {"products": results, "count": len(results)}


@app.get("/products/{product_id}")
def get_product(product_id: str):
    trace.get_current_span().set_attribute("voltverse.product.id", product_id)
    for p in _all_products():
        if p["productId"] == product_id:
            view_counter.add(1, {"category": p["category"]})
            return p
    raise ServiceError(404, "product_not_found", f"Product '{product_id}' does not exist")
