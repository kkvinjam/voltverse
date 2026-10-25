# Shared image definition for every Python microservice.
# Build from the services/ directory:  docker build -f python.Dockerfile --build-arg SERVICE=catalog .
#
# On EKS the OpenTelemetry SDK is NOT in the image: the Amazon CloudWatch Observability add-on injects the
# ADOT Python SDK into annotated pods (CloudWatch Application Signals). Sparky bundles the SDK through its
# requirements.txt. BUNDLE_OTEL_SDK=true bundles it into any image (used by local/docker-compose.yml).
FROM public.ecr.aws/docker/library/python:3.12-slim

ARG SERVICE
ARG BUNDLE_OTEL_SDK=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
# Same relative layout as the repo, so "-r ../common/..." inside a service's requirements.txt resolves.
COPY common/requirements.txt common/requirements-otel-sdk.txt /tmp/req/common/
COPY ${SERVICE}/requirements.txt /tmp/req/service/requirements.txt
RUN set -e; \
    extra=""; [ "$BUNDLE_OTEL_SDK" = "true" ] && extra="-r /tmp/req/common/requirements-otel-sdk.txt"; \
    pip install -r /tmp/req/common/requirements.txt -r /tmp/req/service/requirements.txt $extra

COPY common/start.sh /srv/start.sh
COPY common/voltcommon ./voltcommon
COPY ${SERVICE}/app ./app

RUN chmod 755 /srv/start.sh && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001
EXPOSE 8080

# uvicorn binds all interfaces *inside the pod*; the port is only reachable through a ClusterIP Service.
CMD ["/srv/start.sh"]
