# AWS Lambda image for the shopper simulator (scripts/sparky_shopper.py), used by cloudformation/sparky-standalone.yaml.
# Build from the REPOSITORY ROOT:  docker build -f services/sparky-shopper.Dockerfile -t sparky-shopper .
FROM public.ecr.aws/lambda/python:3.13
# Pinned boto3 with the bedrock-agentcore client (same version as the services)
RUN pip install --no-cache-dir boto3==1.43.117
COPY scripts/sparky_shopper.py ${LAMBDA_TASK_ROOT}/
CMD ["sparky_shopper.handler"]
