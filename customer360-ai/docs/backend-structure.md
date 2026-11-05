# 5. Backend Module Structure

## 5.1 Structure
backend/
  app/
    api/v1/routers/ (routes grouped by domain)
    core/ (config, security, deps, constants, exceptions)
    models/ (SQLAlchemy models)
    schemas/ (Pydantic v2: request/response)
    repositories/ (DB access)
    services/ (business logic)
    ml/ (inference wrappers, registry)
    analytics/ (RFM, CLV, cohorts, journey)
    llm/ (provider abstraction, nl-to-sql, ai analyst)
    tasks/ (Celery tasks: training, processing)
    utils/ (helpers, validators)
    db/ (base, session, migrations via Alembic)
    main.py

## 5.2 Key Principles
- Clean architecture: API → Service → Repository → Models.
- DI via FastAPI Depends.
- Org isolation enforced in services/repos.
- RBAC by roles.
- Async where I/O heavy.
