FROM python:3.11-slim

WORKDIR /app

# Upgrade OS packages to pull in security fixes (e.g. OpenSSL CVE-2026-28390)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Upgrade pip toolchain first so Trivy sees patched versions
# (wheel/setuptools from the base image are older than our pinned versions)
RUN pip install --no-cache-dir "wheel>=0.46.2" "setuptools>=78.0.0"

# Install dependencies (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Two import roots, one image: `app` (the API package) and `core` (shared).
# The API and the sync worker run as separate processes from this same image,
# which is why both roots are on the path rather than one entrypoint vendoring
# the other.
COPY services/ ./services/
COPY packages/ ./packages/
ENV PYTHONPATH=/app/services/api:/app/packages

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
