# The browser service: headless Chromium through Playwright (ADR-006, FR-08).
# The Playwright image has Chromium and its system libraries. Keep the tag equal to the Python package.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir playwright==1.63.0 "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" \
    "pydantic>=2.8" "prometheus-client>=0.20"
WORKDIR /srv
COPY app/__init__.py app/__init__.py
COPY app/browser app/browser
COPY app/clients/__init__.py app/clients/fetcher.py app/clients/
USER pwuser
CMD ["uvicorn", "--factory", "app.browser.server:app", "--host", "0.0.0.0", "--port", "8000"]
