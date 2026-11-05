# 4. Frontend Page Structure

## 4.1 Layouts
- AuthLayout (login/register)
- MainLayout (sidebar, header, user, org switch placeholder)

## 4.2 Pages
The shipped pages as of Phase 11 (each a top-level route wired in App.tsx):
1. Login (/login) · 2. Register (/register) · 3. Dashboard (/) · 4. Data Upload (/data/upload) · 5. Customers List (/customers) · 6. Customer 360 (/customers/:id — profile, RFM/CLV, behavioral features, churn risk + SHAP driver panel, what-if simulator) · 7. Segmentation (/segmentation) · 8. Model Management (/models) · 9. Model Monitoring (/monitoring — per-model health cards with a staleness flag; feature-drift, prediction-drift, and model-health cards that each name their baseline; per-feature table with PSI bars, KS, and reference vs current means/missingness; scan history that shows run #1 establishing the prediction baseline; explicit empty state when there is no model to compare against) · 10. Reports & Exports (/reports — catalogue-driven export cards with per-format buttons, a few real filter controls, blob download via Content-Disposition, and the row cap surfaced) · 11. At-Risk Board (/at-risk) · 12. Retention Recommendations (/retention) · 13. Analytics (/analytics — tabs: Cohorts heatmap, Customer journey, Product affinity) · 14. Campaigns (/campaigns — builder with segment/risk chips and assumption inputs; plan tab with forecast cards, ROI/break-even, experiment-power preview and launch; audience tab with paginated members and A/B badges; results tab with per-arm inputs and the A/B verdict card: Wilson intervals, lift, p-value, required sample size) · 15. AI Analyst (/ai/analyst) · 16. Settings (/settings)

Notes on the two Phase 11 pages:
- Monitoring surfaces the reason a verdict is missing ("no earlier scan to compare against", "fewer than 30 observations") instead of rendering a bare zero. PSI bars are capped at 0.5 so a large PSI still renders inside the column.
- Reports derives its format buttons from the server catalogue rather than hardcoding a list, so adding a backend format needs no frontend change; the download helper parses both the `filename*=UTF-8''` and the ASCII `filename` form of Content-Disposition.

## 4.3 Architecture
- React + Vite + TS, Tailwind + shadcn/ui, Recharts/Plotly, TanStack Query, React Router, Zod, Axios, React Hook Form.
- Service layer (api clients), types, hooks, utils, charts, components (atomic/composable).
- No business logic in components.
