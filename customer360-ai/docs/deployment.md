# 9. Deployment Architecture

## 9.1 Local (Dev)
- docker compose up: frontend, backend, postgres, redis, worker (celery). Health endpoints.
- Health and readiness are served at `/api/v1/health` and `/api/v1/ready` (both under the versioned prefix), and both are exempt from rate limiting so an orchestrator probe cannot be throttled.

## 9.2 Production
- Containers: frontend (static build + nginx), backend (uvicorn+gunicorn), worker (celery), postgres (managed/RDS or container), redis (managed or container).
- Reverse proxy, TLS, secrets via env.
- CI/CD: GitHub Actions. PR → lint+tests+build; main → tests+build images → optional deploy to Render/Railway/AWS (artifact, no secrets pushed).
- Horizontal scale: stateless API + worker queue. The API's only per-process state is the rate-limit counter, which `RATE_LIMIT_BACKEND=redis` moves out of the process (see below).
- Observability: health/ready, structured logs, drift checks.

## 9.3 Testing tiers
Four tiers, each catching what the one above it cannot:

| Tier | Command | Needs |
|---|---|---|
| Backend unit + integration | `cd backend && pytest` | nothing (SQLite) |
| HTTP smoke | `cd backend && python scripts/smoke_datasets_e2e.py` | nothing (SQLite, boots its own server) |
| Frontend unit | `cd frontend && npm run test` | nothing (jsdom) |
| Browser E2E | `cd frontend && npm run test:e2e` | nothing but Playwright's Chromium (`npx playwright install chromium`) |

The browser tier earns its cost on exactly the cases the API-level tests cannot see: that the client stores the *rotated* refresh token rather than the one it just spent, that a transparent re-auth works at all, and that signing out reaches the server instead of only clearing `localStorage`. It builds the production bundle and serves it with `vite preview`, so a bundling or env-injection mistake fails the same way it would in a deployment.

Set `PYTHON` to point the E2E suite at a specific interpreter (a venv, for instance); it defaults to `python` on `PATH`.

## 9.4 Phase 12 hardening

### The API now refuses to start misconfigured
`APP_ENV=production` triggers `Settings._reject_insecure_production_config`, which raises before the app serves a request if `JWT_SECRET` or `SECRET_KEY` is still a shipped placeholder or shorter than 32 characters, `DEBUG=true`, `CORS_ORIGINS` contains `*`, or `DATABASE_URL_RO` is empty (which would silently give the NL-to-SQL path a read-write connection). This is deliberate: the difference between the dev and prod defaults was an authentication bypass, not a rough edge, and a container that refuses to boot is louder than one that logs a warning nobody reads.

Set at minimum, from the host platform's secret store:

```
APP_ENV=production
DEBUG=false
JWT_SECRET=<32+ random chars>
SECRET_KEY=<32+ random chars, different from JWT_SECRET>
DATABASE_URL=postgresql+psycopg://...
DATABASE_URL_RO=postgresql+psycopg://<SELECT-only credential>...
CORS_ORIGINS=https://your-app.example.com
```

### TLS and proxy
`Strict-Transport-Security` is emitted only when `APP_ENV=production`, because asserting it over plain local HTTP would pin `localhost` to HTTPS in a developer browser. Terminate TLS at the proxy.

If the API sits behind that proxy, set `RATE_LIMIT_TRUST_PROXY=true`. Leave it `false` when the API is directly reachable - see `docs/security.md` for why the header is ignored by default.

### Images
`deploy.yml` builds and pushes `backend` (target `prod`) and `frontend` (target `serve`) to GHCR, tagged with the commit SHA and `latest`, with GHA layer caching. Images are only built after the same lint and test suite CI runs. The workflow grants `packages: write` and nothing else; no secret is written to the repository or to an artifact.

### Multi-replica rate limiting
`RATE_LIMIT_BACKEND` picks the counter store:

| Value | Behaviour | Use when |
|---|---|---|
| `memory` | Per process. N replicas grant N x the budget. | Single API process, local dev |
| `redis` | Shared counters keyed in Redis. The budget belongs to the deployment. | More than one API replica |
| `auto` (default) | Redis if reachable, memory otherwise. | Single node today, correct if it grows |

Point `RATE_LIMIT_REDIS_URL` at the same Redis as Celery (blank means `REDIS_URL`) and set `RATE_LIMIT_REDIS_PREFIX` if the instance is shared with anything else. Note that `auto` degrades to `memory` if Redis is unreachable: an outage in a dependency should not become an outage in the product, but the budget quietly returns to per-process. Alert on the `rate_limit store unavailable` log line if the limiter matters to you.

### Sign-in lockout
`LOGIN_LOCKOUT_*` needs no external service - failures are rows in `login_failures`. The threshold (default 5 per 15 minutes) is deliberately generous, because lockout is a denial-of-service vector as much as a defence. Raising it above roughly 10 stops protecting the accounts it is meant to protect.

### CI gates
| Job | What it protects |
|---|---|
| `backend` | ruff over `app tests scripts migrations`, byte-compile, pytest with a 90% coverage floor |
| `migrations` | `alembic upgrade head`, `alembic check` (model/migration drift), and a full downgrade/upgrade cycle - against real PostgreSQL, because autogenerating against SQLite reports phantom diffs for `SERIAL`/`timestamptz` |
| `smoke` | `scripts/smoke_datasets_e2e.py`: register → upload → process → train → score → recommend → campaign → monitor → export → logout, against a real server |
| `frontend` | `npm run lint`, vitest, `tsc --noEmit && vite build` |
| `e2e` | Playwright: the refresh interceptor, server-side logout, and a real CSV upload. The upload case exists because a browser-only encoding bug - axios flattening `FormData` into `{"file":{}}` - looks identical to a healthy backend from the API level. Boots the real API on a throwaway SQLite file and the real production bundle, so no service containers are needed |
| `security` | `pip-audit --strict`, `npm audit --audit-level=high`, `gitleaks` over full history |

Workflow permissions default to `contents: read`, and runs for the same branch are cancelled so a superseded push does not keep a runner busy. The `e2e` job uploads `playwright-report/` and `test-results/` (traces and screenshots of failures) as artifacts.
