# edge and warm-controller (control/ package). Built on node 2, loaded into k3s (no registry).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir uv==0.7.16
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-hashes --no-default-groups -o requirements.txt && uv pip install --system -r requirements.txt
# Tokenizer of the main model for the guard rules (public repo, no token needed).
RUN python -c "from huggingface_hub import hf_hub_download as d; import shutil; \
shutil.copy(d('RedHatAI/gemma-4-31B-it-FP8-dynamic', 'tokenizer.json'), '/app/tokenizer.json')"
COPY control ./control
USER 65534
