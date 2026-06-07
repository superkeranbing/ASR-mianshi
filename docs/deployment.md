# ASR-Mianshi Deployment Guide

> Last updated: 2026-06-06

## Prerequisites

| Dependency | Version | Notes |
|-----------|---------|-------|
| Docker | >= 24.0 | |
| Docker Compose | >= 2.20 | Included with Docker Desktop |
| Git | >= 2.40 | |

## Directory Structure

```
docker/
  docker-compose.yml          # Production (single-server)
  docker-compose.dev.yml      # Development
  docker-compose.backend.yml  # Production (split: backend server)
  docker-compose.frontend.yml # Production (split: frontend server)
  nginx/nginx.conf            # Reverse proxy for single-server prod
  minio/init.sh               # MinIO bucket initialization

backend/
  Dockerfile                  # Python 3.12 + FastAPI + Celery
  .dockerignore

frontend/
  Dockerfile                  # Multi-stage: Node build -> Nginx serve (single-server)
  Dockerfile.split            # Multi-stage: Node build -> Nginx + envsubst (split deploy)
  nginx.conf                  # SPA routing (single-server)
  nginx.split.conf            # SPA routing + remote backend proxy (split deploy)
```

## Quick Start: Development

```bash
# 1. Clone and enter project
git clone <repo-url> asr-mianshi
cd asr-mianshi

# 2. Copy and edit environment
cp backend/.env.example backend/.env
# Edit .env: set LLM_API_KEY, adjust any other settings

# 3. Start all services
docker compose -f docker/docker-compose.dev.yml up -d

# 4. Verify
curl http://localhost:8000/api/health    # Backend
curl http://localhost:5173               # Frontend (Vite dev server)
```

Dev services start:
- PostgreSQL `asr-postgres` on `:5432` (user: `dev`, password: `dev123`, db: `asr_mianshi`)
- Redis `asr-redis` on `:6379`
- MinIO `asr-minio` on `:9000` (console: `:9001`)
- Backend `asr-backend` on `:8000` (uvicorn with hot reload)
- Frontend dev `asr-frontend-dev` on `:5173` (Vite with HMR)

### Dev: start Celery worker manually

The dev compose does NOT start a Celery worker (to allow local Python debugging). Start it separately:

```bash
# On the host (inside backend/ directory):
cd backend
celery -A celery_worker worker -l info -P solo --without-gossip --without-mingle --without-heartbeat

# Or with the run.py helper (starts both FastAPI and Celery):
python run.py
```

## Production Deployment

```bash
# Build and start all services
docker compose -f docker/docker-compose.yml up -d --build

# Check status
docker compose -f docker/docker-compose.yml ps

# View logs
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml logs -f celery-worker
```

### Production Services

| Service | Container | Internal Port | Description |
|---------|-----------|---------------|-------------|
| postgres | asr-postgres | 5432 | PostgreSQL 16 + pgvector |
| redis | asr-redis | 6379 | Cache + Celery broker |
| minio | asr-minio | 9000 | S3-compatible file storage |
| backend | asr-backend | 8000 | FastAPI application server |
| celery-worker | asr-celery-worker | ? | Async tasks (ASR + LLM) |
| celery-beat | asr-celery-beat | ? | Scheduled tasks (cleanup) |
| frontend | asr-frontend | 80 | Nginx serving built React app |

The frontend is the only service exposed externally (port 80). All `/api/` and `/ws/` requests are proxied through the frontend's Nginx to the backend.

### Production Environment Variables

Set in `docker-compose.yml` under each service:

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `postgresql://dev:dev123@postgres:5432/asr_mianshi` | Async driver |
| `SYNC_DATABASE_URL` | same | Sync driver (Celery, Alembic) |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | |
| `MINIO_ENDPOINT` | `minio:9000` | |
| `MINIO_ACCESS_KEY` | `minioadmin` | Change in production |
| `MINIO_SECRET_KEY` | `minioadmin` | Change in production |
| `MINIO_BUCKET` | `asr-mianshi` | |
| `JWT_SECRET` | *must override* | Generate with `openssl rand -hex 32` |
| `LLM_API_KEY` | ? | DeepSeek / Qwen / OpenAI API key |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | |
| `LLM_MODEL` | `deepseek-chat` | |
| `CORS_ORIGINS` | `http://localhost` | Comma-separated origins. In split deploy, must include the frontend server's origin |
| `DEBUG` | `false` | |


## Split Deployment (Frontend + Backend on separate servers)

Use this when you want the frontend on one server (lower cost, CDN-friendly) and the backend on another (needs more CPU/GPU for ASR and LLM).

### Architecture

```
Server A (Backend)                         Server B (Frontend)
+---------------------------+              +---------------------------+
| postgres :5432 (internal) |              | frontend (nginx) :80      |
| redis   :6379 (internal)  |              |     /api/*  ----+         |
| minio   :9000 (internal)  |              |     /ws/*   ----+--+      |
| backend :8000 ------------+-- HTTP ----->|     /*       SPA |  |     |
| celery-worker             |              +------------------|--|-----+
| celery-beat               |                                 |  |
+---------------------------+              proxy_pass http://ServerA_IP:8000
```

### Step 1: Deploy Backend (Server A)

```bash
# Upload code
scp -r backend/ docker/ user@server-a:/opt/asr-mianshi/

# Edit CORS and JWT settings
vim /opt/asr-mianshi/docker/docker-compose.backend.yml
# - CORS_ORIGINS: set to http://ServerB_IP,http://your-domain.com
# - JWT_SECRET: use `openssl rand -hex 32` output

# Start
cd /opt/asr-mianshi
docker compose -f docker/docker-compose.backend.yml up -d --build

# Verify
curl http://localhost:8000/api/health
```

### Step 2: Deploy Frontend (Server B)

```bash
# Upload code
scp -r frontend/ docker/docker-compose.frontend.yml user@server-b:/opt/asr-mianshi/

# Set backend address
vim /opt/asr-mianshi/docker/docker-compose.frontend.yml
# - BACKEND_HOST: set to ServerA's public IP or domain

# Start
cd /opt/asr-mianshi
docker compose -f docker/docker-compose.frontend.yml up -d --build

# Verify
curl http://localhost:80/api/health   # Should proxy to backend
```

### How the split frontend resolves the backend address

The file `frontend/Dockerfile.split` uses a **template-based nginx config**:

```dockerfile
# Key difference from the single-server Dockerfile:
COPY nginx.split.conf /etc/nginx/templates/default.conf.template
CMD ["/bin/sh", "-c", "envsubst '$BACKEND_HOST' < .../default.conf.template > .../default.conf && nginx -g 'daemon off;'"]
```

When the container starts, `envsubst` replaces `${BACKEND_HOST}` in the nginx template with the actual IP/domain from the environment variable:

```nginx
# nginx.split.conf (template)
location /api/ {
    proxy_pass http://${BACKEND_HOST}:8000/api/;     # <- replaced at startup
}
location /ws/ {
    proxy_pass http://${BACKEND_HOST}:8000/ws/;
}
```

This means you can change the backend address by restarting the container with a new `BACKEND_HOST` value ? no rebuild needed.

### Security Notes for Split Deployment

| Concern | Action |
|---------|--------|
| **CORS** | Set `CORS_ORIGINS` in backend compose to the frontend server's exact origin(s) |
| **Firewall** | Server A: only expose port 8000 to Server B's IP (not 0.0.0.0) |
| **JWT secret** | Must be identical on both sides if frontend validates tokens locally (current architecture only validates on backend, so only Server A needs it) |
| **HTTPS** | Put Cloudflare or nginx+certbot in front of Server B's port 80. The backend (Server A) can stay HTTP-only since traffic between servers is internal/private |
| **Upload files** | All files stored on Server A's Docker volume. Both servers access via API ? the frontend server never needs direct filesystem access |

## Volume Management

| Volume | Mount point | Purpose |
|--------|-------------|---------|
| `pgdata` | `/var/lib/postgresql/data` | Database persistence |
| `miniodata` | `/data` | File storage persistence |
| `uploads` | `/app/uploads` | Shared upload directory (backend + workers) |
| `models` | `/app/models` | ASR model cache |

## Backup & Restore

```bash
# Backup PostgreSQL
docker exec asr-postgres pg_dump -U dev asr_mianshi > backup.sql

# Restore
docker exec -i asr-postgres psql -U dev asr_mianshi < backup.sql

# Backup uploads and models
tar -czf uploads-backup.tar.gz -C /var/lib/docker/volumes asr-mianshi_uploads
```

## Troubleshooting

### Celery worker not processing tasks
```bash
# Check worker logs
docker compose -f docker/docker-compose.yml logs celery-worker

# Verify Redis connectivity
docker exec asr-redis redis-cli ping

# Purge stuck tasks
docker exec asr-redis redis-cli FLUSHALL
```

### Database migration
```bash
# Run from backend container
docker exec asr-backend alembic upgrade head
```

### Split deploy: frontend can't reach backend
```bash
# From the frontend server, test connectivity
curl http://BACKEND_IP:8000/api/health

# If timeout: check firewall on Server A, ensure port 8000 is open to Server B's IP
# If "Connection refused": check that backend container is running and port is mapped
docker compose -f docker/docker-compose.backend.yml ps
```

### Split deploy: CORS errors in browser
```bash
# Check the browser console for the exact origin being blocked
# Then update CORS_ORIGINS in docker-compose.backend.yml and restart:
docker compose -f docker/docker-compose.backend.yml up -d backend
```

### Rebuild single service
```bash
docker compose -f docker/docker-compose.yml up -d --build backend
```

## Health Checks

```bash
# Backend
curl http://localhost:8000/api/health

# MinIO
curl http://localhost:9000/minio/health/live

# PostgreSQL
docker exec asr-postgres pg_isready -U dev -d asr_mianshi

# Redis
docker exec asr-redis redis-cli ping
```
