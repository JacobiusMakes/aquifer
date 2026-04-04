# Aquifer Deployment Guide

Aquifer consists of two deployable components:

- **CLI** — the `aquifer` command for local de-identification
- **Strata** — the FastAPI-based hosted API server that manages multi-practice vaults over HTTP

This guide covers deploying Strata. For CLI-only usage, see the README.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| tesseract-ocr | any | Required only for OCR (scanned document) support |
| Docker | 24+ | Recommended for production |

On Debian/Ubuntu, install tesseract:

```bash
apt-get install tesseract-ocr libgl1
```

On macOS:

```bash
brew install tesseract
```

---

## Quick Start (Local Development)

```bash
# Install with all extras (NER + OCR)
pip install -e ".[all]"

# Download the spaCy model
python -m spacy download en_core_web_sm

# Set secrets (insecure dev mode only — see warning below)
export AQUIFER_DEBUG=1
export AQUIFER_ALLOW_INSECURE_DEFAULTS=1

# Start the server
aquifer server --debug
```

The server starts on `http://0.0.0.0:8443`. Health check: `GET /api/v1/health`.

**Warning:** `AQUIFER_ALLOW_INSECURE_DEFAULTS=1` causes the server to boot with hardcoded placeholder keys. All vault data encrypted under those keys will be unrecoverable if you ever run with real keys. Never use this in any persistent environment.

To use real secrets locally:

```bash
export AQUIFER_MASTER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export AQUIFER_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
aquifer server --debug
```

---

## Docker Deployment (Recommended for Production)

### Build the image

```bash
docker build -t aquifer:latest .
```

The build is multi-stage. Stage 1 compiles dependencies and downloads the spaCy model. Stage 2 produces a slim runtime image running as non-root user `aquifer` (UID 1000).

### Run with docker run

```bash
# Generate secrets once and store them securely
export AQUIFER_MASTER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export AQUIFER_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

docker run -d \
  --name aquifer-server \
  --restart unless-stopped \
  -p 8443:8443 \
  -e AQUIFER_MASTER_KEY="$AQUIFER_MASTER_KEY" \
  -e AQUIFER_JWT_SECRET="$AQUIFER_JWT_SECRET" \
  -v aquifer_data:/data/strata \
  aquifer:latest
```

If you omit `AQUIFER_MASTER_KEY` or `AQUIFER_JWT_SECRET`, the entrypoint script auto-generates random values and prints a loud warning. Data encrypted under auto-generated keys is unrecoverable after a container restart. This is only acceptable for throwaway testing.

### Run with docker compose (recommended)

The repo ships `docker-compose.yml` for production and `docker-compose.dev.yml` as a development override.

**Production:**

Create a `.env` file (never commit this):

```
AQUIFER_MASTER_KEY=<output of secrets.token_urlsafe(32)>
AQUIFER_JWT_SECRET=<output of secrets.token_urlsafe(32)>
```

Then:

```bash
docker compose up -d
docker compose logs -f aquifer-server
```

This mounts a named volume (`strata_data`) at `/data/strata`, which persists across restarts.

**Development (with hot reload):**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

The dev override mounts `./aquifer` into the container read-only and runs uvicorn with `--reload`, so changes to Python files take effect without rebuilding the image. It also exposes port 5678 for debugpy remote attach.

### Health check

The container exposes a built-in health check:

```
GET http://localhost:8443/api/v1/health
Interval: 30s | Timeout: 5s | Start period: 10s | Retries: 3
```

```bash
curl -f http://localhost:8443/api/v1/health
```

A `200` response with `{"status": "ok"}` (or equivalent) indicates the server is ready.

---

## Environment Variables

All variables are read at startup. The server will refuse to start if required variables are missing (unless running in insecure dev mode).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AQUIFER_MASTER_KEY` | Yes* | — | Encrypts per-practice vault keys. Losing this key means losing access to all vaults. Generate with `secrets.token_urlsafe(32)`. |
| `AQUIFER_JWT_SECRET` | Yes* | — | Signs JWT authentication tokens. Generate with `secrets.token_urlsafe(32)`. |
| `AQUIFER_DATA_DIR` | No | `./strata_data` | Root directory for all server data (database, vault files, output). |
| `AQUIFER_DB_PATH` | No | `$AQUIFER_DATA_DIR/strata.db` | SQLite database path. Defaults to inside `AQUIFER_DATA_DIR`. |
| `AQUIFER_HOST` | No | `0.0.0.0` | Bind address. |
| `AQUIFER_PORT` | No | `8443` | Listen port. |
| `AQUIFER_JWT_EXPIRY_HOURS` | No | `24` | JWT token lifetime in hours. |
| `AQUIFER_USE_NER` | No | `true` | Enable spaCy NER for name and entity detection. Disable to reduce CPU load (degrades recall). |
| `AQUIFER_MAX_UPLOAD_BYTES` | No | `104857600` | Max file upload size (default 100 MB). |
| `AQUIFER_DEBUG` | No | `false` | Enable debug mode: verbose logging, CORS open, uvicorn reload. Never set in production. |
| `AQUIFER_ALLOW_INSECURE_DEFAULTS` | No | `false` | Allow boot with placeholder keys when `DEBUG=1`. Dev only. |
| `AQUIFER_ALLOW_INSECURE_BOOT` | No | `false` | Fallback boot with insecure keys even if startup validation fails. |

*Required unless `AQUIFER_DEBUG=1` and `AQUIFER_ALLOW_INSECURE_DEFAULTS=1` are both set.

---

## Production Checklist

- [ ] Generate `AQUIFER_MASTER_KEY` with `secrets.token_urlsafe(32)` and store it in a secrets manager (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, etc.)
- [ ] Generate `AQUIFER_JWT_SECRET` separately — do not reuse the master key
- [ ] Never set `AQUIFER_DEBUG=1` in production
- [ ] Never set `AQUIFER_ALLOW_INSECURE_DEFAULTS=1` in production
- [ ] Place Aquifer behind a TLS-terminating reverse proxy (see Security Hardening)
- [ ] Mount a persistent volume at `/data/strata` — container-local storage is ephemeral
- [ ] Set file permissions on the vault directory to `700` (owned by UID 1000)
- [ ] Configure a backup schedule for `strata.db` and all `.aqv` vault files
- [ ] Set up log aggregation (the server logs to stdout/stderr in JSON or plain format)
- [ ] Tune `AQUIFER_MAX_UPLOAD_BYTES` for your largest expected file size

---

## Backup and Recovery

### What to back up

| File | Location | Description |
|------|----------|-------------|
| `strata.db` | `$AQUIFER_DATA_DIR/strata.db` | Server metadata: practices, users, API keys, usage |
| `*.aqv` | `$AQUIFER_DATA_DIR/practices/*/` | Per-practice encrypted token vaults |
| `AQUIFER_MASTER_KEY` value | your secrets manager | Decrypts all vault keys — see below |

The `.aqf` output files are de-identified and contain no PHI. They do not need HIPAA-grade backup, but if you store them on the server, include them in your backup for operational convenience.

### SQLite backup

SQLite is safe to copy while live only if you use one of these methods:

**WAL checkpoint + copy (simplest):**

```bash
sqlite3 /data/strata/strata.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp /data/strata/strata.db /backups/strata-$(date +%Y%m%d).db
```

**SQLite online backup (preferred for zero-downtime):**

```bash
sqlite3 /data/strata/strata.db ".backup /backups/strata-$(date +%Y%m%d).db"
```

Run the backup command from inside the container or from the host if the volume is mounted:

```bash
docker exec aquifer-server sqlite3 /data/strata/strata.db \
  ".backup /data/strata/strata-$(date +%Y%m%d).db"
```

### Vault file backup

The `.aqv` files are self-contained encrypted databases. Copy them directly:

```bash
cp -r /data/strata/practices/ /backups/vaults-$(date +%Y%m%d)/
```

### Master key recovery

`AQUIFER_MASTER_KEY` encrypts the per-practice vault keys stored in `strata.db`. If you lose the master key:

- The `.aqv` vault files themselves remain intact but their per-practice keys (stored in `strata.db`) cannot be decrypted
- PHI stored in those vaults becomes permanently inaccessible
- There is no recovery path — this is by design

**Store `AQUIFER_MASTER_KEY` in at least two independent locations** (e.g., primary secrets manager + a break-glass HSM or printed key escrow). Treat it with the same discipline as a CA private key.

---

## Scaling Considerations

### SQLite limits

Strata uses SQLite for both server metadata and per-practice vault indexes. SQLite has a single-writer constraint: concurrent writes serialize, and heavy write loads will cause lock contention.

SQLite is appropriate for:
- A single deployment serving one or a small number of practices
- Low-to-medium throughput (hundreds of de-identification requests per day)

SQLite is not appropriate for:
- High concurrency write workloads
- Multi-region deployments sharing state

The Phase D roadmap includes a PostgreSQL migration path. If you anticipate high throughput now, track that work or contribute to it.

### Horizontal scaling

Aquifer can run multiple API instances if they share a common data directory:

- Mount an NFS share or AWS EFS volume at `/data/strata` on each instance
- Point all instances at the same `AQUIFER_DATA_DIR`
- Use the same `AQUIFER_MASTER_KEY` and `AQUIFER_JWT_SECRET` on all instances

SQLite write contention limits the benefit of this approach. It helps for read-heavy workloads (inspecting `.aqf` files, fetching vault entries for rehydration) more than for write-heavy ones.

### NER processing

When `AQUIFER_USE_NER=true`, each de-identification request loads and runs a spaCy NLP pipeline. This is CPU-intensive.

For high-throughput deployments:
- Run Strata instances on CPU-optimized instances (not shared burstable VMs)
- Consider deploying a separate worker pool for processing jobs, with the API tier handling routing only
- Set resource limits in docker compose (the production compose file defaults to 2 GB memory, 512 MB reserved)

If throughput is critical and recall can tolerate some reduction, set `AQUIFER_USE_NER=false`. Regex-based detection still covers most HIPAA Safe Harbor categories.

---

## Monitoring

### Health endpoint

```
GET /api/v1/health
```

Returns `200` when the server is up and the database is reachable. Use this as your load balancer health check and uptime monitor probe.

### Key metrics to watch

| Metric | Why |
|--------|-----|
| Disk usage on `/data/strata` | `.aqv` vaults grow as PHI is stored; `.aqf` output files accumulate |
| Request latency on de-identification endpoints | Baseline varies by file size and NER load; spikes indicate resource pressure |
| HTTP 5xx error rate | Any non-zero rate warrants investigation |
| `strata.db` file size | Proxy for total practice/user activity |
| CPU utilization during NER processing | High sustained CPU may indicate need for more instances or disabling NER |

### Log aggregation

The server logs to stdout. In Docker, ship logs with your standard log driver:

```yaml
# In docker-compose.yml
services:
  aquifer-server:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"
```

For centralized aggregation, use the `awslogs`, `splunk`, or `fluentd` driver instead.

---

## Security Hardening

### TLS

Aquifer does not handle TLS directly. Place it behind a reverse proxy that terminates TLS:

**nginx example:**

```nginx
server {
    listen 443 ssl;
    server_name api.yourpractice.com;

    ssl_certificate     /etc/ssl/certs/yourpractice.crt;
    ssl_certificate_key /etc/ssl/private/yourpractice.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Caddy example (automatic HTTPS):**

```
api.yourpractice.com {
    reverse_proxy localhost:8443
}
```

### Firewall

- Do not expose port 8443 directly to the internet
- Accept connections only from the reverse proxy
- Restrict outbound traffic to what the server actually needs (no internet egress required at runtime)

### Vault directory permissions

```bash
# Set on the host volume mount
chmod 700 /path/to/strata_data/practices
chown -R 1000:1000 /path/to/strata_data
```

Inside Docker, the container already runs as UID 1000. If you use a named volume, Docker handles ownership automatically.

### JWT secret rotation

Rotating `AQUIFER_JWT_SECRET` invalidates all active sessions. To rotate:

1. Generate a new secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update the value in your secrets manager
3. Redeploy the container with the new value
4. All clients will need to re-authenticate

Plan rotation during a maintenance window or implement a grace period by running both secrets simultaneously during transition (requires application-level support — check the current Strata implementation before attempting).

### Master key management

In production, `AQUIFER_MASTER_KEY` should never live in environment variables on the host. Preferred approaches:

- **AWS**: Inject from Secrets Manager via ECS task definition or EC2 instance role
- **GCP**: Inject from Secret Manager via Cloud Run or GKE Secrets
- **HashiCorp Vault**: Use the Vault agent sidecar to inject at runtime
- **HSM**: For highest assurance, generate and store the key in an HSM and never let it touch disk in plaintext

If you must use environment variables (e.g., small self-hosted deployment), restrict access to the host's environment with OS-level controls and ensure the `.env` file is never committed to version control.
