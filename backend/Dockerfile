# Pinned to the 3.11/bookworm series; pin an exact digest for releases.
FROM python:3.11-slim-bookworm

WORKDIR /app

# curl is needed for the Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY transcriber.py .
COPY config/ config/
RUN mkdir -p /data

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
