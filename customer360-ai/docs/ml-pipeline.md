# 6. ML Pipeline Architecture

## 6.1 Preprocessing
- ColumnTransformer + Pipeline (sklearn), persist preprocessing_path per model version.
- Type handling, encoding, scaling, imputation; no data leakage.
- Same transform at train and inference.

## 6.2 RFM + Features
- RFM from transactions (recency/frequency/monetary, configurable quantiles).
- Feature engineering: AOV, avg time between, tenure, category diversity, discount usage.

## 6.3 Segmentation (ML)
- K-Means on the latest Customer 360 feature matrix (recency/frequency/monetary/AOV/avg-days-between/tenure/category-diversity/discount-usage/engagement/CLV).
- Pipeline: median impute → drop constant columns → StandardScaler → optional PCA (≤4 components) → K-Means (n_init="auto", reproducible seed).
- Auto-k: silhouette over 2..8; caller can pin 2..8; guard of ≥8 buyers before training.
- Artifacts (fitted prep + PCA + model, joblib) persisted per (org, model, version) under `settings.model_artifact_dir`; Model/ModelVersion registry rows with metrics/params/features; previous versions deactivated.
- ML segments written to `segments` (type="ML", name "Cluster i", color, auto profile, rules_json with scaled centroid ranges); every buyer's CustomerSegment assignment is replaced wholesale each train.
- Segment analytics: per-metric avg/median, top channels, top categories by revenue, top members by CLV, repeat-purchase share. Clusters are user-renameable; profile text is computed from per-cluster means in original feature space (PCA-safe).
- Frontend: /segmentation page with train form + cluster analytics cards.

## 6.4 Churn
- Label: a buyer is churned when their `recency_days` (latest Customer 360 features, as of the org's latest order date) exceeds `churn_window_days` (config, default 90). Deterministic, no future leakage.
- Features: the same 10 Customer 360 metrics used by segmentation (recency, frequency, monetary, AOV, avg-days-between, tenure, category diversity, discount usage, engagement, CLV); buyers with frequency ≤0 skipped; all-NaN columns zeroed.
- Guardrails: ≥`MIN_SAMPLES_FOR_TRAINING` (16) buyers and both churn classes present, else `InsufficientDataError`.
- Families: logistic regression, random forest, XGBoost, LightGBM (`AVAILABLE_FAMILIES`); each family trains on the same stratified 80/20 split with an imputer (median) + StandardScaler pipeline fitted on the training split once and applied identically at fit AND predict (consistent scaling, no leakage).
- Imbalance: logistic/random-forest use `class_weight="balanced"`; xgboost/lightgbm use `scale_pos_weight = n_neg/n_pos`.
- Metrics per family: Accuracy/Precision/Recall/F1, confusion matrix (tn/fp/fn/tp), ROC-AUC and PR-AUC (None if single class on test).
- One Model row ("Churn Prediction") per org holds one ModelVersion per family per run (version_no incremented); every run deactivates the model's prior versions and activates the champion (max ROC-AUC, tie-break PR-AUC).
- Artifacts: joblib bundle `{imputer, scaler, model, feature_columns, family, churn_window_days, trained_at, metrics}` under `model_artifact_dir/{org}/{model}/v{version}`; version params/features/metrics recorded.
- Scoring (`score_customers`): loads the active version's bundle, applies the same imputer+scaler, deletes existing Prediction rows for that version then inserts one per customer (probability, risk bin, label = proba ≥ 0.5).
- Risk bins from `churn_risk_thresholds` sorted ceilings: LOW ≤0.35, MEDIUM ≤0.60, HIGH ≤0.80, else CRITICAL.
- Risk categories (CLV × churn matrix): PRIORITY (high risk + CLV ≥ org median), WIN_BACK (high risk + CLV < median), GROW (low risk + high CLV), MONITOR (low risk + low CLV); each at-risk item and the Customer 360 `risk` block carries `risk_category`, and `value_at_risk = churn probability × CLV` is surfaced in both. The at-risk payload adds `median_clv` and a `summary` block (total value at risk, counts by risk level).
- At-risk list: predictions of the active version filtered by `min_risk_level` (default HIGH), top probability first, display name falls back to external id / email / #id.
- Frontend: /models page — family/window/seed train form, per-version family badge + metrics + confusion matrix, manual Activate, "Score all customers", at-risk table with risk filter.

## 6.5 Explainability
- SHAP (app/ml/explain.py): tree families use `shap.TreeExplainer` (RandomForest output normalized to the churn class via `values[..., 1]`); logistic uses `shap.LinearExplainer` over a ≤100-row background sample of the scaled matrix. Input is the active version's same imputer+scaler-transformed feature matrix.
- Local: per-customer signed contributions (model-output units) plus a `share` = |contribution| / Σ|contributions| used for ranking drivers; stored per prediction in `explanations.local_contrib_json` with the base value and family.
- Global: mean |SHAP| per feature across explained predictions, normalized to shares, stored in `global_importance_json` and returned as a ranked list by POST /predictions/churn/explain.
- Persistence: one `Explanation` row per Prediction (`prediction_id` NOT NULL); rescoring a version deletes its old Prediction rows (cascade removes stale explanations) and re-explaining reprovides them. GET /customers/{id}/explanation reads the stored row or computes on demand if absent; 404 when the customer has no scored prediction. Degenerate models (e.g., LightGBM collapsing to a constant stump on tiny data) legitimately yield all-zero contributions.
- Frontend: /at-risk board "Explain drivers (SHAP)" bulk action + per-row expandable driver panel; Customer 360 page renders its own SHAP driver panel.

## 6.6 CLV
- Transparent baseline (AOV*freq*lifespan / churn-based) with documented formula; CLV×Churn matrix feeds `risk_category` and `value_at_risk` (see 6.4).

## 6.7 What-if
- app/services/whatif_service.py: loads the active churn bundle (imputer + scaler + model), clones the customer's latest feature row, applies a `changes` dict or a named preset (re_engage, increase_frequency, raise_basket, reduce_discount, boost_engagement, diversify_categories), transforms through the same imputer+scaler, and re-predicts. Returns baseline vs scenario (probability, risk_level, risk_category, value_at_risk) + delta; inputs validated against CHURN_FEATURES (non-negative, finite). Results are labeled as hypotheses — never guarantees. Audits via WHATIF_SIMULATE. RBAC ADMIN/ANALYST via POST /customers/{id}/what-if.

## 6.8 Retention recommendation engine
- app/services/retention_service.py: deterministic next-best-action layer over churn output. Eight rules with fixed priorities (e.g. win_back on high proba + long recency + low frequency, concierge_save on high proba + high CLV, cycle_reduce on low frequency + high avg gap, loyal/nourish for healthy customers). priority_score = proba×100×priority_weight + min(CLV/median,1)×priority_weight×10; priority buckets HIGH ≥80 / MEDIUM ≥50 / LOW. Reasons cite the top SHAP driver when an explanation exists, else the scoring features. Persisted as type=RETENTION rows and replaced wholesale per (org, model) — regenerating is idempotent and reflects the latest scores.

## 6.9 Engagement analytics
- app/services/engagement_service.py: deterministic, no-training analytics over the processed ledger.
  - `cohort_retention`: each buyer's first purchase month defines the cohort; per-lag retention = share of cohort customers with an order in that lag month. Only lags fully elapsed since the cohort's start are reported (no partial-tail bias). Summary adds repeat_share (customers with ≥2 orders), avg/median orders per customer.
  - `customer_journey`: lifecycle stage from order count + recency vs the dataset as-of date (max order_date) — NEW (1 order, ≤45d), EARLY (≥2, ≤45d), REGULAR (≥4, ≤45d), AT_RISK (≤120d), DORMANT (≤180d), LOST (>180d). Milestone funnel (1/2/3/5/10+ orders) + time-to-second-purchase buckets with avg/median.
  - `product_recommendations`: association analysis with customer-basket semantics, because the ledger enforces one product per order (unique `(organization_id, external_order_id)`) — a co-purchase is a customer who bought both products. confidence = co_customers/buyers(a), lift = confidence / buyers(b)/all_buyers; ranking score = co×20 + confidence×100 + lift. Item-to-item (per product), personalized (per customer, excluding products they already own), or global top pairs.

## 6.10 Training
- Async Celery tasks; track status (queued/running/completed/failed); artifact + preprocessing + metadata.

## 6.11 AI analyst + NL-to-SQL
- Provider abstraction: `app/llm/{base,factory,providers}.py` - services never import provider SDKs; `LLM_PROVIDER=openai|gemini|mock`, unknown or missing keys fall back to the honest `MockProvider` (it never invents numbers). With no provider configured, open-ended questions are refused rather than answered with fiction.
- Grounded analyst (`app/llm/ai_analyst.py`): the question is keyword-routed to a bounded set of retrieval views (dashboard KPIs always, plus churn board, RFM, CLV, coverage, trends, retention playbook, cohorts, journey, product affinity). Each block is labelled and passed to the model with instructions to cite only those figures and to refuse when the context cannot answer. Multi-turn history (last 6 turns) is rendered for follow-ups. Retrieval failures degrade per-block, never 500 the endpoint. `GET /ai/context` exposes the routing for auditability.
- NL-to-SQL (`app/llm/nl2sql.py`): two generation paths - nine curated, pre-scoped intent templates (customer counts, top customers, at-risk, lapsed, revenue by month, category performance, recent orders, RFM mix, retention actions) so the feature works offline, and LLM generation against the curated schema in `app/llm/sql_schema.py`. Both outputs pass the guard chain in `app/llm/sql_validator.py`: SELECT-only, no stacked statements/comments, forbidden keyword blocklist, table allow-list, tenant scope enforcement (auto-injected for single-table queries; joins must constrain every table explicitly), subquery rejection, row cap. Execution uses a best-effort `statement_timeout` on Postgres, wraps driver errors, and returns at most 200 rows.

## 6.12 Campaign targeting, simulation, and experiments
- No training involved: targeting reads the newest feature + prediction rows, and the statistics are closed-form. That is deliberate - a campaign decision must be explainable line by line.
- Audience resolution (`app/services/campaign_service.py`): inclusive selectors (`segment_ids`, `customer_ids`) choose the population, every other filter key narrows it (rfm_segments, risk_levels, channels, min/max_clv, min_frequency, min/max_recency_days, exclude_customer_ids). The vocabulary is an explicit allow-list: an unknown key is rejected at the router (`extra="forbid"`) and again in the service, and an empty inclusive list is rejected rather than read as "no selector" - otherwise a client typo would target the entire tenant. Customer ids are re-checked against the caller's organization, so another tenant's ids cannot be smuggled in through the selector.
- Missing data is not a pass: a customer with no feature row fails a numeric lower bound (`min_clv`, `min_frequency`, `min_recency_days`), because "we don't know this customer's value" must not be reported as "this customer is valuable".
- Launch snapshots the audience into `campaign_members`. Feature rebuilds afterwards do not move a running campaign's population.
- A/B assignment is `sha256(campaign_id:customer_id)` bucketed against `variant_split` - deterministic across retries, workers, and restarts. Nothing stores the arm as authoritative state; reads recompute it.
- Simulation is a forecast, never a promise: the caller's `response_rate` is scaled per risk level (CRITICAL 0.22 / HIGH 0.16 / MEDIUM 0.10 / LOW 0.05 / unscored 0.09), averaged across the audience, then combined with `conversion_rate` and a revenue-per-conversion that defaults to the audience's median CLV. It returns a binomial confidence band on responses, ROI, and the break-even response rate, plus an experiment power preview so a small audience shows up as "not conclusive" *before* the launch rather than after.
- `app/services/experiment_stats.py` is dependency-free on purpose (two-proportion z-test, Wilson score intervals, required sample size per arm, binomial bands) so any reported number can be recomputed from the standard library, with no hidden scipy/numpy version behind the number. Verdict text is explicit that "not significant" means "treat as equivalent, or wait for more data" - never "the offer is worthless".
- The treatment lift prior (+10% response per 100% discount depth) is an assumption, stated as such, and it can be overridden per simulation.

## 6.13 Monitoring, drift, and reports
- **What counts as the baseline.** Feature drift compares the latest feature rows against the rows of `ModelVersion.train_dataset_version_id`. That is the honest train-time baseline, because the churn and segmentation trainers both read "latest features" and the version records which dataset version was newest when they ran. Population is buyers only (`frequency > 0`) - monitoring a different population than the model was fitted on would manufacture drift.
- **Prediction drift needs a different baseline.** `score_customers` deletes and recreates `predictions` rows, so there is no train-time prediction population to compare against. The earliest recorded `MonitoringRun` for a version therefore becomes the baseline, and the response says so (`baseline_source: first_monitoring_run`) instead of implying a longer history than exists. The first scan reports `UNKNOWN` with "this run becomes the baseline" - a baseline established silently would be indistinguishable from a comparison that was actually performed.
- **PSI, plus a cross-check.** `app/services/drift_metrics.py` implements PSI binned on reference quantiles with `±inf` outer sentinels (so current values outside the training range stay in the edge bins rather than being dropped), and a two-sample KS statistic alongside it. KS is binning-free, so it confirms or contradicts PSI rather than sharing its assumptions. Thresholds are config, not constants: `MODERATE 0.10`, `SIGNIFICANT 0.25`.
- **Adaptive binning.** A fixed 10-bin PSI is biased upwards on small samples - sparse bins amplify noise, and a 120-row comparison of two identical distributions can read 0.28. `effective_bins()` reduces the grid to `max(2, min(drift_bins, n_reference // monitoring_min_per_bin))`, which measurably lowers the mean PSI of null comparisons. Below `monitoring_min_samples` (30) no verdict is issued at all: the status is `UNKNOWN` with the reason attached.
- **Never fabricate a number.** Missing baseline, constant reference, or an under-sized sample yields `NO_DATA` / `UNKNOWN` and a `reason` string. `None` propagates through the API rather than collapsing to `0.0`, because "no drift" and "cannot measure" lead to opposite operational decisions.
- **Persistence is what makes it monitoring.** `MonitoringRun` (migration `0002`) stores the per-feature metrics, both verdicts, both sample sizes, the baseline run id, the duration, and the health block. Without it every request would recompute from scratch and there would be no trend and no prediction baseline. A failed scan is stored as a `FAILED` row carrying the error, so a broken scan is visible rather than missing.
- **Reports share the same honesty rules.** Every builder filters on `organization_id`; filter vocabularies are validated per report with unknown keys rejected; builders fetch one row past the cap so a truncated export is reported as truncated instead of looking like an exactly-full result. PDF is reported `not_available` rather than advertised and then refused.
