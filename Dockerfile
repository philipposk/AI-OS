FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app

RUN useradd --create-home --shell /bin/bash app \
 && mkdir -p /app/data \
 && chown -R app:app /app
USER app

EXPOSE 8501

ENV AI_COMPANY_DB=/app/data/ai_company.sqlite

CMD ["streamlit", "run", "ui/dashboard.py", "--server.headless", "true", "--server.port", "8501", "--server.address", "0.0.0.0", "--browser.gatherUsageStats", "false"]
