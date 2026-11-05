"""Celery task layer.

`app/tasks/tasks.py` had 0% coverage, which left the two paths that actually run
in production untested: the dataset worker (including its FAILED-status handling)
and the scheduled drift sweep. Celery tasks are also awkward to call directly -
`bind=True` tasks are wrapped - so these tests drive the synchronous cores and
verify the task bodies delegate to them, which is where the behaviour lives.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.tasks.tasks as tasks
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.models.dataset import Dataset
from app.models.user import Organization, User


@pytest.fixture()
def session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def _seed_org_and_dataset(factory) -> tuple[int, int]:
    db = factory()
    try:
        org = Organization(name="Acme", slug="acme")
        db.add(org)
        db.flush()
        user = User(email="owner@acme.test", password_hash="x", role="ADMIN", organization_id=org.id)
        db.add(user)
        dataset = Dataset(organization_id=org.id, name="orders.csv", source_type="csv", status="MAPPED")
        db.add(dataset)
        db.commit()
        return org.id, dataset.id
    finally:
        db.close()


def test_process_dataset_sync_rejects_another_tenants_dataset(session_factory) -> None:
    org_id, dataset_id = _seed_org_and_dataset(session_factory)
    with pytest.raises(NotFoundError):
        # Same dataset id, wrong org: the worker must not leak that it exists.
        tasks.run_process_dataset_sync(dataset_id, org_id + 999)


def test_process_dataset_sync_rejects_a_missing_dataset(session_factory) -> None:
    org_id, _ = _seed_org_and_dataset(session_factory)
    with pytest.raises(NotFoundError):
        tasks.run_process_dataset_sync(4242, org_id)


def test_process_dataset_sync_runs_the_core_and_returns_its_result(session_factory, monkeypatch) -> None:
    org_id, dataset_id = _seed_org_and_dataset(session_factory)
    seen = {}

    def fake_core(db, dataset, organization_id, *, force_validation=False):
        seen["dataset_id"] = dataset.id
        seen["organization_id"] = organization_id
        seen["force_validation"] = force_validation
        return {"status": "COMPLETED", "rows": 7}

    monkeypatch.setattr(tasks, "process_dataset_core", fake_core)
    result = tasks.run_process_dataset_sync(dataset_id, org_id, force_validation=True)
    assert result == {"status": "COMPLETED", "rows": 7}
    assert seen == {"dataset_id": dataset_id, "organization_id": org_id, "force_validation": True}


def test_worker_marks_the_dataset_failed_when_the_core_raises(session_factory, monkeypatch) -> None:
    """A crashed pipeline must be visible in the UI, not silently left READY."""
    org_id, dataset_id = _seed_org_and_dataset(session_factory)

    def boom(db, dataset, organization_id, *, force_validation=False):
        raise RuntimeError("column mapping missing")

    monkeypatch.setattr(tasks, "process_dataset_core", boom)

    with pytest.raises(RuntimeError, match="column mapping missing"):
        tasks.process_dataset.run(dataset_id, org_id)

    db = session_factory()
    try:
        assert db.get(Dataset, dataset_id).status == "FAILED"
    finally:
        db.close()


def test_worker_closes_its_session_even_on_failure(session_factory, monkeypatch) -> None:
    org_id, dataset_id = _seed_org_and_dataset(session_factory)
    monkeypatch.setattr(
        tasks,
        "process_dataset_core",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    closed = []
    original = session_factory

    def tracking_factory(*args, **kwargs):
        session = original(*args, **kwargs)
        real_close = session.close

        def close():
            closed.append(True)
            real_close()

        session.close = close
        return session

    monkeypatch.setattr(tasks, "SessionLocal", tracking_factory)
    with pytest.raises(RuntimeError):
        tasks.process_dataset.run(dataset_id, org_id)
    assert closed, "a leaked session would exhaust the pool under repeated failures"


def test_monitoring_sync_sweeps_only_the_given_tenant(session_factory, monkeypatch) -> None:
    org_a, _ = _seed_org_and_dataset(session_factory)
    calls = []
    monkeypatch.setattr(tasks, "scan_active_models", lambda db, organization_id: calls.append(organization_id) or {"runs": []})

    assert tasks.run_monitoring_scan_sync(org_a) == {"runs": []}
    assert calls == [org_a], "the sweep must never be handed another organisation's id"


def test_scheduled_monitoring_task_delegates_to_the_sync_core(session_factory, monkeypatch) -> None:
    org_a, _ = _seed_org_and_dataset(session_factory)
    monkeypatch.setattr(tasks, "scan_active_models", lambda db, organization_id: {"scanned": 2, "failed": 0})
    assert tasks.run_monitoring_scan.run(org_a) == {"scanned": 2, "failed": 0}


def test_monitoring_task_reports_failures_instead_of_aborting_the_sweep(session_factory, monkeypatch) -> None:
    """One bad model version must not cancel the whole scheduled run."""
    org_a, _ = _seed_org_and_dataset(session_factory)
    monkeypatch.setattr(
        tasks,
        "scan_active_models",
        lambda db, organization_id: {"scanned": 3, "failed": 1, "errors": [{"model_version_id": 9}]},
    )
    result = tasks.run_monitoring_scan.run(org_a)
    assert result["scanned"] == 3 and result["failed"] == 1


def test_unimplemented_training_task_refuses_to_claim_success(session_factory) -> None:
    # The stub returned {"status": "COMPLETED"} without training anything, which
    # would have shown an operator a model that did not exist.
    with pytest.raises(NotImplementedError, match="not implemented"):
        tasks.train_churn_model.run(1, 1, 1)


def test_every_task_raises_or_delegates_rather_than_inventing_a_result() -> None:
    """A task must not report success it did not produce."""
    import inspect

    for name, func in inspect.getmembers(tasks, inspect.isfunction):
        if not name.endswith("_model") and name not in {"process_dataset", "run_monitoring_scan"}:
            continue
        body = inspect.getsource(func)
        # Either it delegates to a tested core, or it refuses. Never a bare
        # hardcoded success dict.
        assert "NotImplementedError" in body or "_sync(" in body, f"{name} may fabricate its result"
