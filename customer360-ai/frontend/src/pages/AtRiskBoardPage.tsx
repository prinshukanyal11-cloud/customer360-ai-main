import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import {
  explainChurn,
  fetchAtRisk,
  fetchCustomerExplanation,
  type AtRiskCustomer,
} from "@/services/models";

const RISK_BADGE: Record<string, string> = {
  LOW: "bg-emerald-50 text-emerald-700",
  MEDIUM: "bg-yellow-50 text-yellow-700",
  HIGH: "bg-orange-50 text-orange-700",
  CRITICAL: "bg-red-50 text-red-700",
};

const CATEGORY_BADGE: Record<string, string> = {
  PRIORITY: "bg-red-50 text-red-700",
  WIN_BACK: "bg-orange-50 text-orange-700",
  GROW: "bg-indigo-50 text-indigo-700",
  MONITOR: "bg-slate-100 text-slate-600",
};

const CATEGORY_HINT: Record<string, string> = {
  PRIORITY: "High churn risk, high value",
  WIN_BACK: "High churn risk, lower value",
  GROW: "Low churn risk, high value",
  MONITOR: "Low churn risk, lower value",
};

const formatPct = (value: number | null | undefined): string =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const formatMoney = (value: number | null | undefined): string =>
  value == null ? "—" : `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

function ExplanationPanel({ customer, onClose }: { customer: AtRiskCustomer; onClose: () => void }) {
  const explanation = useQuery({
    queryKey: ["explanation", customer.customer_id],
    queryFn: () => fetchCustomerExplanation(customer.customer_id),
  });

  return (
    <tr>
      <td colSpan={6} className="bg-slate-50 px-4 py-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-900">
            Why is {customer.name ?? `customer #${customer.customer_id}`} at risk?
          </h3>
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-500">
              Base {formatPct(explanation.data?.probability)} · contributions rank by absolute impact
            </span>
            <button onClick={onClose} className="text-xs text-slate-500 hover:text-slate-900">
              Close
            </button>
          </div>
        </div>
        {explanation.isLoading && <p className="mt-2 text-xs text-slate-500">Computing SHAP explanation…</p>}
        {explanation.isError && (
          <p className="mt-2 text-xs text-red-700">
            {explanation.error instanceof Error ? explanation.error.message : "Explanation unavailable."}
          </p>
        )}
        {explanation.data && (
          <div className="mt-3 space-y-1">
            {explanation.data.contributions.slice(0, 5).map((c) => {
              const direction = c.contribution == null ? 0 : Math.sign(c.contribution);
              return (
                <div key={c.feature} className="flex items-center gap-3 text-xs">
                  <span className="w-52 truncate text-slate-600">{c.label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded bg-slate-200">
                    <div
                      className={`h-full ${direction >= 0 ? "bg-red-400" : "bg-emerald-400"}`}
                      style={{ width: `${Math.min(100, Math.abs(c.share ?? 0))}%` }}
                    />
                  </div>
                  <span className="w-28 text-right tabular-nums text-slate-700">
                    {direction >= 0 ? "+" : ""}
                    {(c.contribution ?? 0).toFixed(4)} · {c.share?.toFixed(1)}%
                  </span>
                </div>
              );
            })}
            <p className="pt-1 text-[11px] text-slate-400">
              Positive bars push churn probability up; negative bars protect the customer.
            </p>
          </div>
        )}
      </td>
    </tr>
  );
}

function KpiCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export function AtRiskBoardPage() {
  const queryClient = useQueryClient();
  const [minRisk, setMinRisk] = useState("HIGH");
  const [expanded, setExpanded] = useState<number | null>(null);

  const atRisk = useQuery({
    queryKey: ["at-risk", minRisk],
    queryFn: () => fetchAtRisk(minRisk),
  });

  const explain = useMutation({
    mutationFn: () => explainChurn(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["at-risk"] });
      queryClient.invalidateQueries({ queryKey: ["explanation"] });
    },
  });

  const critical = atRisk.data?.summary.by_risk_level?.CRITICAL ?? 0;
  const high = atRisk.data?.summary.by_risk_level?.HIGH ?? 0;
  const priority = atRisk.data?.items.filter((item) => item.risk_category === "PRIORITY").length ?? 0;

  return (
    <div className="p-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">At-Risk Board</h1>
          <p className="mt-1 text-sm text-slate-500">
            The CLV × churn matrix: customers ranked by churn probability with the revenue at stake and SHAP drivers.
          </p>
        </div>
        <button
          onClick={() => explain.mutate()}
          disabled={explain.isPending}
          className="flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          <Sparkles className="h-4 w-4" />
          {explain.isPending ? "Explaining…" : "Explain drivers (SHAP)"}
        </button>
      </header>

      {explain.isError && (
        <p role="alert" className="mb-4 text-sm text-red-700">
          {explain.error instanceof Error ? explain.error.message : "Explanation failed."}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="At-risk customers" value={`${atRisk.data?.total ?? "—"}`} hint={`${critical} critical · ${high} high`} />
        <KpiCard
          label="Revenue at risk"
          value={formatMoney(atRisk.data?.summary.total_value_at_risk)}
          hint="Σ churn probability × CLV"
        />
        <KpiCard
          label="Median CLV"
          value={formatMoney(atRisk.data?.median_clv)}
          hint="Value cutoff for the matrix"
        />
        <KpiCard label="PRIORITY accounts" value={`${priority}`} hint="High risk + high value" />
      </div>

      {atRisk.isLoading && <div className="mt-8 text-sm text-slate-500">Loading at-risk customers…</div>}
      {atRisk.isError && (
        <div role="alert" className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {atRisk.error instanceof Error ? atRisk.error.message : "Failed to load at-risk customers."}
        </div>
      )}
      {!atRisk.isLoading && !atRisk.isError && atRisk.data?.total === 0 && (
        <div className="mt-8 rounded-lg border border-dashed bg-white p-8 text-center">
          <h2 className="font-medium text-slate-700">No scored customers yet</h2>
          <p className="mt-1 text-sm text-slate-500">
            Train and score a churn model first, then revisit the board to see at-risk accounts.
          </p>
          <Link to="/models" className="mt-3 inline-block text-sm font-medium text-slate-900 underline">
            Go to model management
          </Link>
        </div>
      )}
      {atRisk.data && atRisk.data.total > 0 && (
        <section className="mt-6 rounded-lg border bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3">
            <h2 className="font-medium text-slate-900">
              Ranked by churn probability
              <span className="ml-2 text-sm font-normal text-slate-500">({atRisk.data.items.length} shown)</span>
            </h2>
            <label className="flex items-center gap-1.5 text-sm text-slate-600">
              <span className="text-xs text-slate-500">Min risk</span>
              <select
                value={minRisk}
                onChange={(e) => {
                  setMinRisk(e.target.value);
                  setExpanded(null);
                }}
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-slate-400">
                  <th className="px-5 py-2 font-medium">Customer</th>
                  <th className="py-2 pr-3 font-medium">Churn probability</th>
                  <th className="py-2 pr-3 font-medium">Risk</th>
                  <th className="py-2 pr-3 font-medium">Category</th>
                  <th className="py-2 pr-3 text-right font-medium">Est. CLV</th>
                  <th className="py-2 pr-5 text-right font-medium">Value at risk</th>
                </tr>
              </thead>
              <tbody>
                {atRisk.data.items.map((item) => (
                  <ExplanationRows
                    key={item.prediction_id}
                    item={item}
                    expanded={expanded === item.prediction_id}
                    onToggle={() => setExpanded(expanded === item.prediction_id ? null : item.prediction_id)}
                    onClose={() => setExpanded(null)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function ExplanationRows({
  item,
  expanded,
  onToggle,
  onClose,
}: {
  item: AtRiskCustomer;
  expanded: boolean;
  onToggle: () => void;
  onClose: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50"
      >
        <td className="px-5 py-2.5 text-slate-700">
          <Link
            to={`/customers/${item.customer_id}`}
            className="font-medium text-slate-900 hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {item.name ?? `#${item.customer_id}`}
          </Link>
        </td>
        <td className="py-2.5 pr-3 tabular-nums text-slate-600">{formatPct(item.probability)}</td>
        <td className="py-2.5 pr-3">
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${RISK_BADGE[item.risk_level] ?? ""}`}>
            {item.risk_level}
          </span>
        </td>
        <td className="py-2.5 pr-3">
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium ${CATEGORY_BADGE[item.risk_category] ?? ""}`}
            title={CATEGORY_HINT[item.risk_category]}
          >
            {item.risk_category}
          </span>
        </td>
        <td className="py-2.5 pr-3 text-right tabular-nums text-slate-600">{formatMoney(item.clv_est)}</td>
        <td className="py-2.5 pr-5 text-right font-medium tabular-nums text-slate-900">
          {formatMoney(item.value_at_risk)}
        </td>
      </tr>
      {expanded && <ExplanationPanel customer={item} onClose={onClose} />}
    </>
  );
}