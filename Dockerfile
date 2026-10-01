FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY *.py /app/
COPY static /app/static
ENV DB_PATH=/data/jobs.db PORT=8093 PYTHONUNBUFFERED=1
EXPOSE 8093
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8093/health',timeout=4)"
CMD ["python", "/app/app.py"]
