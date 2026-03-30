# Strata Protocol

**Status:** Phase A Complete (API Server + Cloud Vault)

The Strata Protocol governs how AQF files, vault data, and claims intelligence flow between Aquifer components in cloud and hybrid deployments.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   Strata API Server                       │
│                  (aquifer.strata.server)                   │
│                                                           │
│  ┌─────────┐  ┌──────────┐  ┌────────┐  ┌────────────┐  │
│  │  Auth    │  │  De-ID   │  │ Files  │  │  Practice  │  │
│  │  Routes  │  │  Routes  │  │ Routes │  │  Routes    │  │
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └─────┬──────┘  │
│       │              │            │              │         │
│  ┌────▼──────────────▼────────────▼──────────────▼──────┐ │
│  │              Auth Middleware (JWT + API Keys)          │ │
│  └───────────────────────┬───────────────────────────────┘ │
│                          │                                 │
│  ┌───────────────────────▼───────────────────────────────┐ │
│  │              Multi-Tenant Database (SQLite)            │ │
│  │   practices | users | api_keys | files | usage_log    │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           Cloud Vault Manager                        │   │
│  │   strata_data/practices/{id}/                        │   │
│  │     ├── vault.aqv    (Fernet-encrypted token vault)  │   │
│  │     ├── aqf/         (.aqf output files)             │   │
│  │     └── uploads/     (temp staging)                  │   │
│  └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## API Endpoints

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register practice + admin user |
| POST | `/api/v1/auth/login` | Get JWT token |
| POST | `/api/v1/auth/api-keys` | Create API key |
| GET | `/api/v1/auth/api-keys` | List API keys |
| DELETE | `/api/v1/auth/api-keys/{id}` | Revoke key |

### De-identification
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/deid` | Upload + de-identify single file |
| POST | `/api/v1/deid/batch` | Batch de-identify multiple files |

### Files
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/files` | List processed files |
| GET | `/api/v1/files/{id}` | File details |
| GET | `/api/v1/files/{id}/download` | Download .aqf |
| GET | `/api/v1/files/{id}/inspect` | Token manifest (no PHI) |
| POST | `/api/v1/files/{id}/rehydrate` | Restore PHI (admin only) |

### Vault & Practice
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/vault/stats` | Vault statistics |
| GET | `/api/v1/vault/tokens/{id}` | Token type lookup |
| GET | `/api/v1/practice` | Practice info + tier |
| GET | `/api/v1/practice/usage` | Usage stats |
| GET | `/api/v1/health` | Health check |

## Authentication

Two methods supported:

1. **JWT Bearer Token** — for browser/dashboard sessions
   - Obtain via `POST /api/v1/auth/login`
   - Send as `Authorization: Bearer <jwt_token>`
   - Full access to all scopes

2. **API Key** — for programmatic/CLI access
   - Create via `POST /api/v1/auth/api-keys`
   - Format: `aq_<random>` (shown once at creation)
   - Send as `Authorization: Bearer aq_<key>`
   - Scoped access (configurable per key)

## Security Model

- **Vault encryption**: Each practice gets a unique Fernet key (AES-128-CBC + HMAC-SHA256)
- **Key management**: Practice vault keys encrypted at rest with server master key
- **Password storage**: PBKDF2-SHA256, 600k iterations, random salt
- **Multi-tenancy**: Strict practice isolation — file/vault access enforced at every endpoint
- **Rehydration audit**: PHI restoration logged, requires admin role

## Running the Server

```bash
# Development
aquifer server --debug

# Production
export AQUIFER_MASTER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export AQUIFER_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export AQUIFER_DATA_DIR=/var/lib/aquifer
aquifer server --port 8443

# Docker
docker run -e AQUIFER_MASTER_KEY=... -e AQUIFER_JWT_SECRET=... \
  -v /data:/var/lib/aquifer -p 8443:8443 aquifer server
```

## Roadmap

### Phase A — API Server + Auth (COMPLETE)
- REST API wrapping core de-ID engine
- JWT + API key authentication
- Multi-tenant practice isolation
- Cloud vault (server-managed encryption)
- File upload, process, download, inspect, rehydrate

### Phase B — Dashboard + Polish
- [ ] Hosted web dashboard (extend existing FastAPI UI)
- [ ] WebSocket progress for batch processing
- [ ] Email verification, password reset
- [ ] Rate limiting middleware

### Phase C — Sync Protocol
- [ ] Local ↔ cloud vault bidirectional sync
- [ ] Delta compression for efficient transfers
- [ ] Conflict resolution
- [ ] Offline-first with sync-on-connect

### Phase D — Scale
- [ ] PostgreSQL backend option
- [ ] AQF file deduplication across locations
- [ ] Cross-practice analytics aggregation
- [ ] FHIR interoperability bridge
- [ ] Async task queue for large batch processing
