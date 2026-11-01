FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/data/sales.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .
RUN mkdir -p /data

CMD ["python", "main.py"]
