FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY labels.py app.py command_log.py default_config.json setup_defaults.py ./
COPY static ./static
ENV UI_DATA_DIR=/data
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
