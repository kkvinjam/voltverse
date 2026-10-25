# Upstream review: local `main` (5cdfa54) → `origin/main` (ed32b80, tag v1.0.0)

## Summary

- Local `main` is **2 commits behind** `origin/main`. It is not ahead, and the working tree is clean, so `git pull` will fast-forward.
  - `96f4e78`: "Application Signals via CloudWatch Observability add-on; continuous shopper and Sparky traffic"
  - `ed32b80`: merge of PR #1 (`appsignals-traffic-generators`)
- Size: 21 files, +663/-211.
- The change does two things:
  1. **Observability re-architecture.** Before, each image bundled the ADOT SDK under `opentelemetry-instrument` and sent everything to the in-cluster ADOT collector, with Application Signals turned off. Now the stack installs the **Amazon CloudWatch Observability EKS add-on**. Its operator injects the ADOT Python SDK into annotated pods. Traces and Application Signals metrics go to the add-on's CloudWatch agent. The ADOT collector stays, but only handles business metrics (EMF), OTLP logs, and Sparky's metrics. Sparky still bundles its own SDK and opts out of injection.
  2. **Continuous traffic generators.** `loadgen` is now parameterized, adds page views, and restocks inventory. A new `sparky-traffic` pod calls the AI assistant (Bedrock) around the clock.
- **Cost impact.** The new pieces are:
  - the add-on (CloudWatch agent and Fluent Bit DaemonSets)
  - Container Insights *enhanced* observability
  - Application Signals
  - 4 new log groups
  - a pod that calls Bedrock continuously: default 2 turns/min, about 2,880 turns/day per stack, with Opus as the default model
- **Security posture change.** The `voltverse` namespace drops from enforced PSS `restricted` to `baseline`.
- No secrets or new exposed ports were introduced. IAM additions are mostly AWS-managed policies plus a few well-scoped statements.
- **No blocking bugs found.** Main review concerns:
  - the PSS downgrade
  - the unpinned add-on version
  - doubled log ingestion
  - always-on Bedrock traffic bypassing the WAF cap
  - CloudFormation update failures on stacks or accounts where the add-on or Container Insights log groups already exist

---

## 1. What changed, by theme

### CloudFormation / IAM / EKS add-on (`cloudformation/voltverse.yaml`)
- **New parameters** `ShopperTrafficPerMinute` (default 12, 0–600) and `AgentTrafficTurnsPerMinute` (default 2, 0–10). Both have a new "Traffic generators" parameter group. They are passed to CodeBuild env (`SHOPPER_TRAFFIC_PER_MINUTE`, `AGENT_TRAFFIC_TURNS_PER_MINUTE`) and to the `Deployment` custom resource, so changing them triggers a redeploy.
- **`CloudWatchAgentRole`**: Pod Identity role (`pods.eks.amazonaws.com`, `aws:SourceAccount` condition) with managed policies `CloudWatchAgentServerPolicy` and `AWSXrayWriteOnlyAccess`.
- **4 log groups** `/aws/containerinsights/${EnvironmentName}-eks/{application,dataplane,host,performance}`. They use `RetentionInDays: !Ref LogRetentionDays` and `DeletionPolicy: Delete`.
- **`CloudWatchObservabilityAddon`** (`AWS::EKS::Addon`, `amazon-cloudwatch-observability`):
  - `ResolveConflicts: OVERWRITE`, with a Pod Identity association for SA `cloudwatch-agent`.
  - **No `AddonVersion`**, so it uses the default/latest at create time.
  - `ConfigurationValues` *replaces* the default agent config. It enables `application_signals` (hosted_in cluster), `kubernetes` with `enhanced_container_insights: true`, and Application Signals traces.
- **Deployer role**:
  - `application-signals:StartDiscovery` on `*`
  - `iam:CreateServiceLinkedRole` scoped to the `application-signals.cloudwatch.amazonaws.com` SLR path, with an `iam:AWSServiceName` condition
- **`Deployment`** now `DependsOn: CloudWatchObservabilityAddon`, so the webhook exists before pods are created.
- **Dashboard**:
  - The latency and request widgets switched from `VoltVerse/http.server.request.duration` to `ApplicationSignals` `Latency`. Requests use `SampleCount` on `Environment=eks:${Cluster}/voltverse`.
  - New `Fault` widget.
  - Sparky was removed from these widgets because it isn't an Application Signals service.
  - New header links.
- **New outputs**: `ApplicationSignalsUrl`, `ContainerInsightsUrl`, `ContainerLogGroup`.

### Kubernetes manifests (`k8s/`)
- `00-namespaces.yaml`: `pod-security.kubernetes.io/enforce: restricted` → `baseline`, plus `warn`/`audit: restricted`. The reason given: the injected init container has no `securityContext`.
- `10-otel-collector.yaml`: comments only. The image is still `aws-otel-collector:v0.43.3`. The pipeline is unchanged, but traces are now only used by workloads that explicitly send OTLP traces there.
- `20-config.yaml`: the OTel vars were removed from `voltverse-config` and split into two ConfigMaps:
  - `voltverse-otel` (injected services): `OTEL_AWS_APPLICATION_SIGNALS_ENABLED=true`. Metrics and logs exporters point at `otel-collector.observability:4318`. Also sets `parentbased_always_on` sampling, `xray` propagator, excluded health URLs, and disables urllib/urllib3. Traces exporter and endpoint are left to the operator.
  - `sparky-otel`: the previous settings (aws_distro/configurator, Application Signals `false`). `OTEL_EXPORTER_OTLP_ENDPOINT` was removed; Sparky sets per-signal endpoints in 30-services.
  - `OTEL_SEMCONV_STABILITY_OPT_IN` is no longer set for the microservices. That is consistent with the dashboard no longer using `http.server.request.duration`.
- `30-services.yaml`:
  - catalog, inventory, cart, checkout, payment and orders get the annotation `instrumentation.opentelemetry.io/inject-python: "true"` and `envFrom voltverse-otel`.
  - `OTEL_RESOURCE_ATTRIBUTES` is reduced to `service.namespace,service.version`. `deployment.environment.name` and the `POD_NAME`/`NODE_NAME` k8s.* attributes were removed, so the operator adds them and Application Signals uses `eks:<cluster>/voltverse`.
  - Assistant/Sparky: `inject-python: "false"` and `envFrom sparky-otel`. It keeps its explicit traces/logs/metrics endpoints.
- `50-loadgen.yaml`:
  - The inline `loadgen-script` ConfigMap (≈80 lines of duplicated Python) was removed. The deployer now creates it from `services/loadgen/loadgen.py` plus `data/products.json`.
  - `replicas: ${LOADGEN_REPLICAS}`, injection annotation, and a `script-hash` annotation that rolls pods when the script changes.
  - `command` no longer uses `opentelemetry-instrument`.
  - New env: `SHOPPERS_PER_MINUTE=${SHOPPER_TRAFFIC_PER_MINUTE}`, `INVENTORY_URL`, `RESTOCK_INTERVAL_S=300`, `RESTOCK_BELOW=20`.
  - New `/tmp` emptyDir.
- `60-sparky-traffic.yaml` (new): Deployment `sparky-traffic` with `replicas: ${AGENT_TRAFFIC_REPLICAS}`. It reuses the catalog image and runs `scripts/sparky-traffic.py http://storefront.voltverse --minutes 0 --rate ${AGENT_TRAFFIC_TURNS_PER_MINUTE} --concurrency 1`. It is non-root, read-only rootfs, drops all capabilities, has no SA token, and is not injected.

### Telemetry / instrumentation (services)
- `services/common/requirements.txt`: `aws-opentelemetry-distro==0.21.0` → `opentelemetry-api==1.44.0` only.
- `services/common/requirements-otel-sdk.txt` (new): `aws-opentelemetry-distro==0.21.0`. Used by Sparky (`services/assistant/requirements.txt` adds `-r ../common/requirements-otel-sdk.txt`) and by local builds. I checked PyPI: distro 0.21.0 pins `opentelemetry-api==1.44.0`, so there's no resolver conflict.
- `services/common/start.sh` (new):
  - If `PYTHONPATH` contains `otel-auto-instrumentation-python` (operator injected), it runs plain `uvicorn`.
  - Else, if `opentelemetry-instrument` exists (bundled), it wraps uvicorn with it.
  - Else it runs uvicorn with no telemetry.
- `services/common/voltcommon/__init__.py`: docstring only.

### Traffic generators
- `services/loadgen/loadgen.py`:
  - `page_view()` fetches `/` and the `/assets/*` bundles.
  - New "warehouse" restock loop (`initial_stock()`, `restock()`). Every 300 s it reads stock through the gateway and POSTs **directly to `inventory:8080/restock`**, an internal endpoint the gateway blocks (`services/storefront/nginx.conf:2`). It tops products below 20 units back up to `initialStock` from products.json.
- `scripts/sparky-traffic.py`:
  - `--minutes 0` means run forever.
  - Latencies use a bounded `deque(maxlen=1000)`.
  - Per-conversation exceptions are caught (counted, then a 10 s sleep).
  - A periodic `report()` runs every 20 conversations, with `flush=True` for pod logs.

### Container build (`services/python.Dockerfile`)
- New `ARG BUNDLE_OTEL_SDK=false`. Requirements are copied into `/tmp/req/{common,service}` so the relative `-r ../common/...` resolves.
- The SDK is installed only when the arg is `true`.
- `CMD ["/srv/start.sh"]` replaces `opentelemetry-instrument uvicorn ...`.

### Deployer (`scripts/codebuild-deploy.sh`)
- Defaults for the traffic vars. A `replicas_for` helper sets replicas to 0 or 1.
- Runs `aws application-signals start-discovery`. This is non-fatal: on failure it only warns.
- Waits up to 10 min for deployments in `amazon-cloudwatch`, then `kubectl wait ... Available --timeout=15m`. It fails the build if no CloudWatch mutating webhook exists.
- Computes script hashes, applies `00-namespaces.yaml` first, then creates the two script ConfigMaps (`create --dry-run=client | apply`), then applies the rendered manifests.
- Rollout waits now include `loadgen` and `sparky-traffic`.
- New verification step: any Running pod of an instrumented deployment that lacks the `opentelemetry-auto-instrumentation-python` init container triggers one `rollout restart`.

### Local dev (`local/docker-compose.yml`)
- Every Python service build passes `BUNDLE_OTEL_SDK: "true"`. It is repeated per service because the per-service `build:` map replaces the anchor's.
- The env block is unchanged: it still sends everything to the local collector, with Application Signals off.

### Docs
- `README.md`: component table, deploy steps, parameters, outputs, Observability section, cost note, PSS note, and a troubleshooting row for "service missing from Application Signals".
- `change_history.md` (new, 121 lines): a detailed changelog for this release.
- `docs/architecture.md`: updated.
- `docs/workshop-guide.md`: one line changed.

## 2. Observability architecture: before vs after

| | Before (5cdfa54) | After (v1.0.0) |
|---|---|---|
| SDK in microservice images | bundled `aws-opentelemetry-distro`, `opentelemetry-instrument` | API only. SDK injected by the add-on operator (annotation) |
| Microservice traces | → ADOT collector → X-Ray OTLP endpoint | → CloudWatch agent (`cloudwatch-agent.amazon-cloudwatch:4316`) → X-Ray / Transaction Search |
| Application Signals | disabled (`false`), no agent | enabled. Services appear under `eks:<cluster>/voltverse` |
| Container Insights | none | enhanced observability, plus Fluent Bit container logs |
| Business metrics / logs | collector (EMF / OTLP logs) | unchanged path, via `voltverse-otel` per-signal endpoints |
| Sparky | bundled SDK, direct traces/logs, metrics via collector | same, now with injection explicitly disabled |
| Dashboard latency/requests | `VoltVerse` HTTP histogram | `ApplicationSignals` Latency/Fault |

## 3. New AWS resources, IAM, and cost

- **Resources**:
  - EKS add-on `amazon-cloudwatch-observability`, which runs an operator Deployment plus CloudWatch agent and Fluent Bit DaemonSets on every Auto Mode node
  - IAM role `CloudWatchAgentRole` and its Pod Identity association
  - 4 Container Insights log groups
  - an account-level Application Signals service-linked role, created by `StartDiscovery`
- **IAM**:
  - `CloudWatchAgentServerPolicy` (broad by design: `cloudwatch:PutMetricData`, `logs:*LogGroup/Stream/PutLogEvents` on `*`, some `ec2:Describe*`, `ssm:GetParameter` on `AmazonCloudWatch-*`)
  - `AWSXrayWriteOnlyAccess`
  - deployer: `application-signals:StartDiscovery` (`*`, which the action requires) and a scoped `iam:CreateServiceLinkedRole`
- **Cost drivers** (all continuous while the stack runs):
  - **sparky-traffic**: Bedrock calls 24/7. 2 turns/min ≈ 2,880 turns/day per stack, each a multi-tool agent loop on the default `global.anthropic.claude-opus-5-5`. This is likely the largest new cost, and it multiplies across workshop StackSets.
  - Container Insights *enhanced* observability: per-observation pricing.
  - Application Signals: per-signal pricing, with 100% trace sampling (`parentbased_always_on`).
  - Log ingestion: the microservices' logs now go to CloudWatch twice, as OTLP logs to `ApplicationLogGroup` and as Fluent Bit stdout to `/aws/containerinsights/.../application`.
  - Extra node capacity for the add-on DaemonSets.
  - loadgen: same default rate (12/min), but each shopper now also loads the SPA and its assets.
- README's cost estimate (`$0.45–0.70/hr`) wasn't revised for the add-on or Container Insights. Only a Bedrock note was added.

## 4. What you need to do differently after pulling

1. **Pull:** `git pull` (fast-forward, clean tree).
2. **Existing deployed stacks** must be updated with the new template; the k8s manifests alone are not enough. The add-on, role, and log groups come from CloudFormation, and the deployer now fails if the CloudWatch webhook is missing (`codebuild-deploy.sh`, "Waiting for the CloudWatch Observability add-on" block). A stack update also triggers the redeploy, because new parameters flow into the `Deployment` resource.
3. **Traffic now starts automatically**, including Bedrock traffic. Set `AgentTrafficTurnsPerMinute=0` (or `ShopperTrafficPerMinute=0`) on the stack to disable it, or run `kubectl -n voltverse scale deploy/sparky-traffic --replicas=0`. A manual scale is reverted on the next deploy.
4. **Local docker-compose:** rebuild the images with `docker compose -f local/docker-compose.yml up --build`. New images need `BUNDLE_OTEL_SDK=true`, which compose passes. If you build `python.Dockerfile` yourself without `--build-arg BUNDLE_OTEL_SDK=true`, the service starts with **no telemetry** and no error (`start.sh` falls through to plain uvicorn).
5. **Env var moves:** if you customized OTel vars in `voltverse-config`, they now live in `voltverse-otel` (services) or `sparky-otel` (assistant). `deployment.environment.name` is no longer set on the microservices.
6. **loadgen script location:** edit `services/loadgen/loadgen.py`. The inline copy in `k8s/50-loadgen.yaml` is gone. Applying `k8s/50-loadgen.yaml` by hand without the deployer's `kubectl create configmap loadgen-script ...` leaves the pod without its script.
7. **Pod Security:** the namespace now admits baseline-level pods, so any custom workloads you add are no longer rejected for violating `restricted`, only warned.

## 5. Review concerns

| # | Severity | Concern | Where | Recommendation |
|---|---|---|---|---|
| 1 | Medium (security) | Namespace PSS enforcement lowered from `restricted` to `baseline` for all `voltverse` pods, to accommodate one injected init container. | `k8s/00-namespaces.yaml` (enforce label) | Accept it with the warn/audit labels as documented, or check whether the current add-on version lets you set the init container's securityContext and restore `restricted`. At minimum, add a CI or `kubectl --dry-run=server` check that VoltVerse manifests still pass `restricted`. |
| 2 | Medium (cost) | `sparky-traffic` calls Bedrock continuously by default (2 turns/min, Opus default). In-cluster traffic hits `storefront` directly and **bypasses the WAF `AssistantRequestsPer5Min` cap**. Only the nginx per-client limit (12/min) applies. | `k8s/60-sparky-traffic.yaml`, CFN `AgentTrafficTurnsPerMinute` | Consider defaulting `AgentTrafficTurnsPerMinute` to 0, or adding a run-time limit. Call out that the WAF cap doesn't cover it. Update the README cost estimate. |
| 3 | Low–Medium (reproducibility) | `AWS::EKS::Addon` has no `AddonVersion`. The injected ADOT SDK version, agent config schema, and webhook behavior float with the add-on. Everything else in the repo is pinned (`aws-otel-collector:v0.43.3`, Python deps `==`). | `cloudformation/voltverse.yaml`, `CloudWatchObservabilityAddon` | Pin `AddonVersion` (e.g. as a parameter with a tested default). |
| 4 | Low–Medium (upgrade/deploy failure) | Creating `/aws/containerinsights/<env>-eks/*` log groups or the add-on fails if they already exist (the add-on was installed manually, or Container Insights ran before on this cluster or env name). That fails the stack update. | CFN log groups and add-on resources | Document it in Troubleshooting, or import the existing resources. |
| 5 | Low (cost) | Duplicate log ingestion: OTLP logs → `ApplicationLogGroup`, and container stdout → Container Insights `application` group. | `20-config.yaml` (`OTEL_LOGS_EXPORTER`), add-on Fluent Bit | Decide on one path, or note it. Also revisit 100% sampling (`OTEL_TRACES_SAMPLER=parentbased_always_on`) for Application Signals and Transaction Search span costs. |
| 6 | Low (silent failure) | `start.sh` falls back to plain uvicorn when neither the injected SDK nor `opentelemetry-instrument` exists. A missed annotation or webhook race yields uninstrumented pods with no error. The deployer check mitigates this only at deploy time. | `services/common/start.sh` | Log a one-line warning in the fallback branch. |
| 7 | Low (deployer) | The instrumentation check lists `status.phase=Running` pods right after `rollout status`. Old, pre-annotation pods that are still terminating (still phase Running) can trigger an unnecessary `rollout restart` on the first upgrade. | `scripts/codebuild-deploy.sh`, "Verifying Application Signals auto-instrumentation" | Filter out pods with `deletionTimestamp`, or select by the current ReplicaSet's `pod-template-hash`. |
| 8 | Low (design) | loadgen writes directly to the internal `inventory /restock` endpoint. No NetworkPolicies exist in `k8s/`, so any pod in the cluster could already do this. The change makes the pattern explicit. | `services/loadgen/loadgen.py` (`restock()`), `k8s/50-loadgen.yaml` (`INVENTORY_URL`) | Acceptable for a demo. If hardening, add NetworkPolicies that limit `/restock` callers to orders and loadgen. |
| 9 | Low (correctness) | `restock()` falls back to the first 100 catalog products when `products.json` is missing. The seed has 36 products today, so it's fine. Restock requests go through the gateway's `/api/inventory/stock` and count against nginx per-client rate limits. | `services/loadgen/loadgen.py` | No action needed now. |
| 10 | Info | CloudWatch agent trust policy is scoped by `aws:SourceAccount` only, not by cluster ARN. The Pod Identity association already binds it to the `cloudwatch-agent` SA. | CFN `CloudWatchAgentRole` | Optional: add an `aws:SourceArn` condition on the cluster ARN. |
| 11 | Info | `application-signals start-discovery` failure is only a warning. The deploy then succeeds, but Application Signals stays empty. | `scripts/codebuild-deploy.sh` | Fine. The message tells the user what to do. |

Not found:
- no secrets or credentials added (the compose `AWS_ACCESS_KEY_ID: local` value is a DynamoDB Local dummy that predates this change)
- no new published ports or Services (compose still publishes only `127.0.0.1:8080`)
- no unpinned Python dependencies (`opentelemetry-api==1.44.0` matches `aws-opentelemetry-distro==0.21.0`'s pin)
- render placeholders: all new `${VAR}`s (`LOADGEN_REPLICAS`, `AGENT_TRAFFIC_REPLICAS`, `*_SCRIPT_HASH`, `SHOPPER_TRAFFIC_PER_MINUTE`, `AGENT_TRAFFIC_TURNS_PER_MINUTE`) are exported by the deployer before `render.py`, which fails loudly on missing values

## Conclusions

The upstream release is a coherent, well-documented switch to the One Observability Demo pattern (add-on + Application Signals auto-instrumentation), plus always-on traffic generators. It's safe to fast-forward locally. Before redeploying, especially across many workshop accounts:
- decide whether always-on Bedrock traffic should be on by default (#2)
- pin the add-on version (#3)
- check that no pre-existing Container Insights log groups or add-on will collide (#4)
- accept, or work around, the PSS `baseline` downgrade (#1)

What I verified: the full `git diff 5cdfa54 origin/main`, related upstream files (`scripts/render.py`, `nginx.conf`, `inventory /restock`, `data/products.json`), and the PyPI dependency pin. Not verified: runtime behavior, actual add-on default version or injected SDK version, and real cost figures.
