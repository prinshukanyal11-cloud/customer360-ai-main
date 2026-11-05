# 3. API Architecture

## 3.1 Base
FastAPI, versioned /api, Pydantic schemas, consistent error envelope, RBAC, org isolation, request validation.

## 3.2 Auth
All paths are relative to `/api/v1`.

POST /auth/register -> 201 `{access_token, refresh_token, token_type}`
POST /auth/login -> 200 `{access_token, refresh_token, token_type}`
POST /auth/refresh (body `{refresh_token}`) -> 200 `{access_token, refresh_token, token_type}`
POST /auth/logout -> 204 (revokes every live refresh token for the caller)
GET /auth/me -> 200 current user

Refresh tokens are single-use. `POST /auth/refresh` returns a new pair and revokes the presented token; presenting an already-revoked token revokes that token's whole rotation family and returns 401. Callers must persist the `refresh_token` from every refresh response - keeping the old one guarantees a 401 on the next attempt.

Login failures are counted per address. After `login_lockout_threshold` failures in `login_lockout_window_minutes`, login returns 401 with a locked message for `login_lockout_minutes`. The message is identical whether the address is registered, not registered, or locked, so it cannot be used to enumerate accounts.

See `docs/security.md` §8.4 for the revocation and lockout semantics, and the limits on what logout does *not* cover.

## 3.3 Dashboard
GET /api/dashboard/summary (KPIs: customers, revenue, churn rate, avg CLV, repeat rate, at-risk, revenue at risk)

## 3.4 Datasets (live under /api/v1/datasets)
POST /datasets/upload (multipart CSV — inspect columns, preview, mapping suggestion; re-upload of the same name creates a new version)
GET /datasets (paginated list, optional status filter)
GET /datasets/{id} (detail, current mapping + version history)
GET /datasets/{id}/mapping-suggestion (auto-inferred canonical mapping)
POST /datasets/{id}/map-columns (validate + persist mapping; RBAC ADMIN/ANALYST)
POST /datasets/{id}/validate (run DQ checks, store score + report)
POST /datasets/{id}/process (validate + clean + idempotent upsert into warehouse; with a broker this same core runs in Celery, otherwise inline; on success it also (re)builds the Customer 360 feature store and returns `feature_summary`)
GET /datasets/{id}/quality (latest DQ report for the dataset)
Viewers are read-only; upload/map/validate/process require ADMIN or ANALYST.

## 3.5 Customers
GET /api/customers (paginated, filters, server-side; each row now includes rfm_score, rfm_segment, clv_est)
GET /api/customers/{id} (360 — adds `behavioral` block: aov, tenure_days, avg_days_between, category_diversity, discount_usage, engagement_score; expanded RFM breakdown, per-transaction discount/channel; `value` includes value_at_risk = churn probability × CLV; `risk` adds risk_category)
GET /api/customers/{id}/features
GET /api/customers/{id}/explanation (SHAP explanation for the customer's latest churn prediction; computes+predicts on demand if not yet stored; 404 when the customer has no scored prediction)
POST /api/customers/{id}/what-if (RBAC ADMIN/ANALYST; body: {scenario?: "re_engage"|"increase_frequency"|"raise_basket"|"reduce_discount"|"boost_engagement"|"diversify_categories", changes?: {feature: number}} where feature keys ⊆ churn features; re-scores the active churn model on the perturbation via the same imputer+scaler; returns baseline + scenario_output (probability, risk_level, risk_category, value_at_risk) with risk_level_changed + delta (probability/value_at_risk); 422 ValidationError for unknown/negative features, InsufficientDataError when no active model or the customer is an unscored non-buyer; labeled as hypothesis only)

## 3.6 Analytics (live under /api/v1/analytics)
GET /api/analytics/features-summary (feature store coverage: buyers, total customers, coverage ratio, as-of date)
GET /api/analytics/rfm (segment mix: {segment, customer_count, share})
GET /api/analytics/clv (avg/median/total CLV, percentiles p10–p90, 10 revenue buckets)
GET /api/analytics/trends?months=12 (monthly orders/revenue/active customers)
POST /api/features/build (ADMIN/ANALYST — recompute + persist RFM/behavioral features for all buyers)
GET /api/features/summary (same payload as features-summary)
GET /api/v1/analytics/cohorts?months=1..36 (monthly acquisition cohorts + retention curve per lag; summary: repeat_share, avg/median orders per customer, cohorts_analyzed, total_customers, as_of; only completed lags are reported)
GET /api/v1/analytics/journey (lifecycle stage mix NEW/EARLY/REGULAR/AT_RISK/DORMANT/LOST, milestone funnel 1/2/3/5/10+, time-to-repeat buckets + avg/median, avg orders per customer, as_of)
GET /api/v1/analytics/products?product_id=&customer_id=&limit=1..20 (affinity basis: customer-basket co-purchases with confidence + lift; item-to-item list for a product, personalized list for a customer (plus customer_purchased_products), or global top co-purchase pairs; unknown product/customer -> 404)

## 3.7 Segmentation (live under /api/v1/segments; RBAC ADMIN/ANALYST for train + rename)
GET /api/segments?segment_type=RFM|ML (all segments for the org with live member counts)
POST /api/segments/train (body: {n_clusters?: 2..8, use_pca?: bool, random_seed?: int}; builds latest-feature matrix, impute+scale, optional PCA, K-Means; returns segments/profile list; replaces all ML customer assignments; requires ≥8 buyers)
GET /api/segments/{id}/clusters (stats averages/medians, channels, categories, top members by CLV, repeat-purchase share)
PATCH /api/segments/{id} (rename / description / color)

## 3.8 Models & Predictions (live under /api/v1; churn + activate require ADMIN/ANALYST, list/at-risk any role)
GET /api/models (all registered models + per-version family/metrics/active; churn versions expose `family` and `metrics`)
POST /api/models/churn/train (body: {families?: ["logistic","random_forest","xgboost","lightgbm"], churn_window_days?: 1..365, random_seed?: int}; requires ≥16 buyers with both churn classes; trains each family on a shared stratified split, persists one ModelVersion per family, auto-activates the champion by ROC-AUC/PR-AUC; returns `champion` + `families` list)
POST /api/models/{model_id}/versions/{version_id}/activate (deactivates other versions of the model, activates this one; RBAC ADMIN/ANALYST)
GET /api/models/{model_id}
GET /api/models/{model_id}/versions
POST /api/predictions/churn (body: {customer_ids?: list[int]}; scores buyers (or a subset) with the active churn model, overwrites Prediction rows; returns `n_scored` + per-customer probability/risk_level; 422-style InsufficientDataError if no active churn model)
POST /api/predictions/churn/explain (body: {customer_ids?: list[int]}; computes SHAP for the active model's predictions (or a subset), persists one Explanation row per prediction replacing previous; returns `family`, `base_value`, `global_importance` (features ranked by mean |SHAP| with share), and per-customer `items` with signed contributions; RBAC ADMIN/ANALYST; InsufficientDataError when no active model or no predictions)
GET /api/predictions/at-risk?min_risk_level=HIGH&page=1&page_size=50 (any role; returns HIGH/CRITICAL (or above the filter) customers with display name fallback, probability, risk level, `clv_est`, `value_at_risk` = probability × CLV, `risk_category`; plus `median_clv` and `summary` — total_value_at_risk, by_risk_level counts; empty when no active churn model)
Risk bins: LOW ≤0.35, MEDIUM ≤0.60, HIGH ≤0.80, CRITICAL otherwise (churn_risk_thresholds).
Risk categories (CLV × churn matrix): PRIORITY = high risk + CLV ≥ org median, WIN_BACK = high risk + CLV < median, GROW = low risk + high CLV, MONITOR = low risk + low CLV.

## 3.9 Recommendations (live under /api/v1/recommendations; any role can list, ADMIN/ANALYST generate)
POST /api/recommendations/generate (body: {customer_ids?: list[int], min_risk_level?: "MEDIUM"}; rule-based next-best-action engine on the active churn model's predictions: 8 rules (WIN_BACK_OFFER, CONCIERGE_SAVE, CYCLE_REDUCE, LOYALTY_PROGRAM, REENGAGEMENT, CROSS_SELL, SUBSCRIPTION_PUSH, NOURISH) keyed off score_payload features + SHAP top driver; priority_score = churn prob × rule weight × 100 + min(CLV/median, 1) × weight × 10; priority buckets HIGH ≥80 / MEDIUM ≥50 / LOW; replaces all RETENTION rows for the org (regenerate = idempotent refresh); returns n_generated + items (action, priority, priority_score, confidence, expected_objective, rules, reason); 422 InsufficientDataError when no active churn model)
GET /api/recommendations?customer_id=&min_priority=LOW (any role; returns items with display-name fallback; supports per-customer scoping)

## 3.10 Campaigns (live under /api/v1/campaigns; reads any role, writes + simulate require ADMIN/ANALYST)

Audience filter vocabulary (unknown keys are rejected at the edge *and* in the service - a silently dropped typo would widen reach to everyone):

| key | meaning |
| --- | --- |
| `segment_ids` | inclusive selector: members of the given active segments |
| `customer_ids` | inclusive selector: explicit customer ids |
| `rfm_segments` | narrow to RFM labels (champions … lost) |
| `risk_levels` | narrow to churn risk (LOW/MEDIUM/HIGH/CRITICAL) from the newest prediction |
| `channels` | narrow to acquisition channel (email/sms/push/in_app) |
| `exclude_customer_ids` | subtract customers from the audience |
| `min_clv` / `max_clv` | value bounds on the newest feature row |
| `min_frequency` | minimum order count |
| `min_recency_days` / `max_recency_days` | recency window (e.g. 90 = lapsed) |
| `variant_split` | treatment share, 0.1–0.9 (default 0.5) |

- Omitting every inclusive selector targets the whole tenant. Empty lists are rejected rather than treated as "no selector".
- A missing feature row fails a numeric lower bound: an unscored customer is not a match for "high value".

POST /api/v1/campaigns (body: {name, objective?, offer?, channel?, audience_filter?, assumptions?, start_date?, end_date?}; returns a DRAFT detail. `assumptions` = {response_rate, conversion_rate, revenue_per_conversion, cost_per_contact, control_discount_depth, treatment_discount_depth, confidence} - `treatment_discount_depth` must exceed the control, otherwise there is nothing to test. Audits CAMPAIGN_CREATE.)
GET /api/v1/campaigns?status=&channel=&limit= (list with member counts and the latest result per campaign)
GET /api/v1/campaigns/{id} (detail: filter, assumptions, member count, deterministic variant counts, result history)
PATCH /api/v1/campaigns/{id} (drafts only - once launched the audience is frozen)
POST /api/v1/campaigns/{id}/simulate (body: {assumptions?} as what-if overrides; resolves the audience, blends the response assumption with churn-risk priors, and returns a banded forecast - contacts, response rate + binomial range, conversions, revenue, cost, ROI, break-even response rate - plus an experiment power preview (per-arm sizes, projected lift, p-value, required sample size). An empty audience returns `forecast: null` rather than zeros. Audits CAMPAIGN_SIMULATE.)
POST /api/v1/campaigns/{id}/start (snapshots the audience into campaign_members, assigns A/B variants, sets status ACTIVE, target_count and cost_est; refuses an empty audience or a re-launch. Audits CAMPAIGN_START.)
POST /api/v1/campaigns/{id}/results (body: {response_rate?, conversion_rate?, revenue?, cost?, variant_metrics?: {A: {delivered, responses, conversions, revenue}, B: {...}}, complete?}; stores a measurement, computes ROI, optionally closes the campaign, and returns the fresh A/B analysis. Refused before launch. Audits CAMPAIGN_RESULTS.)
GET /api/v1/campaigns/{id}/members?limit=&offset= (paginated audience preview with the deterministic variant per customer)
GET /api/v1/campaigns/{id}/ab-analysis?confidence=0.95 (variant sizes plus a two-proportion z-test: per-arm rates with Wilson intervals, absolute/relative lift, p-value, confidence interval, required sample size, and a plain-language verdict. With no recorded metrics it reports sizes only and refuses to call a winner.)

A/B assignment is a stable hash of `(campaign_id, customer_id)`, so nobody can drift between arms across retries or a second worker - the variant is recomputed rather than trusted as stored state.

## 3.11 AI (live under /api/v1/ai)
POST /api/v1/ai/query (grounded AI Retention Advisor; body: {question, history?: [{question, answer}]}. Retrieval is intent-routed - the dashboard block is always included, plus matching domain views (churn board, RFM, CLV, coverage, trends, retention playbook, cohorts, journey, product affinity). Response: answer, retrieved_context.blocks[], context_labels, provider, disclaimer)
GET /api/v1/ai/context?question=... (which context blocks a question would retrieve - transparency endpoint)
POST /api/v1/ai/nl-to-sql (body: {question, generated_sql?}; returns executed SQL, columns, rows, row_count, truncated, source = template:<intent> | llm:<provider> | manual). Guard chain before execution: SELECT-only, no comments/stacked statements/forbidden keywords, table allow-list (customers, products, transactions, customer_features, segments, customer_segments, predictions, recommendations), tenant scope (organization_id injected for single-table queries; joins must scope every table explicitly), subquery rejection, and a row cap (max 200 rows, hard ceiling 1000). Rejections are 422. Audits AI_QUERY / AI_NL_TO_SQL.

## 3.12 Monitoring & Health (live under /api/v1/monitoring; reads any role, recording a scan requires ADMIN/ANALYST)
GET /api/v1/health (app/db/redis)
GET /api/v1/ready
GET /api/v1/monitoring/drift?model_version_id=... (live feature + prediction drift for one model version; defaults to the org's active version, then to its newest. Writes nothing. Response: model, model_version, baseline{source, dataset_version_id, note}, summary{drift_status, psi_overall, n_features_profiled, n_features_drifted, drifted_features}, features[]{feature, status, psi, ks, reference, current, mean_delta, missing_rate_delta, reason}, prediction{status, psi, baseline_run_id, baseline_source, summary}, health{days_since_trained, stale, n_population, n_predictions, prediction_coverage}, thresholds, min_samples)
GET /api/v1/monitoring/models (per-model roll-up: active version, n_predictions, staleness, prediction coverage, and the latest recorded verdict)
GET /api/v1/monitoring/runs?model_version_id=&limit=&offset= (recorded scan history, newest first)
GET /api/v1/monitoring/runs/{id} (one run with full per-feature detail)
POST /api/v1/monitoring/runs?model_version_id=... (run a scan and persist a MonitoringRun so drift can be trended). Audits MONITORING_SCAN.

Drift semantics
- Feature drift compares the latest feature rows against the feature rows of ModelVersion.train_dataset_version_id - the real train-time baseline. Population is buyers only (frequency > 0), matching what the trainers score.
- PSI is binned on reference quantiles with +/-inf sentinels; KS is reported alongside as a binning-free cross-check. Thresholds are configurable (drift_psi_thresholds: MODERATE 0.10, SIGNIFICANT 0.25).
- Prediction drift has no train-time baseline because score_customers replaces prediction rows in place, so the earliest recorded run for a version supplies a compact baseline histogram.
- The bin count adapts to sample size (monitoring_min_per_bin) because fixed 10-bin PSI is biased upwards on small samples.
- Nothing is fabricated: a missing baseline, a constant reference, or a sample under monitoring_min_samples is reported as NO_DATA/UNKNOWN with a reason, never as a clean bill of health. Re-processing a training dataset version overwrites its feature rows, so a retrain there silently moves the baseline.

## 3.13 Exports (live under /api/v1/reports)
GET /api/v1/reports/catalog (available reports, their allowed filters, output formats, row cap, and pdf_status). Any role may read.
POST /api/v1/reports/export (body: {report, format = csv|xlsx|json, filters?}; ADMIN/ANALYST only. Streams a file attachment and sets X-Report-Rows, X-Report-Truncated, X-Report-Max-Rows. Audits REPORT_EXPORT with the filters used.)

Reports: customers (lifetime revenue, RFM, CLV), customer_directory (raw profile fields), at_risk (active churn model scoring), features (latest engineered row per customer), predictions (optionally one model_version_id), campaigns (with latest measured results), campaign_results (full history). Filters are validated per report and unknown keys are rejected with 422; enum values are checked server-side. Every builder filters on organization_id, so an export cannot widen tenant visibility. Rows are capped at report_max_rows and the response states whether it was truncated.
PDF is not available: the roadmap tiers it as a later-phase item and the catalogue reports pdf_status = not_available rather than advertising a format the server would reject.

## 3.14 Notes
- All org-scoped. RBAC enforced. Read-only creds for NL-to-SQL. Async heavy ops. Pydantic v2.
