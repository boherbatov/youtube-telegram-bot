FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg deno ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PORT=10000
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 900"]
