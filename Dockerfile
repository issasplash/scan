FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY arbitrage_scanner ./arbitrage_scanner
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "arbitrage_scanner.server:app", "--host", "0.0.0.0", "--port", "8000"]
