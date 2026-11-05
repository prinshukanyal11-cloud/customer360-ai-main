# 2. Database ER Diagram Description

## 2.1 Core Entities

1. organizations (id PK, name, slug, status, created_at, updated_at)
2. users (id PK, org_id FK→organizations, email, password_hash, role [ADMIN/ANALYST/VIEWER], status, last_login_at, created_at, updated_at)
3. datasets (id PK, org_id FK, name, description, source_type, status, total_rows, columns_json, created_by FK→users)
4. dataset_versions (id PK, dataset_id FK, version_no, file_path, checksum, row_count, column_mapping_json, dq_score, dq_report_json, processed_at, created_by)
5. customers (id PK, org_id FK, external_customer_id, name, email, phone, location, age, gender, signup_date, acquisition_channel, created_at, updated_at) — indexed by (org_id, external_customer_id)
6. transactions (id PK, org_id FK, customer_id FK→customers, order_id, order_date, product_id FK, quantity, amount, discount, currency, channel, created_at, updated_at)
7. products (id PK, org_id FK, product_id_ext, product_name, category, subcategory, unit_price, created_at, updated_at)
8. customer_features (id PK, org_id FK, customer_id FK, dataset_version_id FK, recency, frequency, monetary, aov, avg_time_btwn, tenure_days, category_diversity, discount_usage, rfm_score, rfm_segment, clv_est, created_at, updated_at)
9. segments (id PK, org_id FK, type [RFM/ML], name, description, rules_json, color, active, created_at)
10. customer_segments (id PK, org_id FK, customer_id FK, segment_id FK, assigned_at, confidence)
11. models (id PK, org_id FK, name [churn/segmentation/clv/next_purchase], family, status, description, created_by)
12. model_versions (id PK, org_id FK, model_id FK, version_no, artifact_path, preprocessing_path, features_list, params_json, metrics_json, train_dataset_version_id, validation_split, test_split, random_seed, training_duration_s, trained_at, active, created_by)
13. predictions (id PK, org_id FK, model_version_id FK, customer_id FK, prediction_label, prediction_proba, risk_level [LOW/MEDIUM/HIGH/CRITICAL], created_at, inferred_at)
14. explanations (id PK, org_id FK, prediction_id FK, method [SHAP], global_importance_json, local_contrib_json, base_value, created_at)
15. recommendations (id PK, org_id FK, customer_id FK, type [RETENTION/PRODUCT], action, reason, priority, confidence, expected_objective, rules_triggered_json, model_version_id FK nullable, llm_context_json, created_at)
16. campaigns (id PK, org_id FK, name, objective, offer, channel, audience_filter_json, start_date, end_date, status [DRAFT/ACTIVE/PAUSED/COMPLETED], target_count, cost_est, assumptions_json, created_by, created_at, updated_at)
17. campaign_members (id PK, org_id FK, campaign_id FK, customer_id FK, status [ELIGIBLE/CONTACTED/RESPONDED/CONVERTED/EXCLUDED], assigned_at)
18. campaign_results (id PK, org_id FK, campaign_id FK, response_rate, conversion_rate, revenue, cost, roi, metrics_json, calculated_at)
19. audit_logs (id PK, org_id FK, user_id FK, action, entity_type, entity_id, ip_address, user_agent, metadata_json, created_at)

## 2.2 Relationships
- org-scoped all tables (org_id). FK with ON DELETE RESTRICT/SET NULL as appropriate.
- customers 1:N transactions; customers N:M segments via customer_segments.
- datasets 1:N dataset_versions; features tied to dataset_version.
- models 1:N model_versions; model_versions 1:N predictions; predictions 1:N explanations.
- campaigns 1:N members/results.

## 2.3 Design Notes
- Multi-tenancy: every query must be filtered by org_id.
- Soft/enum constraints; timestamps on all mutable entities.
- Indexes: (org_id), (org_id, customer_id), (org_id, order_date), (org_id, external_customer_id), (org_id, created_at).
