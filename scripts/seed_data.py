#!/usr/bin/env python3
"""Load data/products.json into the Products and Inventory DynamoDB tables.

Products are upserted on every run (so catalog edits roll out with a redeploy). Inventory rows
are only created when missing, so a redeploy never resets live stock levels.

Usage:
  seed_data.py --products-table T --inventory-table T [--region R] [--endpoint-url URL] [--reset-stock]
"""

import argparse
import json
import pathlib
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--products-table", required=True)
    ap.add_argument("--inventory-table", required=True)
    ap.add_argument("--region", default=None)
    ap.add_argument("--endpoint-url", default=None, help="e.g. http://127.0.0.1:8000 for DynamoDB Local")
    ap.add_argument("--reset-stock", action="store_true", help="overwrite stock levels with initialStock")
    args = ap.parse_args()

    ddb = boto3.resource("dynamodb", region_name=args.region, endpoint_url=args.endpoint_url)
    products = json.loads((ROOT / "data" / "products.json").read_text(), parse_float=Decimal)
    ptable, itable = ddb.Table(args.products_table), ddb.Table(args.inventory_table)

    with ptable.batch_writer() as batch:
        for p in products:
            batch.put_item(Item={k: v for k, v in p.items() if k != "initialStock"})

    created = 0
    for p in products:
        item = {"pk": f"SKU#{p['productId']}", "available": p["initialStock"], "reserved": 0}
        try:
            if args.reset_stock:
                itable.put_item(Item=item)
            else:
                itable.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            created += 1
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    print(f"Seeded {len(products)} products; created/reset {created} inventory records.")


if __name__ == "__main__":
    main()
