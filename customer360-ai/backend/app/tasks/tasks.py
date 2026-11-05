"""Task definitions. Implemented incrementally per the roadmap."""
from __future__ import annotations

from celery import shared_task

from app.core.logging_config import get_logger
from app.db.session import SessionLocal
from app.models.dataset import Dataset
from app.services.monitoring_service import scan_all_active as scan_active_models
from app.services.processing_service import process_dataset_core

logger = get_logger()


def run_process_dataset_sync(dataset_id: int, organization_id: int, *, force_validation: bool = False) -> dict:
    """Synchronous processing path (used by the API fallback and by tests)."""
    db = SessionLocal()
    try:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None or dataset.organization_id != organization_id:
            from app.core.exceptions import NotFoundError

            raise NotFoundError("Dataset not found.")
        return process_dataset_core(db, dataset, organization_id, force_validation=force_validation)
    finally:
        db.close()


@shared_task(bind=True)
def process_dataset(self, dataset_id: int, organization_id: int, force_validation: bool = False) -> dict:
    """Async worker that runs the same core pipeline as the sync path."""
    db = SessionLocal()
    try:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None or dataset.organization_id != organization_id:
            from app.core.exceptions import NotFoundError

            raise NotFoundError("Dataset not found.")
        try:
            result = process_dataset_core(db, dataset, organization_id, force_validation=force_validation)
        except Exception:
            dataset.status = "FAILED"
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            raise
        return result
    finally:
        db.close()


@shared_task(bind=True)
def train_churn_model(self, organization_id: int, model_id: int, dataset_version_id: int) -> dict:
    """Not wired up.

    Phase 5 trains through the API (`POST /models/train`) rather than the queue.
    This task used to return `{"status": "COMPLETED"}` without training anything,
    which would have told an operator a model existed when none did - the same
    fabricated result this project refuses elsewhere. It now fails loudly instead,
    and nothing calls it.
    """
    raise NotImplementedError(
        "train_churn_model is not implemented; train via POST /api/v1/models/train so the "
        "synchronous path and the audit trail stay in one place."
    )


def run_monitoring_scan_sync(organization_id: int) -> dict:
    """Synchronous monitoring path (used by the API fallback and by tests)."""
    db = SessionLocal()
    try:
        return scan_active_models(db, organization_id)
    finally:
        db.close()


@shared_task(bind=True)
def run_monitoring_scan(self, organization_id: int) -> dict:
    """Phase 11: PSI drift checks for active models.

    Sweeps every active model version for the organisation and records one
    `MonitoringRun` per version. One failing version is reported in the result
    rather than aborting the sweep.
    """
    logger.info("monitoring_scan_start", organization_id=organization_id)
    return run_monitoring_scan_sync(organization_id)