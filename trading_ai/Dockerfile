FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs

ENV PYTHONPATH=/app
ENV TRADING_AI__DATABASE__URL=postgresql+asyncpg://trading:trading@postgres:5432/trading_ai

EXPOSE 8000

CMD ["uvicorn", "trading_ai.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
