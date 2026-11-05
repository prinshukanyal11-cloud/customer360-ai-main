# 1. Customer360 AI - System Architecture

## 1.1 Overview
Customer360 AI is a modular, multi-tenant-capable, production-ready platform for customer segmentation, churn prediction, explainability, CLV, retention recommendations, campaigns, and NL-to-SQL analytics. The architecture follows separation of concerns, domain-driven modular design, and a decision-support pipeline.

## 1.2 High-Level Architecture

Client (Browser)
  ↓
Frontend (React + Vite + TS + Tailwind + shadcn/ui)
  ↓
API Gateway (FastAPI, REST)
  ↓
Service Layer (Business Logic)
  ↓
Repository Layer (Data Access)
  ↓
PostgreSQL (Transactional)
  ↓ (async jobs)
Celery + Redis (Background Jobs, Training, Processing)

External/ML: scikit-learn, XGBoost, SHAP, K-Means/PCA
LLM: Provider abstraction (OpenAI/Gemini) with validation
Storage: Files/Datasets (local or object storage in prod)

## 1.3 Design Principles
- Modular monolith-first, cleanly separable to microservices if needed.
- Do not put business logic in UI; no DB queries in route handlers.
- Multi-tenancy by organization (logical isolation).
- Reproducibility (dataset/model/feature versions, seeds).
- Explain-first AI: distinguish Facts / Predictions / Explanations / Recommendations / Simulations.
- Production: security, observability, testability, containerized.
