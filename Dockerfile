FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data
ENV PYTHONUNBUFFERED=1 TZ=Europe/Moscow
CMD ["python", "main.py"]
