# Customer360 AI

**Customer Segmentation, Churn Prediction & Retention Intelligence Platform**

A production-oriented, multi-tenant-capable SaaS-style analytics and
Machine Learning platform.

> "Don't just predict churn. Explain it. Prioritize it. Recommend an action. Measure the result."

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Product Philosophy](#product-philosophy)
- [Architecture Overview](#architecture-overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Database Setup](#database-setup)
- [Local Development (no Docker)](#local-development-no-docker)
- [Docker Setup](#docker-setup)
- [Testing](#testing)
- [API Documentation](#api-documentation)
- [ML Methodology](#ml-methodology)
- [Model Evaluation](#model-evaluation)
- [Deployment](#deployment)
- [Security](#security)
- [Demo Mode](#demo-mode)
- [Future Improvements](#future-improvements)
- [License](#license)

---

## Problem Statement

Businesses collect large volumes of customer transaction and behavioral
data but struggle to answer fundamental questions:

1. Who are our most valuable customers?
2. Which customers are loyal?
3. Which customers are at risk of leaving?
4. Why are these customers likely to churn?
5. How much customer value is at risk?
6. Which customers should the business prioritize?
7. What retention action should be taken?
8. Which retention campaign should be used?
9. Which products should be recommended?
10. How is customer retention changing over time?
11. Can managers ask questions about the data using natural language?
12. Can the system measure whether retention campaigns are working?

Customer360 AI answers these questions through an integrated analytics
and Machine Learning platform with explainable, prioritized, actionable
retention intelligence.

## Product Philosophy

The application follows a decision-support pipeline:

```
RAW CUSTOMER DATA
        ↓
DATA VALIDATION
        ↓
DATA CLEANING
        ↓
FEATURE ENGINEERING
        ↓
CUSTOMER 360
        ↓
RFM + CLV
        ↓
CUSTOMER SEGMENTATION
        ↓
CHURN PREDICTION
        ↓
SHAP EXPLANATION
        ↓
CUSTOMER PRIORITIZATION
        ↓
RETENTION RECOMMENDATION
        ↓
CAMPAIGN MANAGEMENT
        ↓
CAMPAIGN ANALYSIS
        ↓
AI BUSINESS ANALYST
```

The product deliberately distinguishes between:

- **FACTS** — calculated from data (revenue, counts, RFM, CLV).
- **PREDICTIONS** — produced by supervised machine learning (churn probability).
- **EXPLANATIONS** — model behavior explanations such as SHAP (not causality).
- **RECOMMENDATIONS** — business rules or AI suggestions.
- **SIMULATIONS** — what-if assumptions (never presented as guarantees).

## Architecture Overview

- **Frontend**: React + TypeScript + Vite + Tailwind CSS + shadcn/ui +
  TanStack Query + React Router + Recharts. No business logic in components;
  data access through a typed API service layer.
- **Backend**: Python + FastAPI + Pydantic v2. Clean layering:
  `API → Services → Repositories → Models`. No SQL in route handlers.
- **Database**: PostgreSQL, SQLAlchemy ORM, Alembic migrations, logical
  multi-tenant isolation via `organization_id`.
- **Data/ML**: Pandas (and Polars where beneficial), scikit-learn pipelines
  & transformers, XGBoost, K-Means/PCA, SHAP. Reproducible dataset/model/
  feature versions with fixed seeds.
- **Async jobs**: Celery + Redis for dataset processing, model training,
  monitoring. Heavy work never blocks an HTTP request.
- **LLM**: provider abstraction (OpenAI, Gemini, pluggable) used for the
  AI Analyst and NL-to-SQL. NL-to-SQL is **SELECT-only**, org-filtered,
  row-limited, and runs on read-only database credentials.

See `docs/` for detailed architecture documents.

## Features

- Secure authentication (JWT + refresh, Argon2/bcrypt, RBAC
  `ADMIN` / `ANALYST` / `VIEWER`) and organization isolation.
- Professional dataset ingestion: upload → validate → preview →
  column mapping → async processing → dataset versions → DQ report.
- Data quality engine with scoring, missing/duplicate/outlier detection,
  warnings and a transformation audit trail.
- Customer 360 profiles with purchase metrics, RFM, CLV, risk, behavior,
  historical charts.
- RFM analysis with configurable quantiles and named
  segments (Champions, Loyal, At Risk, etc.).
- ML customer segmentation (K-Means + PCA + Elbow/Silhouette) with
  user-defined business labels.
- Churn prediction pipeline (LR / RandomForest / XGBoost), model
  comparison, model registry, metric transparency (including PR-AUC/ROC-AUC
  given class imbalance).
- Churn risk categorization (LOW / MEDIUM / HIGH / CRITICAL), value-at-risk,
  at-risk customer board with sorting.
- SHAP local + global explainability.
- What-if / counterfactual simulations (clearly labeled as model simulation).
- CLV analytics with transparent formula and CLV×Churn matrix.
- Cohort analysis heatmaps (retention %, customer count, revenue retention).
- Customer journey & next-purchase baseline estimates.
- Product recommendations (frequently bought together / association rules).
- Retention recommendation engine (rule-based, explainable, actionable).
- AI Retention Advisor backed by retrieved data — never invented numbers.
- Secure NL-to-SQL.
- AI Business Insights from calculated metrics.
- Campaign builder, simulator, and A/B testing.
- Executive / Customer / Analytics dashboards.
- Model management & monitoring with drift detection (PSI).
- Export to CSV / Excel / PDF.
- Demo Mode with synthetic data clearly labeled as demo.

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, shadcn/ui, TanStack Query, React Router, Recharts |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 |
| Data | Pandas, NumPy, Polars (optional) |
| ML | scikit-learn, XGBoost, LightGBM (optional), SHAP |
| Async | Celery, Redis |
| LLM | openai + google-genai via a provider abstraction |
| Test | Pytest, httpx, Playwright |
| DevOps | Docker, Docker Compose, GitHub Actions |

## Repository Structure

```
customer360-ai/
├── frontend/                  # React + TS + Vite app
├── backend/                   # FastAPI application
├── database/                  # SQL init scripts, migrations (Alembic)
├── ml/                        # Preprocessing, segmentation, churn, explainability, models
├── data/                      # Sample data for local dev/demo
├── scripts/                   # Dev/admin scripts
├── tests/                     # Integration/e2e tests (top-level shared)
├── docker/                    # Dockerfiles + compose
├── docs/                      # Architecture & design documentation
├── .github/workflows/         # CI/CD
├── docker-compose.yml
├── .env.example
├── README.md
└── LICENSE
```

## Installation

Prerequisites: Docker & Docker Compose (or Python 3.12 + Node 20 + PostgreSQL 16).

1. Clone the repository.
2. Copy environment defaults: `cp .env.example .env`
3. Fill in secrets (LLM keys optional for demo).

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` / `production` |
| `DEBUG` | `true`/`false` |
| `DATABASE_URL` | PostgreSQL connection string (backend) |
| `DATABASE_URL_RO` | Read-only URL used by NL-to-SQL |
| `SECRET_KEY` | Generic app signing key |
| `JWT_SECRET` | JWT signing secret |
| `JWT_ALGORITHM` | e.g. `HS256` |
| `REDIS_URL` | Celery broker/backend |
| `OPENAI_API_KEY` | Optional LLM provider key |
| `GEMINI_API_KEY` | Optional LLM provider key |
| `LLM_PROVIDER` | `openai` / `gemini` / `mock` |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `MAX_UPLOAD_MB` | File size limit |
| `DEMO_MODE` | `true`/`false` |

> **Never commit `.env`.** Use environment variables in production.

## Database Setup

The schema is owned by Alembic. Nothing creates tables on startup, so migrations
must be applied before the API is useful:

```bash
cd backend
alembic upgrade head
```

Docker Compose does this for you via a one-shot `migrate` service that
`backend` and `worker` wait on. To run it by hand:

```bash
docker compose run --rm migrate
```

- `docker compose up db` provisions PostgreSQL for local dev.
- The schema includes organizations, users, datasets, dataset_versions,
  customers, transactions, products, customer_features, segments,
  customer_segments, models, model_versions, predictions, explanations,
  recommendations, campaigns, campaign_members, campaign_results,
  audit_logs, refresh_tokens, login_failures.

See `docs/database.md` for the ER diagram description.

## Local Development (no Docker)

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

`alembic upgrade head` is required: the app does not create tables at startup.
The credentials in `.env` must match a PostgreSQL that actually exists — either
let Compose provide one (`docker compose up -d db`) or point `DATABASE_URL` at
your own instance and create the role and database to match.

Frontend:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

Worker (optional, for async training/processing):

```bash
cd backend
celery -A app.tasks.celery_app:celery_app worker --loglevel=info
```

## Docker Setup

```bash
cp .env.example .env      # Windows: copy .env.example .env
docker compose up --build
```

The first start also runs `alembic upgrade head` through the one-shot `migrate`
service, which `backend` and `worker` wait on.

Services:

- `frontend` — http://localhost:3000 (nginx; proxies `/api` to the backend)
- `backend` — http://localhost:8000 (API docs at `/docs`)
- `db` — PostgreSQL
- `redis` — cache and Celery broker
- `worker` — Celery
- `migrate` — one-shot schema migration, then exits

Start the frontend only after `migrate` reports success:

```bash
docker compose logs migrate
```

## Testing

Backend:

```bash
cd backend
pytest -q
```

Frontend:

```bash
cd frontend
npm run test     # vitest
npm run build    # tsc --noEmit && vite build
```

Browser E2E (needs `npx playwright install chromium` once; no PostgreSQL
required, the suite boots its own API and preview server):

```bash
cd frontend
npm run test:e2e
```

## API Documentation

- Interactive OpenAPI docs: `http://localhost:8000/docs`
- All endpoints are under `/api`.
- See `docs/api.md` for the API surface.
- Every endpoint validates requests with Pydantic schemas and returns
  consistent error responses.

## ML Methodology

- **Preprocessing**: scikit-learn `Pipeline` / `ColumnTransformer`; imputation,
  categorical encoding, scaling; persisted and replayed identically at
  inference. No data leakage in train/val/test.
- **Segmentation**: K-Means on standardized behavioral features;
  optimal-k suggested via Elbow + Silhouette; PCA for visualization;
  cluster labels are user-assigned, never fabricated.
- **Churn**: stratified train/validation/test split; class imbalance handled
  (class weights / resampling); candidate models LR / RandomForest /
  XGBoost; hyperparameter tuning; registry of model versions.
- **Explainability**: SHAP local + global values stored per prediction.
- **CLV**: transparent baseline formula documented in docs.

See `docs/ml-pipeline.md`.

## Model Evaluation

Churn models are compared on:

- Accuracy, Precision, Recall, F1
- ROC-AUC, PR-AUC (important for imbalanced churn)
- Confusion matrix

The selected model and its exact version, features, dataset version,
and metrics are shown on the Model Management page. Each model version is
immutable; retraining always creates a new version.

## Deployment

- Docker images for frontend (static via nginx), backend (uvicorn/gunicorn),
  worker (Celery), db, redis.
- `docker-compose.prod.yml` for production containers.
- GitHub Actions CI: PR pipeline (lint + test + build) and main pipeline
  (test + build images + prepare deployment artifact).
- Deployable to Render, Railway, AWS, Azure, or any Docker host.
- Production requirements: managed secrets, postgres, redis, TLS, reverse proxy.

See `docs/deployment.md`.

## Security

- Argon2/bcrypt password hashing; JWT access + refresh; RBAC.
- Mandatory organization-level data isolation.
- Pydantic input validation; CSV/file type and size validation.
- NL-to-SQL is SELECT-only, token-validated, org-filtered, row-limited,
  executed on read-only credentials.
- CORS allow-lists, rate limiting (per-process or Redis-shared), secure headers,
  audit logging.
- Revocable, rotating refresh tokens; replaying a spent token revokes its whole
  family. Sign-out revokes server-side, not just in the browser.
- Per-address login lockout with responses that do not disclose whether an
  account exists.
- Secrets via environment variables only; API keys never in frontend code;
  `.gitignore` excludes `.env`, artifacts, and model binaries.
- Production refuses to start with placeholder secrets, `DEBUG=true`, wildcard
  CORS, or a missing read-only database URL.

See `docs/security.md`.

## Demo Mode

Set `DEMO_MODE=true` and `ENABLE_SAMPLE_DATA=true`. On first run the app
loads synthetic sample data (`data/sample/`) so evaluators can immediately
explore the Dashboard, Customers, Segmentation, Churn, CLV, Retention,
Campaigns, and AI Analyst. All demo content is clearly labeled as
**synthetic/demo data**, never presented as real business results.

## Future Improvements

- Multi-organization self-service onboarding and billing.
- Direct database / warehouse connectors (SQL, Parquet, REST ingestion).
- Collaborative filtering and session-based recommendations.
- Uplift modeling for campaign targeting and incrementality.
- Scheduled retraining with drift-triggered approval workflow.
- Expanded export branding for PDF reports; chart embedding.
- Audit-log viewer UI; per-user activity dashboards.
- Horizontal scaling towards a service-per-domain architecture.

## License

MIT (placeholder).