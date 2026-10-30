# Standalone Sparky: the agent plus an embedded copy of the store (SPARKY_BACKEND=embedded, see
# services/assistant/app/embedded.py). Runs anywhere with Bedrock access, including Amazon Bedrock AgentCore
# Runtime (which needs linux/arm64). Build from the REPOSITORY ROOT (it needs data/products.json):
#   docker build -f services/sparky-standalone.Dockerfile -t sparky-standalone .
#   docker buildx build --platform linux/arm64 -f services/sparky-standalone.Dockerfile -t sparky-standalone .
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SPARKY_BACKEND=embedded \
    EMBEDDED_SERVICES_DIR=/srv/services \
    EMBEDDED_COMMON_DIR=/srv/lib \
    EMBEDDED_PRODUCTS_FILE=/srv/data/products.json \
    HOME=/tmp

WORKDIR /srv
# Same relative layout as the repo, so "-r ../common/..." inside the requirements files resolves.
COPY services/common/requirements.txt services/common/requirements-otel-sdk.txt /tmp/req/common/
COPY services/assistant/requirements.txt services/assistant/requirements-standalone.txt /tmp/req/service/
RUN pip install -r /tmp/req/common/requirements.txt -r /tmp/req/service/requirements-standalone.txt

# Sparky (bundles the ADOT SDK, started by start.sh under opentelemetry-instrument)
COPY services/common/start.sh /srv/start.sh
COPY services/common/voltcommon ./voltcommon
COPY services/assistant/app ./app
# The embedded store: the real microservices, run as local processes by embedded.py. voltcommon is also in
# /srv/lib so the services don't see Sparky's own "app" package on their path.
COPY services/common/voltcommon ./lib/voltcommon
COPY services/catalog/app ./services/catalog/app
COPY services/inventory/app ./services/inventory/app
COPY services/cart/app ./services/cart/app
COPY services/checkout/app ./services/checkout/app
COPY services/payment/app ./services/payment/app
COPY services/orders/app ./services/orders/app
COPY data/products.json ./data/products.json

RUN chmod 755 /srv/start.sh && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001
EXPOSE 8080

# Port 8080 on all interfaces is the AgentCore Runtime contract. Locally, publish it on loopback only:
#   docker run -p 127.0.0.1:8080:8080 ...
CMD ["/srv/start.sh"]
