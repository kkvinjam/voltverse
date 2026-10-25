#!/usr/bin/env bash
# Runs inside the CodeBuild project created by cloudformation/voltverse.yaml.
# Builds all container images, pushes them to ECR, seeds DynamoDB and applies the Kubernetes manifests.
# Every input arrives as an environment variable set on the CodeBuild project.
set -euo pipefail

: "${AWS_REGION:?}" "${ACCOUNT_ID:?}" "${CLUSTER_NAME:?}" "${REPO_PREFIX:?}" "${TARGET_GROUP_ARN:?}"
: "${PRODUCTS_TABLE:?}" "${INVENTORY_TABLE:?}" "${CARTS_TABLE:?}" "${ORDERS_TABLE:?}" "${PAYMENTS_TABLE:?}"
: "${BEDROCK_MODEL_ID:?}" "${APP_LOG_GROUP:?}" "${METRICS_LOG_GROUP:?}" "${ENVIRONMENT_NAME:?}"
: "${VPC_ID:?}" "${ALB_SECURITY_GROUP_ID:?}" "${AGENT_LOG_GROUP:?}"

# Traffic generators (0 disables one). Agent traffic calls Bedrock on every turn.
export SHOPPER_TRAFFIC_PER_MINUTE="${SHOPPER_TRAFFIC_PER_MINUTE:-12}"
export AGENT_TRAFFIC_TURNS_PER_MINUTE="${AGENT_TRAFFIC_TURNS_PER_MINUTE:-2}"
replicas_for() { python3 -c 'import sys; print(1 if float(sys.argv[1]) > 0 else 0)' "$1"; }
export LOADGEN_REPLICAS="$(replicas_for "$SHOPPER_TRAFFIC_PER_MINUTE")"
export AGENT_TRAFFIC_REPLICAS="$(replicas_for "$AGENT_TRAFFIC_TURNS_PER_MINUTE")"

export ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
export IMAGE_TAG="${IMAGE_TAG:-b${CODEBUILD_BUILD_NUMBER:-$(date +%s)}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "Checking CloudWatch Transaction Search"
# Spans sent to the CloudWatch OTLP traces endpoint are stored in the aws/spans log group, which needs
# Transaction Search (account + region level). Only turned on when it is off; never turned off by this stack.
DEST="$(aws xray get-trace-segment-destination --query Destination --output text)"
if [ "$DEST" = "CloudWatchLogs" ]; then
  echo "Transaction Search already enabled"
elif [ "${ENABLE_TRANSACTION_SEARCH:-true}" = "true" ]; then
  aws logs put-resource-policy --policy-name "TransactionSearchXRayAccess" --policy-document "$(cat <<JSON
{"Version": "2012-10-17", "Statement": [{"Sid": "TransactionSearchXRayAccess", "Effect": "Allow",
  "Principal": {"Service": "xray.amazonaws.com"}, "Action": "logs:PutLogEvents",
  "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:aws/spans:*",
               "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/application-signals/data:*"],
  "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:xray:${AWS_REGION}:${ACCOUNT_ID}:*"},
                "StringEquals": {"aws:SourceAccount": "${ACCOUNT_ID}"}}}]}
JSON
)" >/dev/null
  aws xray update-trace-segment-destination --destination CloudWatchLogs >/dev/null
  echo "Transaction Search enabled (spans -> aws/spans)"
else
  echo "WARNING: Transaction Search is off and EnableTransactionSearch=false - traces sent to the CloudWatch OTLP endpoint will be rejected"
fi

log "Enabling CloudWatch Application Signals"
# Account + region level and idempotent: creates the Application Signals service-linked role if needed.
if aws application-signals start-discovery; then
  echo "Application Signals discovery enabled"
else
  echo "WARNING: could not enable Application Signals - enable it once in the CloudWatch console (Application Signals > Services)"
fi

log "Allowing the CloudFront VPC origin to reach the internal ALB"
# CloudFront creates this security group in our VPC when the VPC origin is created.
CF_SG="$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" \
  "Name=group-name,Values=CloudFront-VPCOrigins-Service-SG" --query 'SecurityGroups[0].GroupId' --output text)"
[ -n "$CF_SG" ] && [ "$CF_SG" != "None" ] || { echo "CloudFront-VPCOrigins-Service-SG not found in ${VPC_ID}"; exit 1; }
if ! out="$(aws ec2 authorize-security-group-ingress --group-id "$ALB_SECURITY_GROUP_ID" --ip-permissions \
    "IpProtocol=tcp,FromPort=80,ToPort=80,UserIdGroupPairs=[{GroupId=${CF_SG},Description=CloudFront VPC origin}]" 2>&1)"; then
  grep -q InvalidPermission.Duplicate <<<"$out" || { echo "$out"; exit 1; }
fi
echo "ALB ${ALB_SECURITY_GROUP_ID} accepts tcp/80 from ${CF_SG}"

log "Installing kubectl matching the cluster version"
K8S_MINOR="$(aws eks describe-cluster --name "$CLUSTER_NAME" --query cluster.version --output text)"
KUBECTL_VERSION="$(curl -fsSL "https://dl.k8s.io/release/stable-${K8S_MINOR}.txt")"
curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
chmod +x /usr/local/bin/kubectl
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"
kubectl version

log "Building and pushing images (tag ${IMAGE_TAG})"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
build_push() {
  local name="$1"; shift
  local image="${ECR_REGISTRY}/${REPO_PREFIX}/${name}:${IMAGE_TAG}"
  docker build --quiet -t "$image" "$@"
  docker push --quiet "$image"
}
for svc in catalog inventory cart checkout payment orders assistant; do
  build_push "$svc" -f services/python.Dockerfile --build-arg "SERVICE=${svc}" services
done
build_push storefront services/storefront

log "Seeding DynamoDB"
python3 -c "import boto3" 2>/dev/null || python3 -m pip install --quiet boto3
python3 scripts/seed_data.py --products-table "$PRODUCTS_TABLE" --inventory-table "$INVENTORY_TABLE" --region "$AWS_REGION"

log "Waiting for the CloudWatch Observability add-on (Application Signals auto-instrumentation webhook)"
# Pods are only instrumented if the add-on's operator is up when they are created.
for _ in $(seq 1 60); do
  [ -n "$(kubectl -n amazon-cloudwatch get deploy -o name 2>/dev/null)" ] && break
  sleep 10
done
kubectl -n amazon-cloudwatch wait --for=condition=Available deploy --all --timeout=15m
kubectl get mutatingwebhookconfigurations -o name | grep -i cloudwatch \
  || { echo "CloudWatch Observability add-on webhook not found - check the add-on in the EKS console"; exit 1; }

log "Applying Kubernetes manifests"
# Script ConfigMaps come straight from the files (no ${VAR} rendering); their hashes roll the pods on change.
export LOADGEN_SCRIPT_HASH="$(cat services/loadgen/loadgen.py data/products.json | sha256sum | cut -c1-16)"
export SPARKY_TRAFFIC_SCRIPT_HASH="$(sha256sum scripts/sparky-traffic.py | cut -c1-16)"
python3 scripts/render.py k8s/*.yaml > /tmp/voltverse.yaml
kubectl apply -f k8s/00-namespaces.yaml
kubectl -n voltverse create configmap loadgen-script --from-file=loadgen.py=services/loadgen/loadgen.py \
  --from-file=products.json=data/products.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n voltverse create configmap sparky-traffic-script --from-file=sparky-traffic.py=scripts/sparky-traffic.py \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f /tmp/voltverse.yaml

INSTRUMENTED="catalog inventory cart checkout payment orders loadgen"
log "Waiting for rollouts"
kubectl -n observability rollout status deploy/otel-collector --timeout=15m
for d in catalog inventory cart checkout payment orders assistant storefront loadgen sparky-traffic; do
  kubectl -n voltverse rollout status "deploy/${d}" --timeout=15m
done

log "Verifying Application Signals auto-instrumentation"
# The injected init container is the proof. If pods raced the webhook, restart them once.
missing=""
for d in $INSTRUMENTED; do
  # One line per running pod: "<pod> <init containers...>"
  pods="$(kubectl -n voltverse get pods -l "app=${d}" --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.initContainers[*].name}{"\n"}{end}')"
  if [ -n "$pods" ] && grep -qv opentelemetry-auto-instrumentation-python <<<"$pods"; then missing="$missing $d"; fi
done
if [ -n "$missing" ]; then
  echo "Not instrumented yet:${missing} - restarting"
  for d in $missing; do kubectl -n voltverse rollout restart "deploy/${d}"; done
  for d in $missing; do kubectl -n voltverse rollout status "deploy/${d}" --timeout=15m; done
else
  echo "All instrumented: ${INSTRUMENTED}"
fi

log "Waiting for the storefront to become healthy behind the load balancer"
for _ in $(seq 1 60); do
  healthy="$(aws elbv2 describe-target-health --target-group-arn "$TARGET_GROUP_ARN" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text)"
  if [ "${healthy:-0}" -ge 1 ]; then echo "Healthy targets: ${healthy}"; break; fi
  sleep 10
done
[ "${healthy:-0}" -ge 1 ] || { echo "No healthy targets after 10 minutes"; kubectl -n voltverse get pods -o wide; exit 1; }

kubectl -n voltverse get pods -o wide
log "Deployment complete"
