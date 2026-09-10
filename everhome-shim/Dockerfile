FROM python:3.12-alpine AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY ocpp_codec.py app.py ui_events.py ./
COPY tests ./tests
RUN python -m pytest -q && echo ok > /ok

FROM python:3.12-alpine
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY --from=test /ok /ok
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ocpp_codec.py app.py ui_events.py ./
USER nobody
EXPOSE 9003
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('SHIM_PORT','9003'), timeout=3)"
CMD ["python", "app.py"]
