#!/bin/sh
# Starts a VoltVerse Python service. Telemetry comes from one of two places:
#   injected - the Amazon CloudWatch Observability add-on injected the ADOT SDK into this pod (PYTHONPATH points
#              at /otel-auto-instrumentation-python and sitecustomize starts it): run uvicorn as-is.
#   bundled  - the image installed the ADOT SDK itself (Sparky, local builds): wrap with opentelemetry-instrument.
set -e
set -- uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log
case "${PYTHONPATH:-}" in
  *otel-auto-instrumentation-python*) exec "$@" ;;
esac
if command -v opentelemetry-instrument >/dev/null 2>&1; then
  exec opentelemetry-instrument "$@"
fi
exec "$@"
