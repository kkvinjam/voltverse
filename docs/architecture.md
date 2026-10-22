# VoltVerse architecture

## Request path

```
viewer ──HTTPS──► CloudFront ─┬─ /assets/*  cached (CachingOptimized)
                              └─ everything else: no cache, all viewer headers + CloudFront-Viewer-Address
                                   │ VPC origin (private ENIs in the VPC)
                                   ▼
                    internal ALB (private subnets, SG: tcp/80 only from CloudFront-VPCOrigins-Service-SG, WAF attached)
                                   │ target group (type ip) ◄── TargetGroupBinding (EKS Auto Mode)
                                   ▼
                    storefront pods: nginx serves the SPA and proxies an allow-list of /api routes
```

The ALB is never internet-facing. CloudFront reaches it through a **VPC origin**, so CloudFront is the only public entry point, and it uses the default `*.cloudfront.net` certificate (no domain needed).

The ALB, its target group and listener are **created by CloudFormation**. Kubernetes only *binds* pods into the target group with a `TargetGroupBinding`. This avoids the usual chicken-and-egg problem where CloudFront would need the DNS name of a load balancer that Kubernetes hasn't created yet, and it lets the whole stack be one template.

### Public API surface (nginx allow-list)

| Public route | Upstream | Methods |
|---|---|---|
| `/api/catalog/categories`, `/api/catalog/products[/{id}]` | catalog | GET |
| `/api/inventory/stock?ids=` | inventory | GET |
| `/api/cart/carts/{uuid}[/items[/{id}]]` | cart | GET, POST, PUT, DELETE |
| `/api/checkout/quote`, `/api/checkout/checkout` | checkout | GET, POST |
| `/api/orders/orders/{id}[/cancel]` | orders | GET, POST (email required) |
| `/api/assistant/chat[/stream]` | assistant | POST |

Anything else under `/api/` returns 404 at the gateway. Payment, `POST /orders`, `/reservations*` and `/restock` are reachable only inside the cluster.

## Checkout saga

```
checkout ─► cart (load) ─► catalog (re-price every line)
         ─► inventory POST /reservations         (DynamoDB TransactWriteItems: all-or-nothing)
         ─► payment  POST /payments/authorize    ── fail ─► inventory release
         ─► orders   POST /orders                ── fail ─► payment refund + inventory release
         ─► payment capture, inventory commit, cart clear   (best effort, logged)
```

Cancelling an order (`orders`) releases the reservation in `inventory` and refunds the payment in `payment`.

## Data model (DynamoDB, on-demand)

| Table | Key | Notes |
|---|---|---|
| `<env>-products` | `productId` | Seeded from `data/products.json` on every deploy (upsert) |
| `<env>-inventory` | `pk` | `SKU#<id>` → `{available, reserved}`; `RES#<orderId>` → reservation state. Stock is only seeded when missing. |
| `<env>-carts` | `cartId` | TTL attribute `expiresAt` (30 days) |
| `<env>-orders` | `orderId` | Fulfilment status is derived from the order age |
| `<env>-payments` | `paymentId` | GSI `orderId-index` |

## Sparky (Strands agent)

- **Statelessness:** the browser sends the visible conversation on every turn, and the service builds a fresh `Agent` per request with `messages=` history. Any replica can answer and nothing is stored server-side.
- **Per-request tools:** tools are created per request by `build_tools(cart_id, ...)`, so the cart id is captured in a closure and never exposed to the model.
- **Streaming:** `POST /chat/stream` returns Server-Sent Events (`tool`, `delta`, `done`, `error`) and sends heartbeat comments every 10 s. These keep CloudFront's 60 s origin read timeout from firing while tools run.
- **Limits:** `limits={"turns": 10}` caps model↔tool cycles. `max_tokens=4096` caps each response.
- **Model:** `BedrockModel(model_id=BEDROCK_MODEL_ID)` via the Bedrock Converse API. IAM allows `bedrock:InvokeModel*` on foundation models and the account's inference profiles.

## Telemetry pipeline

```
service pod  (opentelemetry-instrument: FastAPI, httpx, botocore, logging; Strands GenAI spans)
   │ OTLP/HTTP  http://otel-collector.observability:4318
   ▼
ADOT collector ─ memory_limiter → resource(cloud.*, k8s.cluster.name) → batch
   ├─ traces  → awsxray            (X-Ray ids via OTEL_PYTHON_ID_GENERATOR=xray)
   ├─ metrics → awsemf             (namespace VoltVerse; dimension sets whitelisted to control cardinality)
   ├─ logs    → awscloudwatchlogs  (trace_id/span_id on every record)
   └─ (optional) otlphttp/external → ExternalOtlpEndpoint
```

All configuration uses standard `OTEL_*` variables in the `voltverse-config` ConfigMap (`k8s/20-config.yaml`). To send telemetry somewhere else, change `OTEL_EXPORTER_OTLP_ENDPOINT` or add exporters to the collector. The application code doesn't change.

## IAM (EKS Pod Identity)

| Service account | Permissions |
|---|---|
| catalog | `dynamodb:Scan/GetItem/Query` on products |
| inventory | `GetItem/BatchGetItem/PutItem/UpdateItem/ConditionCheckItem` on inventory |
| cart | `GetItem/PutItem/DeleteItem` on carts |
| orders | `GetItem/PutItem/UpdateItem` on orders |
| payment | `GetItem/PutItem/UpdateItem/Query` on payments (+ GSI) |
| assistant | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` |
| otel-collector | X-Ray writes, CloudWatch Logs writes to the two VoltVerse log groups |
| checkout, storefront, loadgen | none (no AWS API calls) |

## Deployment mechanics

`Custom::VoltVerseDeployment` → Lambda `StartBuild` → CodeBuild (inside the VPC, privileged for Docker):

1. Fetch the source from Git or the S3 zip.
2. Run `scripts/codebuild-deploy.sh`:
   - install a kubectl version that matches the cluster
   - build and push images (tag `b<build number>`)
   - seed DynamoDB
   - run `scripts/render.py k8s/*.yaml | kubectl apply -f -`
   - wait for rollouts and for healthy ALB targets
3. PUT the result to the CloudFormation pre-signed response URL.

On stack deletion the same path runs `scripts/codebuild-destroy.sh`, which always reports success, so a broken cluster never blocks teardown.
