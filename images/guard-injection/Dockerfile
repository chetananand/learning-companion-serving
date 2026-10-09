# guard-injection: the prompt-injection classifier (ADR-011). GPU through a HAMi slice on node 2.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "transformers>=4.57,<5" "fastapi>=0.115" "uvicorn>=0.30" "prometheus-client>=0.20" "pydantic>=2.8" sentencepiece
WORKDIR /app
COPY control/__init__.py ./control/__init__.py
COPY control/guard ./control/guard
