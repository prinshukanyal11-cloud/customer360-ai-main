"""Reusable preprocessing pipelines (Phase 2/5).

- Missing-value imputation (numerical/categorical)
- Duplicate handling and date parsing
- Categorical encoding, scaling, outlier capping
- Built with scikit-learn Pipeline / ColumnTransformer, fitted only on
  training folds to prevent data leakage.
- The fitted pipeline is persisted with each model version and replayed
  unchanged at inference.
"""
from __future__ import annotations


def build_preprocessing_pipeline() -> None:
    """Placeholder: constructed by ml.preprocessing once Phase 2 lands."""
    raise NotImplementedError("Implemented in Phase 2.")