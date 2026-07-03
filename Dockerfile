# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install dependencies into a virtualenv for clean copying
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy virtualenv with all dependencies
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY . .

    # Create non-root user for security
    RUN groupadd -r gemexporter && useradd -r -g gemexporter gemexporter && \
        mkdir -p /app/exports && \
        chown -R gemexporter:gemexporter /app
    USER gemexporter

    # Expose Flask port
    EXPOSE 5000

    # Health check
    HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
        CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

    # Default command: run the web GUI
    CMD ["python", "app.py"]
