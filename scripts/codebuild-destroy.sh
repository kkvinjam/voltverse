#!/usr/bin/env bash
# Runs inside CodeBuild when the CloudFormation stack is deleted. Removes the Kubernetes workloads
# so the EKS Auto Mode controllers deregister targets and scale nodes down before the cluster,
# load balancer and VPC are deleted. Best effort: the stack deletion continues even if this fails.
set -uo pipefail
: "${AWS_REGION:?}" "${CLUSTER_NAME:?}"

# Revoke the CloudFront VPC origin rule so CloudFront can delete its security group during teardown.
if [ -n "${ALB_SECURITY_GROUP_ID:-}" ] && [ -n "${VPC_ID:-}" ]; then
  CF_SG="$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" \
    "Name=group-name,Values=CloudFront-VPCOrigins-Service-SG" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)"
  if [ -n "$CF_SG" ] && [ "$CF_SG" != "None" ]; then
    aws ec2 revoke-security-group-ingress --group-id "$ALB_SECURITY_GROUP_ID" --ip-permissions \
      "IpProtocol=tcp,FromPort=80,ToPort=80,UserIdGroupPairs=[{GroupId=${CF_SG}}]" >/dev/null 2>&1 \
      && echo "Revoked CloudFront VPC origin rule" || true
  fi
fi

if ! aws eks describe-cluster --name "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "Cluster ${CLUSTER_NAME} not found - nothing to clean up"
  exit 0
fi
K8S_MINOR="$(aws eks describe-cluster --name "$CLUSTER_NAME" --query cluster.version --output text)"
KUBECTL_VERSION="$(curl -fsSL "https://dl.k8s.io/release/stable-${K8S_MINOR}.txt")"
curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
chmod +x /usr/local/bin/kubectl
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"

kubectl -n voltverse delete targetgroupbinding --all --wait=true --timeout=5m || true
kubectl delete namespace voltverse observability --wait=true --timeout=10m || true
echo "Kubernetes resources removed"
