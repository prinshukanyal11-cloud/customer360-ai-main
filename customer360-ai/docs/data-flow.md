# 7. Data Flow

## 7.1 Ingestion
Upload → validate type/size → inspect columns/dtypes/missing → preview → auto mapping suggestion → user maps columns (canonical schema: customers/products/transactions) → validate (DQ engine: completeness, duplicates, format, logic; blocked if ERRORs, 5% required-null tolerance) → process (clean + idempotent upsert on natural keys, auto-provision referenced customers/products) → dataset version + DQ report + audit. Without a broker the core runs inline; with Celery+Redis the same code runs in the worker.

## 7.2 Features
Transactions → RFM → Customer 360 features → customer_features + segments.

## 7.3 ML
dataset_version → preprocessing pipeline → train/val/test → model pipeline → metrics → register → activate → predictions → SHAP explanations.

## 7.4 Decision Support
Features + predictions + CLV → prioritization score → retention recommendations → campaign builder → simulate → measure.

## 7.5 AI
User query → tool/retriever pulls actual data from services (no hallucination) → AI Analyst answer with datum/query reference. NL-to-SQL → validate SELECT-only → org filter → row limit → read-only creds → result table.

## 7.6 Render/Export
All charts from APIs; reports generated from live data (csv/xlsx/pdf).
