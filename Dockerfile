# Build stage: install dependencies into a clean prefix
FROM python:3.13-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime stage: no build tools, non-root user, data on a volume
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CIE_DB=/data/cie.db
COPY --from=build /install /usr/local
WORKDIR /app
COPY cie/ cie/
RUN useradd --uid 10001 --no-create-home cie && mkdir /data && chown cie /data
USER cie
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "cie.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
