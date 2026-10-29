# ShareSafe Dockerfile
# Optimized for persistent volume deployment (Fly.io, Railway, or Hugging Face Spaces)

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest httpx

# Copy source code and assets
COPY app/ app/
COPY static/ static/
COPY data/ data/
COPY pytest.ini .

# Create persistent storage directory
ENV DATABASE_PATH=/data/sharesafe.db
VOLUME /data

# Default port
EXPOSE 8000

# Entrypoint script: seed DB if missing, then start uvicorn
CMD ["sh", "-c", "python -m app.db.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

