# 8. Security Architecture

## 8.1 Threat Model
- Authentication bypass, authorization crossing (org leakage), injection (SQL/XSS), file upload abuse, secret exposure, prompt injection.

## 8.2 Controls
- Passwords: bcrypt (strong hash). Argon2 was considered and rejected because passlib's bcrypt backend is the maintained path here.
- Auth: JWT access + refresh; protected routes; RBAC (ADMIN/ANALYST/VIEWER). Token `sub` is validated as a positive integer at decode time, so a malformed token is a 401 rather than an unhandled error.
- Sessions: refresh tokens are hashed, rotated, and revocable; replayed tokens take down their whole family; sign-out revokes server-side. See 8.4.
- Brute force: per-address lockout on top of rate limiting, with responses that do not disclose whether an account exists. See 8.4.
- Org isolation: all queries force org_id; service layer checks.
- Input validation: Pydantic v2; file type/extension/size limits; CSV parsing with sanitize.
- SQL injection: ORM + parameterized; NL-to-SQL SELECT-only matcher, token-level allowlist, readonly role creds, row limits.
- Prompt injection: retrieved text is fenced and stripped of fence markers before it reaches the model; fuzz-tested. See 8.4.
- Secrets: 100% env-based; .env.example; never committed.
- Headers/CORS, rate limiting, HTTPS termination, audit log, secure headers.
- No API keys embedded in frontend.

## 8.3 Phase 12 Review

The Phase 12 review compared every claim above against the code. Three controls were documented but not implemented, and two defects were found. All five are fixed; the controls now exist and have tests.

### Was documented, was not implemented

| Control | Finding | Resolution |
|---|---|---|
| Rate limiting | `rate_limit_enabled` / `rate_limit_per_minute` existed in `config.py` and `.env.example`, but **nothing read them**. No limiter existed anywhere in the app. | `app/middleware/rate_limit.py`. Fixed window per client, with `/auth/*` on a tighter budget (`auth_rate_limit_per_minute`, default 10) because login is the only endpoint where a credential can be guessed. 429 carries `Retry-After` and the standard error envelope. |
| Secure headers | **No header middleware existed.** Not one of `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` was set. | `app/middleware/security_headers.py`. HSTS only in production; CSP only on the docs routes, because Swagger UI loads from a CDN and a global CSP would either break it or weaken every JSON response for nothing. |
| No default secrets in production | `jwt_secret` and `secret_key` both defaulted to the literal `"change-me"`. A deployment that forgot to set them **signed tokens with a key published in this repository** - a complete authentication bypass, and the app started without a murmur. | `Settings._reject_insecure_production_config` refuses to boot when `APP_ENV=production` and any of: a placeholder or <32-char secret, `DEBUG=true`, `*` in `CORS_ORIGINS`, or an empty `DATABASE_URL_RO`. Development still runs on the shipped defaults, so a fresh clone needs no ceremony. |

### Defects found

- **Malformed token returned 500, not 401.** `get_current_user` and `/auth/refresh` did `int(payload["sub"])` on unvalidated claims, so a token with a non-numeric or missing subject raised an unhandled `ValueError`/`KeyError` and surfaced a 500 with a stack trace under `DEBUG`. `decode_token` now validates the subject once, for every caller.
- **`train_churn_model` reported a model it never trained.** The Celery task returned `{"status": "COMPLETED"}` without training anything, which would have shown an operator a model that did not exist. It now raises `NotImplementedError`; nothing called it. This is the one place the codebase was telling a comfortable lie rather than admitting a gap, and `tests/test_tasks.py` now asserts that no task returns a hardcoded success.

### Limiter scope, stated plainly

The counter store is selectable: `rate_limit_backend` is `auto` (default), `memory`, or `redis`. With `redis` the budget is a property of the deployment rather than of one process, and with N replicas behind a proxy every replica reads and writes the same key. `auto` uses Redis when it is reachable and falls back to memory when it is not.

Two limitations worth knowing before you rely on it:

- **Redis degrades, it does not fail closed.** If the store is unreachable the limiter falls back to per-process counting, so the API keeps serving (an outage in a dependency should not become an outage in the product) at the cost of the budget being per-process again. `RATE_LIMIT_REDIS_PREFIX` namespaces keys so a shared Redis is not shared by accident.
- **The window is fixed, not sliding.** A caller can spend close to 2x the budget across a window boundary. That is acceptable for a coarse guard; it is not a substitute for a per-account limit.

`X-Forwarded-For` is **ignored by default**. Trusting it unconditionally would let any direct caller set the header and escape the limit entirely, which would make the control theatre. `rate_limit_trust_proxy=true` opts in and is only safe when the app is unreachable except through that proxy (the shipped `docker/nginx.conf` topology).

### Sessions: revocation, rotation, and lockout

Refresh tokens used to be un-revocable, and the Phase 12 review listed that as open. It is now a token store, and the semantics are worth stating because they interact:

- **Storage.** `refresh_tokens` holds only a SHA-256 hash of the token, plus `jti`, `user_id`, `org_id`, `family_id`, `expires_at`, `revoked_at`, and `revoked_reason`. A database dump does not yield usable tokens.
- **Rotation.** `POST /auth/refresh` returns a new pair and revokes the presented token (`rotated`). Presenting a token twice therefore means either a client bug or a stolen token, and the backend cannot tell which.
- **Replay detection.** Presenting an already-revoked token revokes the entire `family_id` and requires a fresh sign-in. This is the standard trade: it converts an undetected theft into a forced re-login instead of a silent compromise. The cost is that a client which fires two refreshes concurrently logs the user out - which is why the axios interceptor shares one in-flight refresh across all requests.
- **Logout.** `POST /auth/logout` revokes every live refresh token for the user (`logout`). The frontend calls it; clearing `localStorage` alone would leave a token valid for its full lifetime.
- **Not covered.** Access tokens are stateless and are not tracked in the store, so an access token stays valid until `access_token_expire_minutes` (default 30) even after logout. Shortening that window shortens the exposure.

`LoginFailure` rows provide per-address lockout (`login_lockout_*`): after `login_lockout_threshold` failures inside `login_lockout_window_minutes`, further attempts get a generic locked response for `login_lockout_minutes`. Two deliberate choices:

- Failures are keyed on the **normalised submitted address**, not on whether the account exists, and both bad-password and locked responses are worded identically. An attacker cannot use the message to enumerate registered addresses, and cannot lock an address they have not tried.
- The threshold is generous (default 5). Lockout is a denial-of-service vector by nature - anyone who can reach a login form can lock a colleague out - so the limit is set where it stops guessing and not where it starts inconveniencing.

### Prompt injection

Mitigation is by construction and is now fuzzed, not just asserted. `app/llm/ai_analyst.py` wraps untrusted text in `CONTEXT_FENCE` / `HISTORY_FENCE` / `QUESTION_FENCE` markers, and `neutralise_untrusted()` strips newlines and any attempt to emit a fence marker before the payload reaches the prompt, so retrieved records cannot close the block they were quoted in. `tests/test_prompt_injection.py` drives those payloads through the real fence builder.

The residual risk is the one no amount of string handling removes: a sufficiently persuasive retrieved document can still influence the model's *interpretation* of a question it legitimately answered. The fences make the boundary explicit and defeat the structural attacks; they are not a claim that the model cannot be misled. NL-to-SQL keeps its separate SELECT-only guards as the actual containment boundary - see `app/llm/sql_validator.py`, whose single entry point is `apply_readonly_guards()`.

### Verification

- `tests/test_security.py` - 29 tests over the config guard, headers, limiter, and token parsing.
- `tests/test_rate_limit_store.py` - memory and Redis stores against a fake Redis: counting, TTL, key namespacing, fallback, recovery, and outage re-probe.
- `tests/test_token_revocation.py` - issue, rotate, replay detection, family revocation, logout, pruning.
- `tests/test_login_lockout.py` - threshold, cooldown, reset on success, window expiry, pruning, and non-enumerating responses.
- `tests/test_prompt_injection.py` - adversarial payloads through the real prompt fence.
- `tests/test_sql_injection.py` - injection payloads against `apply_readonly_guards()`.
- `tests/test_auth_integration.py` - refresh/logout/deactivated-user paths, which had **zero** coverage before Phase 12.
- `tests/test_migrations.py` - migration/metadata index agreement, the local proxy for the `alembic check` that CI runs against real PostgreSQL.
- `frontend/e2e/auth.spec.ts` - browser tests for the refresh interceptor, server-side logout, and family revocation. This is the only layer that can prove the client stores the *rotated* token rather than the one it just spent.
- `frontend/src/__tests__/api-client.test.ts` - asserts that `FormData` survives axios's `transformRequest` as multipart. A default `Content-Type: application/json` on the instance makes axios serialise the form to `{"file":{}}`, which FastAPI rejects with 422 before any handler runs; the test fails if that default is ever reintroduced.
- `frontend/e2e/dataset-upload.spec.ts` - drives a real file input in a real browser and requires a 201 with the parsed row count. This is the only layer that sees the original symptom end to end, because a flattened body never reaches the upload handler.
- CI runs `pip-audit --strict`, `npm audit --audit-level=high`, and a `gitleaks` scan over full history.

### Deliberately out of scope

- **PDF report branding / export.** The report catalogue reports `pdf_status: not_available` rather than advertising a format the server would reject. `reportlab` was removed from `requirements.txt` so the dependency returns with the feature.
- **Serving the API behind the frontend origin.** The browser calls the API cross-origin, so the CORS allow-list and the deployed API host both matter. A same-origin reverse proxy would remove a class of deployment mistakes but changes the deployment topology.
