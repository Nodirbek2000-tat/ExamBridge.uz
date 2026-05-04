# sat (backend) — Deployment Guide

Server IP: `161.97.107.51`
Domain: `nodir.exambridge.uz` (Django admin + API, proxied by sat_front nginx)

---

## Same-server deployment (recommended)

Backend runs alongside frontend. The `sat_front` nginx handles SSL and proxying.

### Step 1 — Clone and configure
```bash
git clone https://github.com/YOUR_USER/sat.git
cd sat
cp .env.example .env
nano .env   # Fill in secrets: SECRET_KEY, DB_PASSWORD, GOOGLE keys, AI keys
```

### Step 2 — Make sure sat_front is running first
sat_front creates the `exambridge_net` Docker network.
```bash
# In sat_front directory:
docker-compose up -d --build
```

### Step 3 — Start backend
```bash
# In sat/ directory:
docker-compose up -d --build
```

On every start, the entrypoint will:
1. Wait for PostgreSQL to be healthy
2. Run `python manage.py migrate` — **safe, never deletes data**
3. Run `python manage.py collectstatic`
4. Start Gunicorn

### Step 4 — Create superuser (first time only)
```bash
docker-compose exec web python manage.py createsuperuser
```

---

## Re-deploy after code changes
```bash
git pull
docker-compose up -d --build
```
PostgreSQL data is in a named Docker volume (`sat_postgres_data`) — it **persists** across all restarts and re-deploys.

---

## Useful commands
```bash
# View logs
docker-compose logs -f web
docker-compose logs -f celery

# Django management commands
docker-compose exec web python manage.py shell
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser

# Database shell
docker-compose exec db psql -U satuser -d satdb

# Check containers
docker-compose ps

# Stop everything (data is safe)
docker-compose down

# Stop AND delete database (DANGER — only if you want to reset)
docker-compose down -v
```

---

## Environment variables (.env)

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key (generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`) |
| `DEBUG` | `False` in production |
| `ALLOWED_HOSTS` | `nodir.exambridge.uz,exambridge.uz,www.exambridge.uz` |
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | PostgreSQL username |
| `DB_PASSWORD` | PostgreSQL password |
| `DB_HOST` | `db` (Docker service name) |
| `REDIS_URL` | `redis://redis:6379/0` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) API key |
| `OPENAI_API_KEY` | OpenAI API key |

---

## Standalone deployment (separate server)

If backend is on its own server:
```bash
chmod +x init-letsencrypt.sh
sudo bash init-letsencrypt.sh
docker-compose -f docker-compose.standalone.yml up -d --build
```
