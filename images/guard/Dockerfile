# guard: NeMo Guardrails server (ADR-011). The config comes from the guard-nemo-config ConfigMap.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG NEMO_GUARDRAILS_VERSION=0.24.1
RUN pip install --no-cache-dir "nemoguardrails[server]==${NEMO_GUARDRAILS_VERSION}"
USER 65534
