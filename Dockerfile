FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite db lives here - mount a volume at this path so data survives redeploys
VOLUME ["/app/data"]

CMD ["python", "bot.py"]