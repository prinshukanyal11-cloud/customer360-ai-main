# Customer360 AI — Backend

FastAPI application with a clean layered architecture:

```
app/
├── api/v1/routers/     # REST endpoints (thin)
├── core/               # config, security, deps, exceptions, constants
├── models/             # SQLAlchemy ORM models
├── schemas/            # Pydantic v2 request/response models
├── repositories/       # data-access layer (org-scoped)
├── services/           # business logic layer
├── ml/                 # model registry + inference wrappers
├── analytics/          # RFM, CLV, cohorts, journey, features
├── llm/                # provider abstraction, nl-to-sql, ai analyst
├── tasks/              # Celery async jobs
├── db/                 # engine/session/base + Alembic config
├── utils/              # helpers
└── main.py             # FastAPI app factory
```

## Local run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# .venv/bin/activate            # macOS/Linux
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

## Tests

```bash
pytest -q
```