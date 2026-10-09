# The app image: Companion API, Streamlit UI, ingest job, sweep job (app/ package).
# Built on node 2 and loaded into k3s (no registry). The command comes from the manifest.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1
RUN pip install --no-cache-dir uv==0.7.16
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-hashes --no-default-groups --group app --group ui -o requirements.txt \
    && uv pip install --system -r requirements.txt
COPY app ./app
COPY tools ./tools
RUN mkdir -p /data && chown 65534:65534 /data
USER 65534
