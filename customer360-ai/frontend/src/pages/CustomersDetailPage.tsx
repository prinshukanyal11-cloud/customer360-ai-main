import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import client from "@/services/api";
import {
  fetchCustomerExplanation,
  runCustomerWhatIf,
  type ExplanationContribution,
  type WhatIfResult,
} from "@/services/models";

interface Customer360 {
  customer: {
    id: number;
    external_customer_id: string;
    name: string | null;
    email: string | null;
    location: string | null;
    signup_date: string | null;
  };
  purchase_metrics: {
    total_orders: number;
    total_spending: number;
    avg_order_value: number | null;
    first_purchase: string | null;
    last_purchase: string | null;
  };
  rfm: {
    recency_days: number | null;
    frequency: number | null;
    monetary: number | null;
    rfm_score: string | null;
    rfm_segment: string | null;
  } | null;
  behavioral: {
    aov: number | null;
    tenure_days: number | null;
    avg_days_between: number | null;
    category_diversity: number | null;
    discount_usage: number | null;
    engagement_score: number | null;
  } | null;
  value: { clv_est: number | null; value_at_risk: number | null };
  risk: { churn_probability: number | null; risk_level: string | null; risk_category: string | null } | null;
  transactions: { id: number; order_date: string; amount: number; channel: string | null }[];
}

const formatINR = (value: number | null | undefined, digits = 0): string =>
  value == null ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: digits })}`;

const SEGMENT_STYLE: Record<string, string> = {
  champions: "bg-emerald-50 text-emerald-700",
  loyal_customers: "bg-teal-50 text-teal-700",
  potential_loyalists: "bg-cyan-50 text-cyan-700",
  new_customers: "bg-sky-50 text-sky-700",
  promising: "bg-indigo-50 text-indigo-700",
  need_attention: "bg-amber-50 text-amber-700",
  at_risk: "bg-orange-50 text-orange-700",
  cannot_lose_them: "bg-rose-50 text-rose-700",
  hibernating: "bg-purple-50 text-purple-700",
  lost: "bg-slate-100 text-slate-600",
};

export function CustomersDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["customer", id],
    queryFn: async () => {
      const res = await client.get<Customer360>(`/customers/${id}`);
      return res.data;
    },
  });

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Customer 360</h1>
      </header>

      {isError && (
        <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {error instanceof Error ? error.message : "Failed to load customer."}
        </div>
      )}
      {isLoading && <div className="text-sm text-slate-500">Loading Customer 360 profile…</div>}

      {!isLoading && data && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <section className="rounded-lg border bg-white p-6">
            <h2 className="text-sm font-medium text-slate-500">Profile</h2>
            <p className="mt-3 text-lg font-semibold text-slate-900">
              {data.customer.name ?? data.customer.external_customer_id}
            </p>
            <p className="text-sm text-slate-500">{data.customer.email ?? data.customer.external_customer_id}</p>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">Location</dt>
                <dd className="text-slate-900">{data.customer.location ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Signup</dt>
                <dd className="text-slate-900">{data.customer.signup_date ?? "—"}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border bg-white p-6">
            <h2 className="text-sm font-medium text-slate-500">Purchase metrics</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">Orders</dt>
                <dd className="text-slate-900">{data.purchase_metrics.total_orders}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Total spending</dt>
                <dd className="text-slate-900">₹{data.purchase_metrics.total_spending.toLocaleString("en-IN")}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Avg order value</dt>
                <dd className="text-slate-900">
                  {data.purchase_metrics.avg_order_value != null
                    ? `₹${data.purchase_metrics.avg_order_value.toLocaleString("en-IN")}`
                    : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Last purchase</dt>
                <dd className="text-slate-900">{data.purchase_metrics.last_purchase ?? "—"}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border bg-white p-6">
            <h2 className="text-sm font-medium text-slate-500">RFM &amp; value</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">RFM segment</dt>
                <dd>
                  {data.rfm?.rfm_segment ? (
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium normal-case ${SEGMENT_STYLE[data.rfm.rfm_segment] ?? "bg-slate-100 text-slate-600"}`}
                    >
                      {data.rfm.rfm_segment.replace(/_/g, " ")}
                    </span>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">RFM score</dt>
                <dd className="font-medium tabular-nums text-slate-900">
                  {data.rfm?.rfm_score
                    ? (() => {
                        const rfmScore = data.rfm!.rfm_score!.split("");
                        return `R${rfmScore[0]} · F${rfmScore[1]} · M${rfmScore[2]}`;
                      })()
                    : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Recency</dt>
                <dd className="text-slate-900">
                  {data.rfm?.recency_days != null ? `${data.rfm.recency_days} days` : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Frequency</dt>
                <dd className="text-slate-900">{data.rfm?.frequency ?? "—"} orders</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Monetary</dt>
                <dd className="text-slate-900">{formatINR(data.rfm?.monetary, 2)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">CLV estimate</dt>
                <dd className="text-slate-900">{formatINR(data.value.clv_est, 2)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Churn probability</dt>
                <dd className="text-slate-900">
                  {data.risk?.churn_probability != null
                    ? `${(data.risk.churn_probability * 100).toFixed(0)}%`
                    : "Train a model to see predictions"}
                </dd>
              </div>
              {data.risk?.risk_category && (
                <div className="flex justify-between">
                  <dt className="text-slate-500">Risk category</dt>
                  <dd className="text-slate-900">{data.risk.risk_category}</dd>
                </div>
              )}
              {data.value.value_at_risk != null && (
                <div className="flex justify-between">
                  <dt className="text-slate-500">Value at risk</dt>
                  <dd className="font-medium text-red-700">{formatINR(data.value.value_at_risk, 2)}</dd>
                </div>
              )}
            </dl>
          </section>

          <section className="rounded-lg border bg-white p-6">
            <h2 className="text-sm font-medium text-slate-500">Behavioral features</h2>
            <dl className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">Active tenure</dt>
                <dd className="text-slate-900">
                  {data.behavioral?.tenure_days != null
                    ? `${Math.round(data.behavioral.tenure_days / 365)}y ${Math.round(data.behavioral.tenure_days) % 365}d`
                    : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Avg days between orders</dt>
                <dd className="text-slate-900">
                  {data.behavioral?.avg_days_between != null ? `${Math.round(data.behavioral.avg_days_between)} days` : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Category diversity</dt>
                <dd className="text-slate-900">{data.behavioral?.category_diversity ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Discount usage</dt>
                <dd className="text-slate-900">
                  {data.behavioral?.discount_usage != null
                    ? `${(data.behavioral.discount_usage * 100).toFixed(0)}%`
                    : "—"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Engagement score</dt>
                <dd className="text-slate-900">
                  {data.behavioral?.engagement_score != null ? `${Math.round(data.behavioral.engagement_score)}/100` : "—"}
                </dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border bg-white p-6 lg:col-span-3">
            <h2 className="mb-4 text-sm font-medium text-slate-500">Transactions</h2>
            {data.transactions.length === 0 ? (
              <p className="text-sm text-slate-500">No transactions recorded for this customer.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th className="pb-2">Order</th>
                      <th className="pb-2">Date</th>
                      <th className="pb-2">Amount</th>
                      <th className="pb-2">Channel</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {data.transactions.map((t) => (
                      <tr key={t.id}>
                        <td className="py-2 text-slate-600">{t.id}</td>
                        <td className="py-2 text-slate-600">{t.order_date}</td>
                        <td className="py-2 text-slate-600">₹{t.amount.toLocaleString("en-IN")}</td>
                        <td className="py-2 text-slate-600">{t.channel ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <ExplanationSection id={id ?? ""} />
          <WhatIfSection id={id ?? ""} />
        </div>
      )}
    </div>
  );
}

function ExplanationSection({ id }: { id: string }) {
  const explanation = useQuery({
    queryKey: ["explanation", Number(id)],
    queryFn: () => fetchCustomerExplanation(Number(id)),
    retry: 0,
  });

  return (
    <section className="rounded-lg border bg-white p-6 lg:col-span-3">
      <h2 className="mb-1 text-sm font-medium text-slate-500">Why is this customer at risk? (SHAP)</h2>
      {explanation.isLoading && <p className="mt-3 text-sm text-slate-500">Computing SHAP explanation…</p>}
      {explanation.isError && (
        <p className="mt-3 text-sm text-slate-500">
          {(explanation.error as { response?: { data?: { error?: { message?: string } } } })?.response?.data?.error
            ?.message ?? "No explanation available yet. Score customers and explain drivers."}
        </p>
      )}
      {explanation.data && <Contributions list={explanation.data.contributions} />}
    </section>
  );
}

function Contributions({ list }: { list: ExplanationContribution[] }) {
  if (list.length === 0) {
    return <p className="mt-3 text-sm text-slate-500">No feature contributions available for this customer.</p>;
  }
  return (
    <div className="mt-3 space-y-1.5">
      {list.slice(0, 8).map((c) => {
        const direction = c.contribution == null ? 0 : Math.sign(c.contribution);
        return (
          <div key={c.feature} className="flex items-center gap-3 text-sm">
            <span className="w-48 shrink-0 truncate text-slate-600">{c.label}</span>
            <span className="w-20 shrink-0 text-right tabular-nums text-slate-400">
              {c.value != null ? c.value : "—"}
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded bg-slate-200">
              <div
                className={`h-full ${direction >= 0 ? "bg-red-400" : "bg-emerald-400"}`}
                style={{ width: `${Math.min(100, Math.abs(c.share ?? 0))}%` }}
              />
            </div>
            <span className="w-28 shrink-0 text-right tabular-nums text-slate-700">
              {direction >= 0 ? "+" : ""}
              {(c.contribution ?? 0).toFixed(4)}
            </span>
          </div>
        );
      })}
      <p className="pt-1 text-xs text-slate-400">
        SHAP contributions in model units — positive values push churn probability up, negative values protect.
      </p>
    </div>
  );
}

const SCENARIO_PRESETS: Array<{ id: string; label: string }> = [
  { id: "re_engage", label: "Re-engage (drop recency to 15d)" },
  { id: "increase_frequency", label: "Increase frequency to 6" },
  { id: "raise_basket", label: "Raise AOV to ₹400" },
  { id: "reduce_discount", label: "Reduce discount to 10%" },
  { id: "boost_engagement", label: "Boost engagement to 70" },
  { id: "diversify_categories", label: "Add categories (3)" },
];

const WHATIF_FEATURES: Array<{ id: string; label: string }> = [
  { id: "recency_days", label: "Recency (days)" },
  { id: "frequency", label: "Frequency" },
  { id: "monetary", label: "Monetary" },
  { id: "aov", label: "Avg order value" },
  { id: "avg_days_between", label: "Days between orders" },
  { id: "tenure_days", label: "Tenure (days)" },
  { id: "category_diversity", label: "Categories" },
  { id: "discount_usage", label: "Discount usage" },
  { id: "engagement_score", label: "Engagement score" },
  { id: "clv_est", label: "CLV estimate" },
];

function WhatIfSection({ id }: { id: string }) {
  const customerId = Number(id);
  const [preset, setPreset] = useState("");
  const [changes, setChanges] = useState<Record<string, number>>({});

  const simulate = useMutation({
    mutationFn: () =>
      runCustomerWhatIf(customerId, preset ? { scenario: preset } : { changes }),
  });

  return (
    <section className="rounded-lg border bg-white p-6 lg:col-span-3">
      <h2 className="mb-1 text-sm font-medium text-slate-500">What-if simulator</h2>
      <p className="mb-4 text-xs text-slate-400">
        Re-score this customer under a hypothetical change using the active churn model. Hypothesis only — not a
        guarantee.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm">
          <span className="text-slate-600">Scenario preset</span>
          <select
            value={preset}
            onChange={(e) => {
              setPreset(e.target.value);
              if (simulate.data) simulate.reset();
            }}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">— custom changes below —</option>
            {SCENARIO_PRESETS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        <div className="rounded-md bg-slate-50 p-3 text-xs text-slate-500">
          <p className="mb-1 font-medium text-slate-700">Quick confidence check</p>
          <p>A preset simulates a marketing outcome (e.g. a win-back). Custom changes override the same features.</p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
        {WHATIF_FEATURES.filter((f) => changes[f.id] !== undefined).map((f) => (
          <label key={f.id} className="block text-sm">
            <span className="flex items-center justify-between text-xs text-slate-500">
              {f.label}
              <button
                type="button"
                onClick={() => setChanges((prev) => {
                  const next = { ...prev };
                  delete next[f.id];
                  return next;
                })}
                className="text-slate-400 hover:text-red-600"
              >
                remove
              </button>
            </span>
            <input
              type="number"
              step="any"
              min="0"
              value={changes[f.id]}
              onChange={(e) => setChanges((prev) => ({ ...prev, [f.id]: Number(e.target.value) }))}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
        ))}
        <label className="block text-sm">
          <span className="text-xs text-slate-500">Add a feature</span>
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) setChanges((prev) => ({ ...prev, [e.target.value]: 0 }));
            }}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">None</option>
            {WHATIF_FEATURES.filter((f) => changes[f.id] === undefined).map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        onClick={() => simulate.mutate()}
        disabled={simulate.isPending || (!preset && Object.keys(changes).length === 0)}
        className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {simulate.isPending ? "Simulating…" : "Simulate change"}
      </button>

      {simulate.isError && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {simulate.error instanceof Error ? simulate.error.message : "Simulation failed."}
        </p>
      )}
      {simulate.data && <SimulationResult result={simulate.data} />}
    </section>
  );
}

function SimulationResult({ result }: { result: WhatIfResult }) {
  const fmtPct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const delta = result.delta.probability;
  const improved = delta < 0;
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-40">
          <p className="text-xs text-slate-500">Baseline churn</p>
          <p className="text-xl font-semibold tabular-nums text-slate-900">
            {fmtPct(result.baseline.probability)}
          </p>
          <p className="text-xs text-slate-400">
            {result.baseline.risk_level} · {result.baseline.risk_category}
          </p>
        </div>
        <div className="min-w-40">
          <p className="text-xs text-slate-500">After change</p>
          <p className={`text-xl font-semibold tabular-nums ${improved ? "text-emerald-700" : "text-slate-900"}`}>
            {fmtPct(result.scenario_output.probability)}
          </p>
          <p className="text-xs text-slate-400">
            {result.scenario_output.risk_level} · {result.scenario_output.risk_category}
          </p>
        </div>
        <div className="min-w-40">
          <p className="text-xs text-slate-500">Delta</p>
          <p className={`text-xl font-semibold tabular-nums ${improved ? "text-emerald-700" : "text-red-700"}`}>
            {delta > 0 ? "+" : ""}
            {(delta * 100).toFixed(1)}pp
          </p>
          <p className="text-xs text-slate-400">
            Value at risk: {formatINR(result.scenario_output.value_at_risk, 0)}
          </p>
        </div>
        <div className="min-w-40">
          <p className="text-xs text-slate-500">Change applied</p>
          <p className="text-sm text-slate-700">
            {result.scenario ? SCENARIO_PRESETS.find((s) => s.id === result.scenario)?.label : "custom"}
          </p>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-400">{result.note}</p>
    </div>
  );
}