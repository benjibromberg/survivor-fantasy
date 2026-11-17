# Pinned by digest for reproducible, supply-chain-hardened builds.
# Dependabot (docker) keeps this digest current. Re-pin: docker buildx imagetools inspect python:3.11-slim
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "gunicorn==26.2.0"

COPY . .

# Create appuser WITH a home directory: gunicorn 26's control server writes to
# $HOME, so a missing home dir causes "Permission denied: '/home/appuser'".
RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser

ENV DEV_LOGIN=0

EXPOSE 5050

# Use gunicorn for production instead of Flask dev server
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "app:create_app()"]
