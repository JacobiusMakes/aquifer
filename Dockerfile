FROM python:3.12-slim

WORKDIR /app

# System dependencies for OCR and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY pyproject.toml .
COPY aquifer/ aquifer/
COPY README.md .
COPY LICENSE .

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]" \
    && python -m spacy download en_core_web_sm

# Default data directory
RUN mkdir -p /data/vault /data/output /data/input

ENV AQUIFER_VAULT_PATH=/data/vault/aquifer.aqv
ENV AQUIFER_OUTPUT_DIR=/data/output

EXPOSE 8080

# Default: run the dashboard
CMD ["python", "-m", "aquifer", "dashboard", \
     "--vault", "/data/vault/aquifer.aqv", \
     "--host", "0.0.0.0", "--port", "8080"]
