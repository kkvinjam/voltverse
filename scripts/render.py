#!/usr/bin/env python3
"""Render k8s/*.yaml by substituting ${VAR} placeholders from environment variables.

Only UPPER_CASE ${VAR} placeholders are substituted (Kubernetes' own $(VAR) syntax is untouched).
Fails loudly if any placeholder has no value, so a half-configured manifest is never applied.

Usage: render.py k8s/*.yaml > rendered.yaml
"""

import hashlib
import os
import re
import sys

PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def external_exporter(endpoint: str) -> tuple[str, str]:
    if not endpoint:
        return "", ""
    block = (
        "      otlphttp/external:\n"
        f'        endpoint: "{endpoint}"\n'
        "        compression: gzip\n"
    )
    return block, ", otlphttp/external"


def main(paths: list[str]) -> None:
    env = dict(os.environ)
    block, extra = external_exporter(env.get("EXTERNAL_OTLP_ENDPOINT", "").strip())
    docs = []
    for path in paths:
        text = open(path, encoding="utf-8").read()
        text = re.sub(r"^ *# __EXTERNAL_EXPORTER__.*\n", block, text, flags=re.M)
        text = text.replace("__EXTERNAL_LIST__", extra)
        docs.append(text)
    combined = "\n---\n".join(docs)

    collector = next((d for p, d in zip(paths, docs) if "otel-collector" in p), "")
    env.setdefault("COLLECTOR_CONFIG_HASH", hashlib.sha256(
        (collector + env.get("EXTERNAL_OTLP_ENDPOINT", "")).encode()).hexdigest()[:16])

    missing = sorted({m for m in PLACEHOLDER.findall(combined) if not env.get(m)})
    if missing:
        sys.exit(f"render.py: missing values for: {', '.join(missing)}")
    sys.stdout.write(PLACEHOLDER.sub(lambda m: env[m.group(1)], combined))


if __name__ == "__main__":
    main(sys.argv[1:])
