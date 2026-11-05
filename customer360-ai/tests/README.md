# Top-level test guide

The repository uses four tiers, each covering what the ones above it cannot:

- **Backend unit/integration/API tests** — `backend/tests/` (pytest)
- **Frontend unit tests** — `frontend/src/__tests__/` (Vitest + Testing Library)
- **End-to-end critical flows** — `backend/scripts/smoke_datasets_e2e.py`, a real
  server driven through register → upload → process → train → score →
  recommend → campaign → monitor → export → logout against a throwaway SQLite
  database.
- **Browser E2E** — `frontend/e2e/` (Playwright), a narrow suite for the flows
  that only exist in a browser: the refresh interceptor, whether signing out
  reaches the server, and a CSV actually arriving at the API as multipart. It
  builds the real bundle, serves it with `vite preview`, and boots the API
  against its own SQLite file, so it needs no PostgreSQL.

## Run backend tests

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Unit tests use an in-memory SQLite DB; PostgreSQL integration tests run against
`DATABASE_URL_TEST` when provided.

## Coverage

```bash
python -m pytest --cov=app --cov-report=term-missing:skip-covered
```

CI enforces a 90% floor. Overall coverage is ~94%. The deliberate gaps are the
LLM provider SDK adapters (`app/llm/providers.py`), which need network mocks to
exercise, and `app/tasks/celery_app.py`, which is Celery wiring.

## Test module map

| Module | Covers |
|---|---|
| `test_security.py` | Production config guard, security headers, rate limiter, token parsing |
| `test_auth_integration.py` | Register/login/refresh/logout, RBAC, deactivated users |
| `test_migrations.py` | Migration chain, downgrade symmetry, model/metadata index agreement |
| `test_token_revocation.py` | Refresh token issue, rotation, replay detection, family revocation, logout |
| `test_login_lockout.py` | Failure counting, cooldown, reset on success, window expiry, non-enumerating responses |
| `test_rate_limit_store.py` | Memory and Redis counters: TTL, namespacing, fallback, recovery |
| `test_prompt_injection.py` | Adversarial payloads through the analyst's prompt fences |
| `test_sql_injection.py` | Injection payloads against the SELECT-only guard chain |
| `test_tasks.py` | Celery task layer: tenant rejection, FAILED status, scheduled sweep |
| `test_drift_metrics.py` | PSI, KS, quantile binning, adaptive binning, edge cases |
| `test_monitoring.py` / `test_monitoring_api.py` | Baselines, persistence, isolation, RBAC, auditing |
| `test_reports.py` | Report catalogue, formats, filters, truncation, tenant scoping |
| `test_sql_validator.py` / `test_nl2sql.py` | NL-to-SQL guard chain (security-critical) |
| `test_campaigns.py` / `test_campaigns_api.py` | Targeting, simulation, A/B statistics |
| `test_churn.py` / `test_segmentation.py` / `test_explain.py` | Model training, metrics, SHAP |
| `test_processing.py` / `test_mapping_dq.py` / `test_feature_engineering.py` | Ingestion pipeline |
| `test_retention.py` / `test_engagement.py` / `test_analytics_api.py` | Recommendations, cohorts, journey, affinity |
| `test_predictions_api.py` / `test_segments_api.py` / `test_datasets_api.py` | Endpoint contracts |

## Run frontend tests

```bash
cd frontend
npm install
npm run lint        # eslint
npm run test        # vitest (src only; the Playwright specs are excluded)
npm run build       # tsc --noEmit && vite build
```

`tsconfig.json` includes `e2e/` and the tooling configs, so the browser tests are
type-checked by `npm run build` rather than only when Playwright happens to run.

## Run browser E2E

```bash
cd frontend
npm install
npx playwright install chromium   # once
npm run test:e2e                  # or: npm run test:e2e:ui
```

Set `PYTHON` if the backend interpreter is not `python` on `PATH` (for example a
virtualenv). The suite starts and stops both servers itself; ports live in
`frontend/e2e/backend-url.ts` so the specs and the config cannot disagree.

## Coverage targets by phase

| Area | Type |
|---|---|
| Preprocessing, RFM, CLV | unit |
| Segmentation, churn, SHAP | unit + integration |
| Auth & authorization | API + security tests |
| Session revocation, rotation, lockout | unit + integration + browser (Phase 13) |
| NL-to-SQL validation | unit (security-critical) |
| Recommendation engine | unit |
| Monitoring, drift, reports | unit + integration + API |
| Deployment config, headers, rate limiting | unit + API (Phase 12) |
| Critical UI flows | HTTP-level smoke + browser E2E (Phase 13) |
