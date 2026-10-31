FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 INVEST_DB=/data/invest.sqlite3 INVEST_MASTER_KEY_FILE=/secrets/master.key
WORKDIR /app
RUN groupadd --gid 10001 invest && useradd --uid 10001 --gid invest --no-create-home invest && mkdir /data /secrets && chown invest:invest /data /secrets && chmod 700 /secrets
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=invest:invest server.py tbank.py access_control.py ./
COPY --chown=invest:invest certs/ ./certs/
COPY --chown=invest:invest static/ ./static/
USER invest
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080"]
