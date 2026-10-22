# ⚡ VoltVerse — *Electronics, energized.*

VoltVerse is a sleek, fully working electronics store built as a **microservices application on Amazon EKS**. It includes **Sparky**, an AI shopping and support assistant built with **Strands Agents** on **Amazon Bedrock**, and every component is instrumented with **OpenTelemetry**.

It is packaged as **one CloudFormation template**, so you can launch identical copies in many AWS accounts for workshops, demos, and observability training.

```
                         ┌──────────────── Amazon EKS (Auto Mode) ─────────────────────────────┐
 Browser ──HTTPS──► CloudFront ──VPC origin──► internal ALB ──► storefront (React + nginx gateway)
   ▲  WAF (rate caps,        │                                     │
   │  optional allow-list)   │      ┌──────────┬──────────┬────────┼─────────┬──────────┐
   │                         │      ▼          ▼          ▼        ▼         ▼          ▼
   │                         │   catalog   inventory     cart   checkout   orders   assistant ──► Amazon Bedrock
   │                         │      │          │          │      │  │  │     │      (Strands)     (Claude)
   │                         │      │          │          │      │  │  └► payment     │ 13 tools call the
   │                         │      ▼          ▼          ▼      ▼  ▼        ▼        │ services above
   │                         │   ───────────────── Amazon DynamoDB ──────────────     │
   │                         │                                                        │
   │                         │   every pod ──OTLP──► OpenTelemetry Collector (ADOT) ──┼──► AWS X-Ray (traces)
   │                         │                                                        ├──► CloudWatch Metrics (EMF)
   │                         └────────────────────────────────────────────────────────├──► CloudWatch Logs
   │                                                                                  └──► any OTLP endpoint (optional)
```

## What's inside

| Component | Tech | What it does |
|---|---|---|
| **storefront** | React 19 + Vite, nginx | Responsive SPA: home, category browsing, search, product pages, cart drawer, checkout, order tracking, and the Sparky chat widget. nginx is also the API gateway and exposes only an allow-list of public routes. |
| **catalog** | Python / FastAPI | 36 products in 10 categories, search, filters, sorting |
| **inventory** | Python / FastAPI | Live stock levels and **atomic reservations** (DynamoDB transactions) |
| **cart** | Python / FastAPI | Anonymous carts keyed by a browser UUID, with a 30-day TTL |
| **checkout** | Python / FastAPI | A saga that orchestrates pricing → reserve stock → authorize payment → create order → capture, and compensates on failure |
| **payment** | Python / FastAPI | Simulated card processor with test cards and fault injection. Stores only the card brand and last 4 digits. |
| **orders** | Python / FastAPI | Orders, simulated shipment tracking, cancellations (refund + restock), returns |
| **assistant** | Python / **Strands Agents** + Bedrock | **Sparky**: 13 tools, streaming replies (SSE), bounded agent loop |
| **loadgen** | Python | Synthetic shoppers, so dashboards and traces always have data (never calls Bedrock) |
| **otel-collector** | AWS Distro for OpenTelemetry | Receives OTLP from every pod and exports to X-Ray, CloudWatch, and an optional external OTLP endpoint |

### Sparky's tools

Each tool calls one microservice over HTTP, so agent reasoning, tool calls, and downstream service spans show up in **one distributed trace**.

| Tool | Service | | Tool | Service |
|---|---|---|---|---|
| `search_products` | catalog | | `get_order_status` | orders |
| `get_product_details` | catalog | | `cancel_order` | orders → inventory, payment |
| `list_categories` | catalog | | `request_return` | orders |
| `check_stock` | inventory | | `get_payment_status` | orders → payment |
| `view_cart` / `add_to_cart` / `update_cart_item` | cart | | `get_store_policies` | built-in knowledge |
| `get_checkout_quote` | checkout | | | |

Guardrails:
- The shopper's cart id is bound in code; the model never chooses it.
- Order tools require both the order number and the checkout email.
- IDs are validated before they are put into URLs.
- The agent asks for confirmation before cancellations and returns.
- Each request is capped at 10 model↔tool turns. A WAF rule and nginx rate limits cap Bedrock spend.

---

## Deploy

### Prerequisites

1. **An AWS Region that has:** EKS Auto Mode, CloudFront VPC origins, and your Bedrock model (for example `us-west-2` or `us-east-1`).
2. **Bedrock model access.** The default model is `global.anthropic.claude-opus-5-5`, a cross-region inference profile.
   - Anthropic models need a one-time *use-case form* per account. Submit it once in the Amazon Bedrock console, from the Anthropic model's page.
   - Confirm the ID is invocable: `aws bedrock list-inference-profiles --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].inferenceProfileId"`.
   - Use any other model or profile ID through the `BedrockModelId` parameter (for example `us.anthropic.claude-sonnet-5-5`).
3. **The source code must be reachable by CodeBuild.** Use one of:
   - **Public Git repo** (simplest): push this repo to GitHub and pass its HTTPS URL as `SourceRepoUrl`.
   - **S3 zip** (private repos, Workshop Studio): `scripts/package-source.sh <bucket>`, then set `SourceS3Bucket` / `SourceS3Key`.

### Option A — AWS Console

1. Open **CloudFormation → Create stack → With new resources**.
2. Upload `cloudformation/voltverse.yaml`.
3. Set `SourceRepoUrl` (or `SourceS3Bucket`). Leave everything else at defaults.
4. Tick **"I acknowledge that AWS CloudFormation might create IAM resources"** and create the stack.
5. After about 30 minutes, open the **`StorefrontUrl`** output.

### Option B — CLI

```bash
scripts/launch-stack.sh --repo https://github.com/<you>/voltverse.git
# or deploy from S3:
scripts/package-source.sh my-artifacts-bucket
scripts/launch-stack.sh --s3-bucket my-artifacts-bucket --s3-key voltverse-source.zip
```

### What happens during deployment

1. CloudFormation creates:
   - a VPC (2 AZs, single NAT), an EKS Auto Mode cluster, 5 DynamoDB tables, and 8 ECR repositories
   - least-privilege Pod Identity roles, an internal ALB, a WAF web ACL, a CloudFront distribution with a VPC origin, and a CloudWatch dashboard
2. A custom resource starts the **CodeBuild deployer**, which runs inside the VPC. The deployer:
   - clones the source
   - builds and pushes 8 images
   - seeds DynamoDB
   - renders and applies `k8s/*.yaml`
   - waits until the storefront is healthy behind the ALB
3. The build reports success or failure back to CloudFormation. If the deployment fails, the stack fails too, and the reason points at the CodeBuild build to inspect.

### Parameters

| Parameter | Default | Notes |
|---|---|---|
| `EnvironmentName` | `voltverse` | Prefix for all resource names. Must be unique per account and region. |
| `SourceRepoUrl` / `SourceBranch` | — / `main` | Public Git source |
| `SourceS3Bucket` / `SourceS3Key` | empty / `voltverse-source.zip` | S3 source. Overrides Git when set. |
| `BedrockModelId` | `global.anthropic.claude-opus-5-5` | Model or inference profile used by Sparky |
| `AssistantRequestsPer5Min` | `1000` | WAF cap on **total** assistant requests (Bedrock cost guard) |
| `ExternalOtlpEndpoint` | empty | Also send all telemetry to this OTLP/HTTP endpoint |
| `ParticipantRoleArn` | empty | IAM role granted EKS cluster-admin (for example a workshop participant role) |
| `EnablePublicClusterEndpoint` / `PublicEndpointCidrs` | `false` / empty | Expose the IAM-authenticated EKS API so `kubectl` works from laptops or CloudShell. Restrict it with CIDRs. |
| `AllowedViewerCidrs` | empty | Restrict the **store** to these CIDRs (WAF). Empty means public. |
| `KubernetesVersion` | empty (latest) | Pin an EKS version |
| `DeployVersion` | `1` | Change it and update the stack to rebuild and redeploy |

### Outputs

`StorefrontUrl`, `DashboardUrl`, `TraceMapUrl`, `ApplicationLogGroup`, `ClusterName`, `KubeconfigCommand`, `DeployerProject`.

---

## Running workshops in many accounts

The template has no account-specific values. Pick whichever launch method fits your setup.

**1. A script per account or profile:**
```bash
for p in team-01 team-02 team-03; do
  AWS_PROFILE=$p AWS_REGION=us-west-2 scripts/launch-stack.sh --repo https://github.com/<you>/voltverse.git &
done; wait
```

**2. CloudFormation StackSets (AWS Organizations, service-managed):**
```bash
aws cloudformation create-stack-set --stack-set-name voltverse \
  --template-body file://cloudformation/voltverse.yaml \
  --capabilities CAPABILITY_IAM --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=false \
  --parameters ParameterKey=SourceRepoUrl,ParameterValue=https://github.com/<you>/voltverse.git

aws cloudformation create-stack-instances --stack-set-name voltverse \
  --deployment-targets OrganizationalUnitIds=ou-xxxx-workshop --regions us-west-2 \
  --operation-preferences MaxConcurrentPercentage=100,FailureTolerancePercentage=10
```

**3. AWS Workshop Studio:** see [docs/workshop-studio.md](docs/workshop-studio.md) for a `contentspec.yaml` example. It shows the template and source zip as workshop assets, and the participant role passed through `ParticipantRoleArn`.

Notes for workshop operators:
- **Bedrock access is per account.** Submit the Anthropic use-case form in each account, or use the account vending process your event platform provides, before the event.
- **Cost (estimate, verify with the AWS Pricing Calculator):** roughly **US$0.45–0.70 per account per hour** while running. That covers EKS, Auto Mode nodes, NAT, ALB, WAF, and CloudWatch. Bedrock is extra and depends on usage. Delete stacks after the event.
- **Default quotas:** each account needs 1 EIP, 1 NAT gateway, 1 VPC, and 1 CloudFront distribution.

---

## Using the store

- **Test cards** (no real payments):

  | Card | Result |
  |---|---|
  | `4242 4242 4242 4242` | Approved |
  | `4000 0000 0000 0002` | Declined |
  | `4000 0000 0000 9995` | Insufficient funds |
  | `4000 0000 0000 0119` | Processor error (HTTP 502) |

- **Order tracking:** orders move CONFIRMED → PROCESSING (2 min) → SHIPPED (10 min) → OUT FOR DELIVERY (30 min) → DELIVERED (60 min). Express shipping is twice as fast. Orders can be cancelled until they ship.
- **Try Sparky:**
  - *"I need noise-cancelling headphones under $400"*
  - *"Compare the Nova X1 and X1 Pro"*
  - *"Add two Pulse Buds Pro 2 to my cart"*
  - *"What's my total with express shipping?"*
  - *"Where is order VV-XXXXXXXX? my email is …"*
  - *"Cancel it"*

## Observability

Everything uses **standard OpenTelemetry**. The Python services run under `opentelemetry-instrument`, configured purely through `OTEL_*` environment variables. Strands emits GenAI semantic-convention spans for agent cycles, model calls, and tool calls.

| Signal | Where |
|---|---|
| Traces | **X-Ray / CloudWatch trace map** (`TraceMapUrl`). If the account has X-Ray *Transaction Search* enabled, spans are stored in the `aws/spans` log group; use **CloudWatch → Transaction Search** to find them, because only a sample is indexed as trace summaries. Searchable annotations: `voltverse.order.id`, `voltverse.checkout.outcome`, `voltverse.payment.status`, `session.id`. |
| Metrics | CloudWatch namespace **`VoltVerse`** and the `DashboardUrl` dashboard: HTTP latency per service, checkout outcomes, revenue, payment outcomes, agent latency, tool usage, and `gen_ai.*` token usage |
| Logs | `ApplicationLogGroup`. JSON logs carry `trace_id` / `span_id` for log↔trace correlation. |
| Anything else | Set `ExternalOtlpEndpoint`, or edit `k8s/10-otel-collector.yaml` |

**Fault injection** for exercises is in the `voltverse-chaos` ConfigMap:

```bash
kubectl -n voltverse patch configmap voltverse-chaos -p '{"data":{"PAYMENT_FAILURE_RATE":"0.3","PAYMENT_LATENCY_MS":"1500"}}'
kubectl -n voltverse rollout restart deploy/payment
```

The ConfigMap also has `CHAOS_LATENCY_MS` / `CHAOS_ERROR_RATE` (applied to every service) and `ORDER_TIMELINE_SCALE`. See [docs/workshop-guide.md](docs/workshop-guide.md) for guided labs.

---

## Local development

```bash
# Whole stack on http://127.0.0.1:8080 (Docker or Finch). Only the storefront port is published, on loopback.
docker compose -f local/docker-compose.yml up --build      # or: finch compose ...
# Sparky locally needs Bedrock credentials from ~/.aws (set AWS_PROFILE).

# Integration tests: every service runs on 127.0.0.1 against moto-emulated DynamoDB. No AWS account needed.
python3.12 -m venv .venv && . .venv/bin/activate && pip install -r tests/requirements.txt
pytest -q tests

# Frontend hot reload (proxies /api to the compose stack)
cd services/storefront && npm install && npm run dev
```

## Updating, redeploying and cleaning up

- **Redeploy after code changes:**
  - Push to the branch (or re-upload the zip).
  - Then either update the stack with a new `DeployVersion`, or click **Start build** on the `DeployerProject` CodeBuild project.
- **Delete:** `aws cloudformation delete-stack --stack-name voltverse`. A destroy build removes the Kubernetes workloads first, then CloudFormation deletes everything else, including ECR images, tables, and log groups. Deletion takes about 20 minutes, because CloudFront has to be disabled first.

## Security notes

- **Public by design:** the storefront is an anonymous retail site, like any web shop. Customer-specific data is protected:
  - Carts are keyed by unguessable UUIDs.
  - Orders require the order number **and** the checkout email.
  - Lookups don't reveal whether an order exists.

  Set `AllowedViewerCidrs` to restrict the whole store to your workshop network.
- **Single entry point:** CloudFront (HTTPS) → VPC origin → **internal** ALB, whose security group admits only CloudFront's VPC-origin security group. Nothing in the VPC has a public IP except the NAT gateway.
- **Internal-only APIs:** nginx exposes an allow-list of routes. Payment, restock, reservation, and order-creation APIs are reachable only inside the cluster.
- **EKS API endpoint:** private by default. The deployer runs inside the VPC.
- **Pods:**
  - Each workload has its own least-privilege IAM role through EKS Pod Identity.
  - Pods run as non-root with read-only root filesystems, all capabilities dropped, and the `restricted` Pod Security Standard.
- **Data at rest:** DynamoDB tables are encrypted. ECR images are scanned on push. The payment service never stores card numbers or CVCs.
- **No authentication:** this is a demo store with no customer accounts. Don't put real personal or payment data in it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Stack fails at `Deployment` | Open the CodeBuild project `<env>-deployer` and read the latest build log. Common causes: a wrong `SourceRepoUrl` or branch, or a private repo (use the S3 source instead). |
| Sparky says it's "taking a short break" | Bedrock access isn't enabled, or the model ID isn't available in the region. Check the `assistant` logs in `ApplicationLogGroup` (search for `agent invocation failed`). |
| HTTP 429 from the store | nginx per-viewer rate limits (`services/storefront/nginx.conf`) or the WAF assistant cap |
| `kubectl` times out | The EKS endpoint is private by default. Set `EnablePublicClusterEndpoint=true` (plus `PublicEndpointCidrs`), or use a CloudShell VPC environment in the stack's VPC. |

## Repository layout

```
cloudformation/voltverse.yaml   # the one template to launch everything
k8s/                            # Kubernetes manifests (rendered by scripts/render.py at deploy time)
services/
  common/                       # shared FastAPI app factory, logging, DynamoDB + HTTP helpers, requirements
  python.Dockerfile             # one image definition for all Python services
  catalog/ inventory/ cart/ checkout/ payment/ orders/ assistant/
  storefront/                   # React app, nginx gateway config, Dockerfile
  loadgen/                      # synthetic traffic script
scripts/                        # deploy/destroy (CodeBuild), seeding, rendering, packaging, launch helpers
data/products.json              # catalog seed data
local/                          # docker-compose stack for local development
tests/                          # end-to-end integration tests
docs/                           # architecture, workshop guide, Workshop Studio notes
```

## License

MIT-0. See [LICENSE](LICENSE). Product names and brands in the catalog are fictional.
