FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

ENV LOG_LEVEL=INFO \
    RATE_LIMIT_RPM=60 \
    CORPUS_PATH=/app/questions-corpus.txt \
    DB_PATH=/data/betterask.db

EXPOSE 8000

# To include the corpus, mount or COPY it to /app/data/questions-corpus.txt
# Example: docker cp ../end-small-talk/questions-corpus.txt container:/app/data/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
