FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg unzip ca-certificates && rm -rf /var/lib/apt/lists/*
ADD https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip /tmp/deno.zip
RUN unzip /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PORT=10000
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 900"]
