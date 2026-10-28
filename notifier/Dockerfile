FROM python:3.12-slim
WORKDIR /app
COPY notifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY notifier /app/notifier
CMD ["python", "notifier/worker.py"]
