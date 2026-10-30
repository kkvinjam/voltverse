# Change history

## 2026-10-24: CloudWatch Application Signals instrumentation and continuous traffic generators

### Why

The microservice containers weren't instrumented for CloudWatch Application Signals or Container Insights:
- The images bundled the ADOT SDK and exported only to the in-cluster collector.
- `OTEL_AWS_APPLICATION_SIGNALS_ENABLED` was set to `false`.
- There was no CloudWatch agent or add-on in the cluster.

This change switches to the pattern used by the [One Observability Demo](https://github.com/aws-samples/one-observability-demo) (`src/cdk/lib/constructs/eks.ts`). The stack installs the Amazon CloudWatch Observability EKS add-on, and pod annotations make its operator inject the ADOT SDK.

It also adds continuous traffic for the website and the Sparky AI agent. Everything is still deployed by `cloudformation/voltverse.yaml`.

### CloudFormation (`cloudformation/voltverse.yaml`)

- New resources:
  - `CloudWatchAgentRole`: Pod Identity role with `CloudWatchAgentServerPolicy` and `AWSXrayWriteOnlyAccess`.
  - `CloudWatchObservabilityAddon` (`amazon-cloudwatch-observability`): Pod Identity association for the `cloudwatch-agent` service account. Its agent config turns on Application Signals and Container Insights with enhanced observability.
  - Container Insights log groups (`/aws/containerinsights/<env>-eks/{application,dataplane,host,performance}`), created with a retention period and deleted with the stack.
- New parameters:
  - `ShopperTrafficPerMinute` (default 12)
  - `AgentTrafficTurnsPerMinute` (default 2, max 10)

  `0` disables either generator. Both are passed to CodeBuild and to the `Deployment` custom resource, so changing them redeploys.
- `Deployment` now depends on the add-on, so the injection webhook exists before any pods are created.
- The deployer role can call `application-signals:StartDiscovery` and create the Application Signals service-linked role.
- Dashboard: the per-service latency and request widgets now use `ApplicationSignals` metrics (`Environment=eks:<cluster>/voltverse`), and a faults widget was added. The header links to Application Signals and Container Insights.
- New outputs: `ApplicationSignalsUrl`, `ContainerInsightsUrl`, `ContainerLogGroup`.

### Kubernetes manifests (`k8s/`)

- `00-namespaces.yaml`: the `voltverse` namespace now enforces the `baseline` Pod Security Standard and warns/audits on `restricted`. The operator's injected init container has no container `securityContext`, so `restricted` would reject the pods. Every VoltVerse container still sets the restricted controls itself.
- `20-config.yaml`: the OTel variables moved out of `voltverse-config` into two ConfigMaps:
  - `voltverse-otel`, for injected services: sets `OTEL_AWS_APPLICATION_SIGNALS_ENABLED=true` explicitly and sends business metrics and OTLP logs to the collector. Traces and Application Signals metrics go to the CloudWatch agent, configured by the operator.
  - `sparky-otel`, for Sparky's bundled SDK.
- `30-services.yaml`:
  - catalog, inventory, cart, checkout, payment and orders have `instrumentation.opentelemetry.io/inject-python: "true"`.
  - Their resource attributes are reduced so Application Signals uses the EKS environment.
  - Sparky has `inject-python: "false"`, because it keeps its bundled SDK for Bedrock AgentCore GenAI observability.
- `50-loadgen.yaml`:
  - The inline script is removed; the deployer creates the ConfigMap from `services/loadgen/loadgen.py`.
  - The pod is annotated for injection, and its rate and replica count come from the parameter.
- `60-sparky-traffic.yaml` (new): a continuous Sparky conversation generator. It runs `scripts/sparky-traffic.py` against `http://storefront.voltverse`, mounted from a ConfigMap, and is not instrumented.
- `10-otel-collector.yaml`: header comment updated for the new telemetry split. The pipelines are unchanged.

### Images (`services/`)

- `common/requirements.txt`: `aws-opentelemetry-distro` replaced by `opentelemetry-api==1.44.0`. The injected SDK comes first on `PYTHONPATH`, so it takes precedence.
- `common/requirements-otel-sdk.txt` (new): `aws-opentelemetry-distro==0.21.0`, which `assistant/requirements.txt` includes.
- `python.Dockerfile`:
  - New `BUNDLE_OTEL_SDK` build arg (default `false`).
  - Requirements are copied in a layout where the relative `-r` includes resolve.
  - The image now starts with `start.sh`.
- `common/start.sh` (new): runs plain `uvicorn` when the SDK was injected, and `opentelemetry-instrument uvicorn` when the SDK is bundled.
- `loadgen/loadgen.py`: each shopper first loads `/` and its `/assets/*` bundles.

### Scripts

- `scripts/codebuild-deploy.sh`:
  - Enables Application Signals (best effort).
  - Waits for the add-on's deployments and webhook.
  - Creates the `loadgen-script` and `sparky-traffic-script` ConfigMaps from their files and uses the script hashes to roll the pods when the scripts change.
  - Computes the generator replica counts from the parameters.
  - Waits for all rollouts.
  - Checks that each annotated pod has the `opentelemetry-auto-instrumentation-python` init container, and restarts the deployment once if it doesn't.
- `scripts/sparky-traffic.py`:
  - `--minutes 0` runs forever, printing a progress report every 20 conversations.
  - Errors in a conversation no longer stop the run.
  - The latency history is bounded.

### Local and docs

- `local/docker-compose.yml`: builds with `BUNDLE_OTEL_SDK=true`, because there is no add-on locally.
- `README.md`, `docs/architecture.md` and `docs/workshop-guide.md` updated: observability design, parameters, outputs, traffic generators, troubleshooting and security notes.

### Verification

- `cfn-lint` passes. The manifests render with `scripts/render.py`, and `bash -n` passes on the deploy script.
- Integration tests: `pytest tests`, 7 passed.
- Built the images with Finch:
  - The catalog image has no SDK. Run with the add-on's injected SDK (`adot-autoinstrumentation-python:v0.21.0`, copied the way the init container does as uid 10001, read-only root filesystem), it emitted server spans with `aws.local.service=catalog` and `aws.local.operation="GET /health"`.
  - The assistant image still starts under `opentelemetry-instrument`.
- Not yet deployed to an AWS account.

### Known trade-offs and follow-ups

- `ExternalOtlpEndpoint` now receives only metrics and logs. Traces go to the CloudWatch agent.
- The injected services no longer opt in to the stable HTTP semantic conventions, so their OTel HTTP metrics use the older names. Per-service latency, requests and faults now come from Application Signals.
- `sparky-traffic` calls Amazon Bedrock continuously, about 2,900 turns a day at the default rate. Set `AgentTrafficTurnsPerMinute=0` to stop it.
- The architecture images in `docs/images/` haven't been regenerated. The Mermaid diagrams in `docs/architecture.md` are updated.

## 2026-10-24: Deployed to account 108782060132 (us-east-1), plus loadgen auto-restock

### Stack update

Stack `voltverse` was updated in account 108782060132 (profile `amo-log-acct`) with change set `appsignals-traffic-20261024`:
- `DeployVersion` 7 → 8.
- `ShopperTrafficPerMinute` = 12, `AgentTrafficTurnsPerMinute` = 2.
- All other parameters kept their previous values.

Files in `s3://voltverse-source-108782060132-us-east-1/`:
- Source zip: `voltverse-source.zip`, built from the working tree.
- Previous zip, kept for rollback: `voltverse-source.backup-20261022.zip`.
- Template: `cloudformation/voltverse-20261024-appsignals.yaml`.

Result:
- The stack reached `UPDATE_COMPLETE`.
- The `amazon-cloudwatch-observability` add-on v6.7.0-eksbuild.1 is `ACTIVE`.
- The deployer reported "All instrumented" for the six services and loadgen.
- The services appeared in Application Signals under `eks:voltverse-eks/voltverse`.

### Fix: the store had sold out

After the update, every loadgen checkout returned HTTP 400 (`empty_cart`), because adding to the cart returned 409 (`insufficient_stock`). 35 of the 36 SKUs had zero stock. The loadgen had been placing orders since 2026-10-22, and `seed_data.py` deliberately never resets stock on a redeploy. So `payment` and `orders` received no traffic, and Sparky's order scenarios failed.

- `services/loadgen/loadgen.py`: the loadgen now also plays the warehouse. Every `RESTOCK_INTERVAL_S` (300 s) it reads stock through the gateway and tops up every product below `RESTOCK_BELOW` (20) to its `initialStock`. It does this through the inventory service's internal `POST /restock` API (`INVENTORY_URL=http://inventory:8080`).
- `scripts/codebuild-deploy.sh`: the `loadgen-script` ConfigMap now also contains `products.json` (from `data/products.json`), and the script hash covers both files.
- `k8s/50-loadgen.yaml`: new environment variables `INVENTORY_URL`, `RESTOCK_INTERVAL_S` and `RESTOCK_BELOW`.
- Tested locally against mock gateway and inventory endpoints (18 low SKUs topped up to their initial stock). Deployed by re-running the `voltverse-deployer` CodeBuild project with an updated source zip.

## 2026-10-28: Standalone Sparky (embedded store, AgentCore Runtime, simulated shoppers)

### Why

Sparky's tools call the store's microservices, so on its own it could only answer policy questions. This change lets Sparky run independently and hold meaningful conversations, without changing how it runs on EKS.

### Changes

- `services/assistant/app/embedded.py` (new): with `SPARKY_BACKEND=embedded`, Sparky starts the real catalog, inventory, cart, checkout, payment and orders services as local uvicorn processes, against an in-memory moto DynamoDB.
  - Seeding: the catalog, plus 7 demo orders placed through checkout and backdated into each fulfilment state (confirmed, processing, shipped, out for delivery, delivered and returnable, delivered with the return window closed, cancelled and refunded).
  - The child services get dummy AWS credentials and have OTel disabled. The store is ready in about 3 seconds.
- `services/assistant/app/main.py`:
  - `SPARKY_BACKEND` switch, default `http`, which is unchanged.
  - Bedrock AgentCore Runtime contract: `GET /ping`, and `POST /invocations` (`prompt` / `messages` / `action: demo_data|reset`). Conversations are remembered per runtime session in a bounded LRU, and each session gets its own cart (uuid5 of the session id).
  - `/chat` and `/invocations` share `_invoke()`.
- `scripts/sparky_shopper.py` (new): an LLM shopper simulator (default model `global.anthropic.claude-haiku-5-5`).
  - 15 personas with goals. Order personas use the demo orders fetched in the same session.
  - Targets AgentCore Runtime (`--runtime-arn`) or the container (`--url`).
  - Works as a CLI and as a Lambda handler, and logs one JSON line per conversation.
- `services/sparky-standalone.Dockerfile`, `services/assistant/requirements-standalone.txt` (adds `moto[server]==5.2.4`), `services/sparky-shopper.Dockerfile` (Lambda image) and a root `.dockerignore` (allow-list for the builds that use the repository root as context).
- `cloudformation/sparky-standalone.yaml` (new):
  - ECR repository (empties on delete).
  - ARM64 CodeBuild project, run by a custom resource, that builds and smoke-tests the images (`scripts/codebuild-sparky-standalone.sh`, which also enables Transaction Search).
  - `AWS::BedrockAgentCore::Runtime` (public network mode, IAM role with Bedrock, ECR pull and telemetry permissions only).
  - Shopper Lambda (arm64 image), run by an EventBridge `rate(10 minutes)` rule (`ConversationsPer10Min`, 0 disables it).
- `tests/test_standalone.py` (new): the embedded store's demo order states and the tools against it, session memory in `/invocations`, and the shopper loop (no Bedrock calls).
- `README.md`: new "Run Sparky standalone" section.

### Effect on the EKS application

None by default:
- The EKS image doesn't install moto, keeps `SPARKY_BACKEND=http`, and never starts the embedded store.
- The new `/ping` and `/invocations` routes aren't exposed by the nginx gateway.
- The standalone stack uses separate names, roles, repositories and logs. It does share the account's Bedrock quotas.

### Verification

- `pytest tests`: 11 passed (7 existing, 4 new). `cfn-lint` passes on both templates.
- The standalone arm64 image was built with Finch and is ready in about 3 seconds with 7 demo orders.
- Real Bedrock test (us-east-1, Opus 5.5 for Sparky, Haiku 5.5 for the shopper), all with `goal_reached`:
  - `cart_builder`: 4 turns, 6 tools.
  - `track_order_forgot_email`: Sparky asked for the email before looking up the order.
  - `return_item`: Sparky confirmed before starting the return, issued an RMA and reported the refund to the card.
- The shopper Lambda image imports its handler, and its boto3 1.43.117 includes the `bedrock-agentcore` client.
- Not yet deployed to AWS.

### Observed during testing

Sparky once had to add an item to the cart twice ("didn't save the first time"). The likely cause is concurrent `add_to_cart` tool calls racing in the cart service's read-modify-write. This exists on EKS too; it hasn't been fixed.
