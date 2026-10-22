# Shared image definition for every Python microservice.
# Build from the services/ directory:  docker build -f python.Dockerfile --build-arg SERVICE=catalog .
FROM public.ecr.aws/docker/library/python:3.12-slim

ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY common/requirements.txt /tmp/common-requirements.txt
COPY ${SERVICE}/requirements.txt /tmp/service-requirements.txt
RUN pip install -r /tmp/common-requirements.txt -r /tmp/service-requirements.txt \
 && opentelemetry-bootstrap -a requirements > /tmp/otel-requirements.txt \
 && pip install -r /tmp/otel-requirements.txt

COPY common/voltcommon ./voltcommon
COPY ${SERVICE}/app ./app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001
EXPOSE 8080

# Binds all interfaces *inside the pod*; the port is only reachable through a ClusterIP Service.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
