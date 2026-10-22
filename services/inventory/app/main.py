"""Inventory service: stock levels and atomic reservations (DynamoDB transactions).

Table layout (partition key `pk`):
  SKU#<productId>  -> {available, reserved}
  RES#<orderId>    -> {status: HELD|COMMITTED|RELEASED, items: [{productId, quantity}]}
"""

from __future__ import annotations

import logging
import time

from botocore.exceptions import ClientError
from fastapi import Query
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field

from voltcommon import ServiceError, create_app, dynamodb_table, to_jsonable

SERVICE = "inventory"
app = create_app(SERVICE)
log = logging.getLogger(SERVICE)
meter = metrics.get_meter("voltverse.inventory")
reservation_counter = meter.create_counter("voltverse.inventory.reservations", description="Reservation attempts by outcome")
LOW_STOCK_THRESHOLD = 10


class Line(BaseModel):
    productId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=20)


class ReservationRequest(BaseModel):
    orderId: str = Field(min_length=1, max_length=64)
    items: list[Line] = Field(min_length=1, max_length=50)


class RestockRequest(BaseModel):
    productId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=10000)


# Note: table.meta.client (de)serializes native Python values, so no {"S": ...} wrappers below.
def _table():
    return dynamodb_table("INVENTORY_TABLE")


def _stock_view(product_id: str, item: dict | None) -> dict:
    available = int(item.get("available", 0)) if item else 0
    status = "out_of_stock" if available <= 0 else "low_stock" if available <= LOW_STOCK_THRESHOLD else "in_stock"
    return {"productId": product_id, "available": available, "status": status}


@app.get("/stock/{product_id}")
def get_stock(product_id: str):
    item = _table().get_item(Key={"pk": f"SKU#{product_id}"}).get("Item")
    if not item:
        raise ServiceError(404, "unknown_product", f"No inventory record for '{product_id}'")
    return _stock_view(product_id, item)


@app.get("/stock")
def get_stock_bulk(ids: str = Query(..., max_length=2000)):
    product_ids = list(dict.fromkeys(i for i in ids.split(",") if i))[:100]
    if not product_ids:
        return {"stock": []}
    table = _table()
    resp = table.meta.client.batch_get_item(
        RequestItems={table.name: {"Keys": [{"pk": f"SKU#{p}"} for p in product_ids]}}
    )
    found = {i["pk"][4:]: i for i in resp["Responses"].get(table.name, [])}
    return {"stock": [_stock_view(p, found.get(p)) for p in product_ids]}


@app.post("/reservations", status_code=201)
def reserve(req: ReservationRequest):
    """Atomically decrement stock for every line or fail the whole reservation."""
    span = trace.get_current_span()
    span.set_attribute("voltverse.order.id", req.orderId)
    table = _table()
    merged: dict[str, int] = {}
    for line in req.items:
        merged[line.productId] = merged.get(line.productId, 0) + line.quantity

    transact = [
        {
            "Update": {
                "TableName": table.name,
                "Key": {"pk": f"SKU#{pid}"},
                "UpdateExpression": "SET available = available - :q, reserved = if_not_exists(reserved, :zero) + :q",
                "ConditionExpression": "attribute_exists(pk) AND available >= :q",
                "ExpressionAttributeValues": {":q": qty, ":zero": 0},
            }
        }
        for pid, qty in merged.items()
    ]
    transact.append(
        {
            "Put": {
                "TableName": table.name,
                "Item": {
                    "pk": f"RES#{req.orderId}",
                    "status": "HELD",
                    "createdAt": int(time.time()),
                    "items": [{"productId": p, "quantity": q} for p, q in merged.items()],
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }
    )
    try:
        table.meta.client.transact_write_items(TransactItems=transact)
    except ClientError as e:
        if e.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = e.response.get("CancellationReasons", [])
        failed = [pid for (pid, _), r in zip(merged.items(), reasons) if r.get("Code") == "ConditionalCheckFailed"]
        if len(reasons) > len(merged) and reasons[-1].get("Code") == "ConditionalCheckFailed":
            raise ServiceError(409, "reservation_exists", f"Order {req.orderId} already has a reservation")
        reservation_counter.add(1, {"outcome": "insufficient_stock"})
        span.set_attribute("voltverse.inventory.insufficient", ",".join(failed))
        raise ServiceError(409, "insufficient_stock", f"Insufficient stock for: {', '.join(failed) or 'one or more items'}")
    reservation_counter.add(1, {"outcome": "held"})
    return {"orderId": req.orderId, "status": "HELD", "items": [{"productId": p, "quantity": q} for p, q in merged.items()]}


def _transition(order_id: str, new_status: str, return_stock: bool) -> dict:
    table = _table()
    res = table.get_item(Key={"pk": f"RES#{order_id}"}).get("Item")
    if not res:
        raise ServiceError(404, "reservation_not_found", f"No reservation for order {order_id}")
    if res["status"] == new_status:
        return {"orderId": order_id, "status": new_status}
    allowed = {"COMMITTED": {"HELD"}, "RELEASED": {"HELD", "COMMITTED"}}[new_status]
    if res["status"] not in allowed:
        raise ServiceError(409, "invalid_transition", f"Reservation is {res['status']}, cannot move to {new_status}")

    items = to_jsonable(res["items"])
    transact = [
        {
            "Update": {
                "TableName": table.name,
                "Key": {"pk": f"RES#{order_id}"},
                "UpdateExpression": "SET #s = :new",
                "ConditionExpression": "#s = :old",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {":new": new_status, ":old": res["status"]},
            }
        }
    ]
    for line in items:
        if return_stock:
            expr = "SET available = available + :q, reserved = reserved - :r"
            # stock that was already committed (shipped out of reserved) is returned as available too
            reserved_delta = line["quantity"] if res["status"] == "HELD" else 0
            values = {":q": line["quantity"], ":r": reserved_delta}
        else:
            expr = "SET reserved = reserved - :q"
            values = {":q": line["quantity"]}
        transact.append(
            {"Update": {"TableName": table.name, "Key": {"pk": f"SKU#{line['productId']}"},
                        "UpdateExpression": expr, "ExpressionAttributeValues": values}}
        )
    table.meta.client.transact_write_items(TransactItems=transact)
    reservation_counter.add(1, {"outcome": new_status.lower()})
    return {"orderId": order_id, "status": new_status}


@app.post("/reservations/{order_id}/commit")
def commit(order_id: str):
    return _transition(order_id, "COMMITTED", return_stock=False)


@app.post("/reservations/{order_id}/release")
def release(order_id: str):
    return _transition(order_id, "RELEASED", return_stock=True)


@app.post("/restock")
def restock(req: RestockRequest):
    """Internal-only endpoint (not routed through the public gateway)."""
    resp = _table().update_item(
        Key={"pk": f"SKU#{req.productId}"},
        UpdateExpression="SET available = if_not_exists(available, :zero) + :q, reserved = if_not_exists(reserved, :zero)",
        ExpressionAttributeValues={":q": req.quantity, ":zero": 0},
        ReturnValues="ALL_NEW",
    )
    log.info("restocked %s by %d", req.productId, req.quantity)
    return _stock_view(req.productId, resp["Attributes"])
