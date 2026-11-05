# data/

Runtime data directory for Customer360 AI.

| Path | Purpose |
|---|---|
| `sample/` | Synthetic/demo datasets (`customers.csv`, `transactions.csv`) used for local development and Demo Mode. Clearly labeled as demo data. |
| `uploads/` | Files uploaded through the Data Ingestion workflow (gitignored). |

## Regenerate sample data

```bash
python scripts/generate_sample_data.py --customers 500 --seed 42
```

Sample data is intentionally realistic in structure but entirely synthetic —
never present its results as real business metrics.