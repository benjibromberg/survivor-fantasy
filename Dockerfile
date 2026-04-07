FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

RUN adduser --disabled-password --no-create-home appuser && \
    mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser

ENV DEV_LOGIN=0

EXPOSE 5050

# Use gunicorn for production instead of Flask dev server
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "app:create_app()"]
