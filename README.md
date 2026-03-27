# Fitness Tracker

A self-hosted workout logging app with a mobile-friendly web UI, REST API, and AI coaching integration.

## Features

- **Workout Logging** — Log exercises with sets, reps, weight, and RPE
- **Exercise Catalogue** — Persistent exercise definitions with muscle group classification
- **Progress Tracking** — Strength progression charts, volume-by-muscle-group donut chart, PR board
- **Personal Records** — Automatic PR detection per exercise
- **Multi-User** — User isolation via Cloudflare Access (Google OAuth)
- **AI Coaching** — Optional Claude-powered workout feedback (requires Anthropic API key)

## Tech Stack

- **Backend**: FastAPI (Python 3.11)
- **Database**: PostgreSQL 15
- **Frontend**: Single-page app served by FastAPI
- **Auth**: Cloudflare Access (header-based)
- **Deployment**: Docker Compose

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/coltonschulz/fitness-tracker.git
cd fitness-tracker
cp .env.example .env
```

Edit `.env` with your own values:

```env
POSTGRES_USER=fitness
POSTGRES_PASSWORD=your-secure-password
POSTGRES_DB=fitness_db
DATABASE_URL=postgresql://fitness:your-secure-password@db:5432/fitness_db
ALLOWED_ORIGINS=https://your-domain.com
```

### 2. Start the application

```bash
docker compose up -d --build
```

### 3. Verify

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

The web UI is available at `http://localhost:8000`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_USER` | Yes | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `POSTGRES_DB` | Yes | PostgreSQL database name |
| `DATABASE_URL` | Yes | Full connection string for the API |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `http://localhost:8000`) |
| `CLAUDE_API_KEY` | No | Anthropic API key for AI coaching feature |
| `AI_COACH_MODEL` | No | Claude model for coaching (default: `claude-haiku-4-5-20251001`) |

## Authentication

This app uses [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/) for authentication. Cloudflare injects a `Cf-Access-Authenticated-User-Email` header on every request after the user authenticates via Google OAuth (or any other IdP you configure).

**Setup overview:**

1. Create a Cloudflare Tunnel pointing to your server on port 8000
2. Create a Cloudflare Access application for your domain
3. Add an Access policy allowing your users' email addresses
4. Users are auto-created in the database on first login

For local development or testing without Cloudflare, you can pass `X-User-Email` as a header.

## API Endpoints

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/workouts` | List workouts (paginated) |
| `POST` | `/api/workouts` | Create workout with exercises |
| `GET` | `/api/workouts/{id}` | Get workout detail |
| `PUT` | `/api/workouts/{id}` | Update workout metadata |
| `DELETE` | `/api/workouts/{id}` | Delete workout |
| `GET` | `/api/exercises` | List exercise definitions |
| `POST` | `/api/exercises` | Add/update exercise definition |
| `GET` | `/api/exercises/{name}/history` | Exercise set history |
| `GET` | `/api/stats/prs` | Personal records per exercise |
| `GET` | `/api/stats/volume-by-muscle` | Sets per muscle group (30 days) |
| `GET` | `/api/stats/summary` | High-level training stats |
| `GET` | `/api/auth/me` | Current user info |
| `POST` | `/api/ai-coaching` | AI coaching feedback |
| `GET` | `/health` | Health check |

## Data Management

### Backup

```bash
docker exec fitness-db pg_dump -U fitness fitness_db | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore

```bash
gunzip -c backup.sql.gz | docker exec -i fitness-db psql -U fitness fitness_db
```

## Project Structure

```
fitness-tracker/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── app/
│   ├── main.py              # FastAPI application
│   ├── deps.py              # Shared dependencies (DB, auth)
│   ├── schemas.py           # Pydantic models
│   ├── database/
│   │   └── models.py        # SQLAlchemy ORM models
│   ├── api/
│   │   ├── auth.py          # Auth routes
│   │   ├── workouts.py      # Workout creation
│   │   └── ai_coaching.py   # AI coaching endpoint
│   └── scripts/
│       └── assign_muscle_groups.py  # Exercise classification utility
├── static/
│   └── index.html           # Web UI (single-page app)
└── README.md
```

## License

MIT
