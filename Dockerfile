FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 INVEST_DB=/data/invest.sqlite3
WORKDIR /app
RUN groupadd --gid 10001 invest && useradd --uid 10001 --gid invest --no-create-home invest && mkdir /data && chown invest:invest /data
COPY --chown=invest:invest server.py ./
COPY --chown=invest:invest static/ ./static/
USER invest
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080"]
