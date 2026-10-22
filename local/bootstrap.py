"""Create the DynamoDB Local tables and seed the catalog (used by docker-compose)."""
import os
import subprocess
import sys
import time

import boto3

endpoint = os.environ["DYNAMODB_ENDPOINT"]
ddb = boto3.client("dynamodb", endpoint_url=endpoint, region_name="us-west-2")
tables = {
    "voltverse-products": ("productId", None),
    "voltverse-inventory": ("pk", None),
    "voltverse-carts": ("cartId", None),
    "voltverse-orders": ("orderId", None),
    "voltverse-payments": ("paymentId", "orderId"),
}
for _ in range(30):
    try:
        existing = set(ddb.list_tables()["TableNames"])
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("DynamoDB Local did not start")

for name, (key, gsi) in tables.items():
    if name in existing:
        continue
    attrs = [{"AttributeName": key, "AttributeType": "S"}]
    extra = {}
    if gsi:
        attrs.append({"AttributeName": gsi, "AttributeType": "S"})
        extra["GlobalSecondaryIndexes"] = [{"IndexName": f"{gsi}-index", "KeySchema": [{"AttributeName": gsi, "KeyType": "HASH"}],
                                            "Projection": {"ProjectionType": "ALL"}}]
    ddb.create_table(TableName=name, AttributeDefinitions=attrs, KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                     BillingMode="PAY_PER_REQUEST", **extra)
    print("created", name)

subprocess.run([sys.executable, "/repo/scripts/seed_data.py", "--products-table", "voltverse-products",
                "--inventory-table", "voltverse-inventory", "--region", "us-west-2", "--endpoint-url", endpoint], check=True)
