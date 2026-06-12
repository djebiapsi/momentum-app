FROM python:3.11-slim
# Sans ça, les print() des jobs (briefings, monitor) restent bloqués dans le
# buffer stdout et n'apparaissent jamais dans `docker logs`.
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "0", "--graceful-timeout", "30"]
