"""End-to-end smoke test for the Phase 2 dataset pipeline.

Runs the real app (no dependency overrides) against a throwaway SQLite
database: register -> upload CSV -> mapping suggestion -> map -> validate
(blocking issues) -> process -> quality. Exits non-zero on failure.

Set SMOKE_BASE to point at an already-running server to skip launching one.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8092")

CUSTOMERS_CSV = b"""customer_id,name,email,city,age,signup_date,acquisition_channel
C1,Ann,ann@x.com,Delhi,34,2024-01-05,web
C2,Ben,ben@x.com,Mumbai,notanumber,2024-02-10,app
C3,Cal,,Pune,,,web
"""

TRANSACTIONS_CSV = b"""order_id,customer_id,order_date,product_name,category,quantity,amount,discount,channel
O1,C1,2024-03-01,Widget,Tools,1,300.00,0,web
O2,C1,2024-05-01,Widget,Tools,1,300.00,0,web
O3,C2,2024-04-01,Gadget,Tools,1,200.00,10,app
O4,C3,2024-06-01,Gadget,Tools,1,250.00,0,web
O5,C4,2024-04-15,Gadget,Tools,1,210.00,0,web
O6,C4,2024-06-15,Gadget,Tools,1,210.00,5,app
O7,C5,2024-03-10,Widget,Tools,1,280.00,0,web
O8,C5,2024-05-10,Widget,Tools,1,280.00,0,web
O9,C5,2024-07-10,Widget,Tools,1,280.00,0,web
O10,C6,2024-04-20,Gadget,Tools,1,190.00,0,app
O11,C6,2024-06-20,Widget,Tools,1,260.00,0,app
O12,C7,2024-03-25,Gadget,Tools,1,205.00,0,web
O13,C7,2024-05-25,Gadget,Tools,1,205.00,0,web
O14,C8,2024-05-01,Gadget,Tools,1,215.00,0,web
O15,C8,2024-07-01,Gadget,Tools,1,215.00,0,web
O16,C9,2024-06-12,Widget,Tools,1,320.00,0,web
O17,C9,2024-08-12,Widget,Tools,1,325.00,5,web
O18,C10,2024-01-20,Gadget,Tools,1,180.00,0,app
O19,C11,2024-02-10,Widget,Tools,1,240.00,0,web
O20,C11,2024-04-10,Widget,Tools,1,245.00,0,web
O21,C12,2024-01-30,Gadget,Tools,1,160.00,5,app
O22,C12,2024-03-30,Gadget,Tools,1,165.00,0,app
O23,C13,2024-02-20,Widget,Tools,1,230.00,0,web
O24,C13,2024-06-20,Widget,Tools,1,235.00,0,web
O25,C14,2024-01-05,Gadget,Tools,1,175.00,0,app
O26,C14,2024-05-05,Gadget,Tools,1,178.00,5,app
O27,C15,2024-07-20,Widget,Tools,1,310.00,0,web
O28,C16,2024-02-25,Widget,Tools,1,270.00,0,web
O29,C16,2024-07-25,Widget,Tools,1,275.00,0,web
"""


def report(status: str, label: str, ok: bool, logs=None) -> None:
    print(f"  [{('ok' if ok else 'FAIL')}] {status} - {label}")
    if not ok:
        if logs is not None:
            print(logs())
        sys.exit(1)


def close_to(value, expected: float, tolerance: float = 1e-6) -> bool:
    """Float comparison for the smoke checks (no pytest out here)."""
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError):
        return False


def wait_healthy(timeout: float = 60.0) -> None:
    client = httpx.Client(base_url=BASE, timeout=5)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = client.get("/api/v1/health")
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(1)
    raise SystemExit("server did not become healthy")


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DEMO_MODE": "false", "ENABLE_SAMPLE_DATA": "false"}
    # Every check below comes from 127.0.0.1, so the per-IP limiter would start
    # rejecting the run partway through as if it were abuse. The limiter has its
    # own tests; this script verifies flows, not throttling.
    env["RATE_LIMIT_ENABLED"] = "false"
    server = None
    tmpdir = tempfile.mkdtemp(prefix="smoke_")
    log_handle = None
    try:
        if not os.environ.get("SMOKE_BASE"):
            dbfile = str(Path(tmpdir) / "smoke.db")
            logfile = Path(tmpdir) / "server.log"
            env["DATABASE_URL"] = f"sqlite:///{dbfile}"
            # Alembic migrations target Postgres (SERIAL, timestamptz), so for
            # this throwaway SQLite database we create the schema directly.
            init_code = (
                "import os; from sqlalchemy import create_engine; "
                "from app.db.base import Base; import app.models; "
                "Base.metadata.create_all(create_engine(os.environ['DATABASE_URL']))"
            )
            subprocess.run(
                [sys.executable, "-c", init_code],
                cwd=str(ROOT),
                env=env,
                check=True,
                capture_output=True,
            )
            log_handle = open(logfile, "w", encoding="utf-8")  # noqa: SIM115 (lives for the server's lifetime)
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8092", "--log-level", "warning"],
                cwd=str(ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            wait_healthy()

            def server_logs() -> str:
                return "\n".join(logfile.read_text(encoding="utf-8").splitlines()[-40:])

        else:

            def server_logs() -> str:
                return ""
        try:
            client = httpx.Client(base_url=BASE, timeout=30)

            r = client.get("/api/v1/health")
            report("health", f"{r.status_code}", r.status_code == 200)

            r = client.post(
                "/api/v1/auth/register",
                json={"email": "smoke@x.com", "password": "password123", "organization_name": "Smoke Org"},
            )
            report("register", f"{r.status_code}", r.status_code == 201)
            token = r.json()["access_token"]
            refresh_token = r.json()["refresh_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Auth lifecycle against a real server rather than test fixtures.
            # Each control gets its own account on purpose: reuse detection kills
            # the whole token family, so exercising it on the same account would
            # make the logout check pass for the wrong reason.
            r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
            rotated_ok = r.status_code == 200 and r.json()["refresh_token"] != refresh_token
            rotated_token = r.json()["refresh_token"] if r.status_code == 200 else ""
            report("refresh rotates the token", f"{r.status_code}", rotated_ok)

            r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
            report("replayed refresh token is refused", f"{r.status_code}", r.status_code == 401)

            r = client.post("/api/v1/auth/refresh", json={"refresh_token": rotated_token})
            report(
                "reuse detection revokes the successor",
                f"{r.status_code}",
                r.status_code == 401,
            )

            r = client.post(
                "/api/v1/auth/register",
                json={"email": "logout@x.com", "password": "password123", "organization_name": "Smoke Org"},
            )
            report("second account", f"{r.status_code}", r.status_code == 201)
            logout_access = r.json()["access_token"]
            logout_refresh = r.json()["refresh_token"]

            r = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {logout_access}"})
            report("logout", f"{r.status_code}", r.status_code == 204)

            r = client.post("/api/v1/auth/refresh", json={"refresh_token": logout_refresh})
            report(
                "refresh after logout is refused",
                f"{r.status_code}",
                r.status_code == 401,
            )

            r = client.post(
                "/api/v1/datasets/upload",
                headers=headers,
                files={"file": ("customers.csv", io.BytesIO(CUSTOMERS_CSV), "text/csv")},
            )
            report("upload", f"{r.status_code}", r.status_code == 201)
            body = r.json()
            ds_id = body["dataset_id"]
            assert body["mapping_suggestion"]["customers"]["external_customer_id"] == "customer_id"

            r = client.get("/api/v1/datasets", headers=headers)
            report("list", f"total={r.json()['total']}", r.status_code == 200 and r.json()["total"] >= 1)

            r = client.get(f"/api/v1/datasets/{ds_id}/mapping-suggestion", headers=headers)
            report("mapping-suggestion", f"{r.status_code}", r.status_code == 200)
            suggestion = r.json()["entities"]

            r = client.post(f"/api/v1/datasets/{ds_id}/map-columns", headers=headers, json={"mapping": suggestion})
            report("map-columns", f"{r.status_code}", r.status_code == 200)

            r = client.post(f"/api/v1/datasets/{ds_id}/validate", headers=headers)
            report("validate", f"score={r.json().get('score')}", r.status_code == 200)
            report("validate-blocked", "clean-ish (warn allowed)", not r.json()["blocked"])

            r = client.post(f"/api/v1/datasets/{ds_id}/process", headers=headers)
            ok = r.status_code == 200 and r.json()["status"] == "PROCESSED"
            report("process", f"{r.status_code}", ok, logs=server_logs)

            r = client.get(f"/api/v1/datasets/{ds_id}/quality", headers=headers)
            q_ok = r.status_code == 200 and r.json()["status"] == "PROCESSED"
            report("quality", f"score={r.json().get('score')}", q_ok)

            r = client.get("/api/v1/customers", headers=headers)
            if r.status_code == 200 and r.json().get("total", 0) >= 2:
                report("customers-visible", f"total={r.json().get('total')}", True)
            else:
                report("customers-visible", f"http={r.status_code}", False)

            r = client.post(
                "/api/v1/datasets/upload",
                headers=headers,
                files={"file": ("transactions.csv", io.BytesIO(TRANSACTIONS_CSV), "text/csv")},
            )
            report("upload-txns", f"{r.status_code}", r.status_code == 201)
            txn_id = r.json()["dataset_id"]
            suggestion = r.json()["mapping_suggestion"]

            r = client.post(f"/api/v1/datasets/{txn_id}/map-columns", headers=headers, json={"mapping": suggestion})
            report("map-txns", f"{r.status_code}", r.status_code == 200)

            r = client.post(f"/api/v1/datasets/{txn_id}/process", headers=headers)
            fs = r.json().get("feature_summary") or {}
            report(
                "process-txns",
                f"buyers={fs.get('buyers')}",
                r.status_code == 200 and r.json()["status"] == "PROCESSED" and (fs.get("buyers") or 0) >= 8,
                logs=server_logs,
            )

            r = client.post("/api/v1/segments/train", headers=headers, json={"n_clusters": 3, "use_pca": True})
            trained = r.json() if r.status_code == 200 else {}
            report(
                "segments-train",
                f"k={trained.get('n_clusters')} n={trained.get('n_samples')}",
                r.status_code == 200 and trained.get("status") == "TRAINED" and len(trained.get("segments", [])) == 3,
                logs=server_logs,
            )

            r = client.get("/api/v1/segments", headers=headers, params={"segment_type": "ML"})
            ml = r.json().get("items", []) if r.status_code == 200 else []
            report(
                "segments-list",
                f"ml_segments={len(ml)}",
                r.status_code == 200 and len(ml) == 3 and all(s["member_count"] > 0 for s in ml),
            )

            cluster = ml[0] if ml else {}
            r = client.get(f"/api/v1/segments/{cluster.get('id')}/clusters", headers=headers)
            detail = r.json() if r.status_code == 200 else {}
            report(
                "segments-clusters",
                f"members={detail.get('member_count')} stats={len(detail.get('stats') or [])}",
                r.status_code == 200 and detail.get("member_count", 0) > 0,
            )

            r = client.post(
                "/api/v1/models/churn/train",
                headers=headers,
                json={"families": ["logistic", "random_forest"], "random_seed": 42},
            )
            trained = r.json() if r.status_code == 200 else {}
            report(
                "churn-train",
                f"families={len(trained.get('families') or [])} n={trained.get('n_samples')}",
                r.status_code == 200
                and trained.get("status") == "TRAINED"
                and len(trained.get("families") or []) == 2
                and (trained.get("n_samples") or 0) >= 16,
                logs=server_logs,
            )

            r = client.get("/api/v1/models", headers=headers)
            models = r.json().get("items", []) if r.status_code == 200 else []
            churn_model = next((m for m in models if m.get("model_type") == "churn"), None)
            report(
                "models-list",
                f"churn_versions={len(churn_model.get('versions') or []) if churn_model else 0}",
                churn_model is not None and sum(1 for v in churn_model["versions"] if v["active"]) == 1,
            )

            r = client.post("/api/v1/predictions/churn", headers=headers, json={})
            scored = r.json() if r.status_code == 200 else {}
            report(
                "churn-score",
                f"n_scored={scored.get('n_scored')}",
                r.status_code == 200 and (scored.get("n_scored") or 0) >= 16,
                logs=server_logs,
            )

            r = client.get("/api/v1/predictions/at-risk", headers=headers, params={"min_risk_level": "HIGH"})
            at_risk = r.json() if r.status_code == 200 else {}
            report(
                "at-risk",
                f"total={at_risk.get('total')} listed={len(at_risk.get('items') or [])}",
                r.status_code == 200
                and at_risk.get("total") == scored.get("n_scored")
                and len(at_risk.get("items") or []) > 0
                and all(item.get("name") and item.get("risk_level") for item in at_risk.get("items", [])),
            )
            report(
                "at-risk-categories",
                f"summary_var={bool(at_risk.get('summary'))}",
                r.status_code == 200
                and at_risk.get("summary") is not None
                and all(
                    item.get("risk_category") in ("PRIORITY", "WIN_BACK")
                    for item in at_risk.get("items", [])
                ),
            )

            r = client.post("/api/v1/predictions/churn/explain", headers=headers, json={})
            explained = r.json() if r.status_code == 200 else {}
            report(
                "churn-explain",
                f"n_explained={explained.get('n_explained')} gi={len(explained.get('global_importance') or [])}",
                r.status_code == 200
                and explained.get("n_explained") == scored.get("n_scored")
                and len(explained.get("global_importance") or []) > 0
                and all(item.get("contributions") for item in explained.get("items", [])),
                logs=server_logs,
            )

            first = (explained.get("items") or [{}])[0]
            if first.get("customer_id"):
                r = client.get(
                    f"/api/v1/customers/{first['customer_id']}/explanation",
                    headers=headers,
                )
                expl = r.json() if r.status_code == 200 else {}
                report(
                    "customer-explanation",
                    f"pid={expl.get('prediction_id')} contribs={len(expl.get('contributions') or [])}",
                    r.status_code == 200
                    and expl.get("prediction_id") == first.get("prediction_id")
                    and (expl.get("contributions") or []) != [],
                )

            r = client.post("/api/v1/recommendations/generate", headers=headers, json={})
            recs = r.json() if r.status_code == 200 else {}
            report(
                "recommendations-generate",
                f"n={recs.get('n_generated')}",
                r.status_code == 200
                and (recs.get("n_generated") or 0) > 0
                and all(
                    item.get("action") and item.get("priority") and item.get("reason")
                    for item in recs.get("items", [])
                ),
                logs=server_logs,
            )

            r = client.get("/api/v1/recommendations", headers=headers)
            listed = r.json() if r.status_code == 200 else {}
            report(
                "recommendations-list",
                f"total={listed.get('total')}",
                r.status_code == 200 and listed.get("total") == recs.get("n_generated"),
            )

            if scored.get("items"):
                customer_id = scored["items"][0]["customer_id"]
                r = client.post(
                    f"/api/v1/customers/{customer_id}/what-if",
                    headers=headers,
                    json={"scenario": "re_engage"},
                )
                whatif = r.json() if r.status_code == 200 else {}
                base = whatif.get("baseline", {}).get("probability")
                delta = whatif.get("delta", {}).get("probability")
                report(
                    "what-if",
                    f"baseline={base} delta={delta}",
                    r.status_code == 200
                    and base is not None
                    and whatif.get("scenario_output", {}).get("probability") is not None
                    and delta is not None,
                    logs=server_logs,
                )

            r = client.get("/api/v1/analytics/cohorts", headers=headers)
            cohorts = r.json() if r.status_code == 200 else {}
            report(
                "analytics-cohorts",
                f"cohorts={len(cohorts.get('cohorts') or [])} "
                f"customers={cohorts.get('summary', {}).get('total_customers')} "
                f"repeat_share={cohorts.get('summary', {}).get('repeat_share')}",
                r.status_code == 200
                and (cohorts.get("summary", {}).get("total_customers") or 0) > 0
                and (cohorts.get("cohorts") or []) != []
                and all(
                    row.get("retention") and row.get("retention")[0] == 1.0
                    for row in cohorts.get("cohorts", [])
                ),
                logs=server_logs,
            )

            r = client.get("/api/v1/analytics/journey", headers=headers)
            journey = r.json() if r.status_code == 200 else {}
            staged = sum(stage.get("customers", 0) for stage in journey.get("stages", []))
            report(
                "analytics-journey",
                f"staged={staged} avg_orders={journey.get('avg_orders_per_customer')} "
                f"milestones={len(journey.get('milestones') or [])}",
                r.status_code == 200
                and staged == cohorts.get("summary", {}).get("total_customers")
                and (journey.get("milestones") or []) != []
                and (journey.get("as_of") or "") != "",
                logs=server_logs,
            )

            r = client.get("/api/v1/analytics/products", headers=headers)
            products = r.json() if r.status_code == 200 else {}
            report(
                "analytics-products-global",
                f"baskets={products.get('total_baskets')} pairs={len(products.get('recommendations') or [])}",
                r.status_code == 200
                and (products.get("total_baskets") or 0) > 0
                and (products.get("recommendations") or []) != []
                and all(
                    pair.get("product_a") and pair.get("product_b")
                    for pair in products.get("recommendations", [])
                ),
                logs=server_logs,
            )

            top_pair = (products.get("recommendations") or [{}])[0]
            drill_id = top_pair.get("product_a", {}).get("id")
            r = client.get(
                "/api/v1/analytics/products",
                headers=headers,
                params={"product_id": drill_id, "limit": 5},
            )
            drilled = r.json() if r.status_code == 200 else {}
            report(
                "analytics-products-item-to-item",
                f"product_id={drill_id} n={len(drilled.get('recommendations') or [])}",
                r.status_code == 200
                and (drill_id or 0) > 0
                and (drilled.get("recommendations") or []) != []
                and all(
                    rec.get("product_id")
                    and rec.get("co_purchases", 0) > 0
                    and rec.get("score", 0) > 0
                    for rec in drilled.get("recommendations", [])
                ),
                logs=server_logs,
            )

            r = client.post(
                "/api/v1/ai/query",
                headers=headers,
                json={
                    "question": "How much revenue is at risk right now?",
                    "history": [{"question": "How is revenue trending?", "answer": "Revenue is up."}],
                },
            )
            analyst = r.json() if r.status_code == 200 else {}
            r2 = client.post(
                "/api/v1/ai/query",
                headers=headers,
                json={"question": "and what about the risky ones?", "history": []},
            )
            followup = r2.json() if r2.status_code == 200 else {}
            report(
                "ai-query-grounded",
                f"provider={analyst.get('provider')} contexts={len(analyst.get('context_labels') or [])} "
                f"followup_ok={bool(followup.get('answer'))}",
                r.status_code == 200
                and r2.status_code == 200
                and (analyst.get("answer") or "") != ""
                and (analyst.get("retrieved_context") or {}).get("blocks") != []
                and (analyst.get("context_labels") or []) != []
                and (followup.get("answer") or "") != "",
                logs=server_logs,
            )

            r = client.post(
                "/api/v1/ai/nl-to-sql",
                headers=headers,
                json={"question": "How many customers do we have?"},
            )
            nlq = r.json() if r.status_code == 200 else {}
            n_customers = (nlq.get("rows") or [{}])[0].get("customers")
            report(
                "ai-nl-to-sql-template",
                f"source={nlq.get('source')} customers={n_customers} rows={nlq.get('row_count')}",
                r.status_code == 200
                and (nlq.get("executed") is True)
                and (nlq.get("source") or "").startswith("template:")
                and isinstance(n_customers, int)
                and n_customers == fs.get("total_customers", n_customers)
                and "LIMIT" in (nlq.get("sql") or "")
                and "organization_id" in (nlq.get("sql") or ""),
                logs=server_logs,
            )

            r = client.post(
                "/api/v1/ai/nl-to-sql",
                headers=headers,
                json={
                    "question": "raw customers",
                    "generated_sql": "SELECT external_customer_id FROM customers ORDER BY external_customer_id",
                },
            )
            manual = r.json() if r.status_code == 200 else {}
            manual_rows = manual.get("rows") or []
            report(
                "ai-nl-to-sql-manual-scoped",
                f"source={manual.get('source')} rows={len(manual_rows)}",
                r.status_code == 200
                and manual.get("source") == "manual"
                and "organization_id" in (manual.get("sql") or "")
                and len(manual_rows) == (fs.get("total_customers") or len(manual_rows))
                and manual.get("row_count") == len(manual_rows),
                logs=server_logs,
            )

            blocked = 0
            for unsafe in (
                "DELETE FROM customers",
                "SELECT * FROM customers; DROP TABLE customers",
                "SELECT * FROM users",
                "SELECT c.name FROM customers c JOIN transactions t ON t.customer_id = c.id",
            ):
                rr = client.post(
                    "/api/v1/ai/nl-to-sql",
                    headers=headers,
                    json={"question": "unsafe", "generated_sql": unsafe},
                )
                if rr.status_code == 422:
                    blocked += 1
            report(
                "ai-nl-to-sql-blocks-unsafe",
                f"blocked={blocked}/4",
                blocked == 4,
                logs=server_logs,
            )

            r = client.post(
                "/api/v1/campaigns",
                headers=headers,
                json={
                    "name": "Smoke win-back",
                    "objective": "Bring lapsed buyers back",
                    "offer": "20% off",
                    "channel": "email",
                    "audience_filter": {"max_recency_days": 400, "variant_split": 0.5},
                    "assumptions": {"response_rate": 0.2, "cost_per_contact": 0.5},
                },
            )
            created = r.json() if r.status_code == 201 else {}
            campaign_id = created.get("id")
            report(
                "campaign-create-draft",
                f"id={campaign_id} status={created.get('status')}",
                r.status_code == 201
                and campaign_id is not None
                and created.get("status") == "DRAFT"
                and created.get("member_count") == 0,
                logs=server_logs,
            )

            r = client.post(
                f"/api/v1/campaigns/{campaign_id}/simulate",
                headers=headers,
                json={"assumptions": {"cost_per_contact": 0.5}},
            )
            simulation = r.json() if r.status_code == 200 else {}
            forecast = simulation.get("forecast") or {}
            audience = simulation.get("audience") or {}
            response_range = forecast.get("response_range") or [0, 0]
            report(
                "campaign-simulate-forecast",
                f"audience={audience.get('size')} contacts={forecast.get('contacts')} "
                f"roi={forecast.get('roi')} p={((simulation.get('experiment') or {}).get('p_value'))}",
                r.status_code == 200
                and isinstance(forecast.get("contacts"), int)
                and forecast.get("contacts", 0) > 0
                and forecast.get("contacts") == audience.get("size")
                and response_range[0] <= forecast.get("expected_responses", -1) <= response_range[1]
                and forecast.get("estimated_cost") is not None
                and (simulation.get("experiment") or {}).get("split") is not None,
                logs=server_logs,
            )

            r = client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=headers)
            started = r.json() if r.status_code == 200 else {}
            variant_counts = started.get("variant_counts") or {}
            report(
                "campaign-start-snapshots-audience",
                f"status={started.get('status')} members={started.get('member_count')} "
                f"A={variant_counts.get('A')} B={variant_counts.get('B')}",
                r.status_code == 200
                and started.get("status") == "ACTIVE"
                and started.get("member_count") == audience.get("size")
                and sum(variant_counts.values() or [0, 0]) == audience.get("size"),
                logs=server_logs,
            )

            r = client.get(f"/api/v1/campaigns/{campaign_id}/members", headers=headers, params={"limit": 5})
            members = r.json() if r.status_code == 200 else {}
            member_items = members.get("items") or []
            report(
                "campaign-members-paginated",
                f"total={members.get('total')} page={len(member_items)}",
                r.status_code == 200
                and members.get("total") == audience.get("size")
                and len(member_items) <= 5
                and all(item.get("variant") in ("A", "B") for item in member_items),
                logs=server_logs,
            )

            r = client.post(
                f"/api/v1/campaigns/{campaign_id}/results",
                headers=headers,
                json={
                    "revenue": 2400.0,
                    "cost": 800.0,
                    "variant_metrics": {
                        "A": {"delivered": 400, "responses": 40, "revenue": 900.0},
                        "B": {"delivered": 400, "responses": 76, "revenue": 1500.0},
                    },
                    "complete": True,
                },
            )
            recorded = r.json() if r.status_code == 200 else {}
            report(
                "campaign-results-recorded",
                f"status={recorded.get('status')} roi={recorded.get('roi')}",
                r.status_code == 200
                and recorded.get("status") == "COMPLETED"
                and close_to(recorded.get("roi"), 2.0),
                logs=server_logs,
            )

            r = client.get(f"/api/v1/campaigns/{campaign_id}/ab-analysis", headers=headers)
            analysis = r.json() if r.status_code == 200 else {}
            test = analysis.get("test") or {}
            report(
                "campaign-ab-verdict",
                f"significant={test.get('significant')} lift={test.get('lift_relative')} "
                f"p={test.get('p_value')} revenue_lift={analysis.get('revenue_lift')}",
                r.status_code == 200
                and analysis.get("metric_compared") == "responses"
                and test.get("significant") is True
                and isinstance(test.get("lift_relative"), (int, float))
                and analysis.get("revenue_lift") is not None,
                logs=server_logs,
            )

            r = client.get("/api/v1/campaigns", headers=headers)
            listing = r.json() if r.status_code == 200 else {}
            report(
                "campaign-list-summary",
                f"total={listing.get('total')} draft={((listing.get('by_status') or {}).get('DRAFT'))}",
                r.status_code == 200
                and listing.get("total", 0) >= 1
                and all(item.get("latest_result") is not None for item in listing.get("items") or []),
                logs=server_logs,
            )

            rejected = 0
            for bad in (
                {"name": "typo", "audience_filter": {"min_ltv": 10}},
                {"name": "empty selector", "audience_filter": {"customer_ids": []}},
                {"name": "no test", "assumptions": {"control_discount_depth": 0.3, "treatment_discount_depth": 0.1}},
                {"name": "bad channel", "channel": "smoke_signal"},
            ):
                rr = client.post("/api/v1/campaigns", headers=headers, json=bad)
                if rr.status_code == 422:
                    rejected += 1
            report(
                "campaign-rejects-bad-input",
                f"rejected={rejected}/4",
                rejected == 4,
                logs=server_logs,
            )

            # ------------------------------------------------ Phase 11: monitoring
            r = client.get("/api/v1/monitoring/drift", headers=headers)
            drift = r.json() if r.status_code == 200 else {}
            features = drift.get("features") or []
            report(
                "monitoring-drift",
                f"status={drift.get('summary', {}).get('drift_status')} features={len(features)}",
                r.status_code == 200
                and len(features) == 10
                and drift.get("summary", {}).get("n_features_profiled") == 10
                and drift.get("baseline", {}).get("source") == "training_dataset_version"
                and all("psi" in f and "status" in f for f in features),
                logs=server_logs,
            )

            r = client.post("/api/v1/monitoring/runs", headers=headers)
            first_run = r.json() if r.status_code == 201 else {}
            report(
                "monitoring-record-run",
                f"id={first_run.get('id')} status={first_run.get('status')}",
                r.status_code == 201
                and first_run.get("status") == "COMPLETED"
                and first_run.get("n_current_rows", 0) > 0
                and first_run.get("baseline_run_id") is None,
                logs=server_logs,
            )

            # The second scan must link to the first as its prediction baseline.
            # This dataset scores fewer rows than `monitoring_min_samples`, so the
            # verdict is deliberately withheld rather than reported from thin data.
            r = client.post("/api/v1/monitoring/runs", headers=headers)
            second_run = r.json() if r.status_code == 201 else {}
            report(
                "monitoring-prediction-baseline",
                f"baseline_run={second_run.get('baseline_run_id')} status={second_run.get('prediction_drift_status')}",
                r.status_code == 201
                and second_run.get("baseline_run_id") == first_run.get("id")
                and second_run.get("prediction_drift_status") in ("UNKNOWN", "STABLE", "MODERATE", "SIGNIFICANT"),
                logs=server_logs,
            )

            r = client.get("/api/v1/monitoring/runs", headers=headers)
            run_list = r.json() if r.status_code == 200 else {}
            report(
                "monitoring-run-history",
                f"total={run_list.get('total')}",
                r.status_code == 200 and run_list.get("total", 0) >= 2,
            )

            r = client.get("/api/v1/monitoring/models", headers=headers)
            health = r.json() if r.status_code == 200 else {}
            health_items = health.get("items") or []
            report(
                "monitoring-model-health",
                f"versions={health.get('total')} scanned={sum(1 for i in health_items if i.get('last_run'))}",
                r.status_code == 200
                and health.get("total", 0) >= 1
                # Only scanned versions carry a run; unscanned ones must not fake one.
                and any(item.get("last_run") is not None for item in health_items)
                and all(item.get("model_version_id") and item.get("n_predictions", -1) >= 0 for item in health_items),
            )

            # ------------------------------------------------ Phase 11: reports
            r = client.get("/api/v1/reports/catalog", headers=headers)
            catalog = r.json() if r.status_code == 200 else {}
            report(
                "reports-catalog",
                f"reports={len(catalog.get('items') or [])} formats={catalog.get('formats')}",
                r.status_code == 200
                and len(catalog.get("items") or []) >= 5
                and catalog.get("formats") == ["csv", "xlsx", "json"],
            )

            for fmt, magic in (("csv", None), ("xlsx", b"PK"), ("json", b"{")):
                r = client.post(
                    "/api/v1/reports/export",
                    headers=headers,
                    json={"report": "customers", "format": fmt},
                )
                body = r.content
                ok = (
                    r.status_code == 200
                    and r.headers.get("x-report-rows", "0").isdigit()
                    and "attachment" in r.headers.get("content-disposition", "")
                    and (magic is None or body[: len(magic)] == magic)
                )
                report(f"reports-export-{fmt}", f"bytes={len(body)} rows={r.headers.get('x-report-rows')}", ok)

            r = client.post(
                "/api/v1/reports/export",
                headers=headers,
                json={"report": "at_risk", "format": "csv", "filters": {"min_risk_level": "HIGH"}},
            )
            report(
                "reports-export-filtered",
                f"rows={r.headers.get('x-report-rows')}",
                r.status_code == 200 and int(r.headers.get("x-report-rows", "0")) >= 1,
            )

            bad_exports = 0
            for bad in (
                {"report": "not_a_report", "format": "csv"},
                {"report": "customers", "format": "pdf"},
                {"report": "customers", "format": "csv", "filters": {"nope": 1}},
                {"report": "customers", "format": "csv", "surprise": True},
            ):
                rr = client.post("/api/v1/reports/export", headers=headers, json=bad)
                if rr.status_code == 422:
                    bad_exports += 1
            report("reports-rejects-bad-input", f"rejected={bad_exports}/4", bad_exports == 4)

            print("\nSMOKE OK")
            return 0
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception:
            print(server_logs())
            raise
    finally:
        if log_handle is not None:
            log_handle.close()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())