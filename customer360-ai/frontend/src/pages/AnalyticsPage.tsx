import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCohorts,
  fetchJourney,
  fetchProducts,
  type GlobalPairEntry,
  type ProductMeta,
  type RecommendationEntry,
} from "@/services/models";

const STAGE_COLORS: Record<string, string> = {
  NEW: "bg-sky-500",
  EARLY: "bg-teal-500",
  REGULAR: "bg-emerald-500",
  AT_RISK: "bg-amber-500",
  DORMANT: "bg-orange-500",
  LOST: "bg-red-500",
};

const STAGE_ORDER = ["NEW", "EARLY", "REGULAR", "AT_RISK", "DORMANT", "LOST"];

type Tab = "cohorts" | "journey" | "products";

const TABS: { id: Tab; label: string }[] = [
  { id: "cohorts", label: "Cohorts" },
  { id: "journey", label: "Customer journey" },
  { id: "products", label: "Product affinity" },
];

export function AnalyticsPage() {
  const [tab, setTab] = useState<Tab>("cohorts");
  const [months, setMonths] = useState(12);

  const cohorts = useQuery({ queryKey: ["cohorts", months], queryFn: () => fetchCohorts(months) });
  const journey = useQuery({ queryKey: ["journey"], queryFn: fetchJourney });
  const products = useQuery({ queryKey: ["products"], queryFn: () => fetchProducts({ limit: 20 }) });

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Analytics</h1>
        <p className="mt-1 text-sm text-slate-500">
          Cohort retention, customer journey stages, and cross-product co-purchase signals across your book.
        </p>
      </header>

      <div className="mb-5 flex items-center gap-1 rounded-lg border bg-white p-1 shadow-sm">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${
              tab === id ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "cohorts" && <CohortsTab cohorts={cohorts.data} isLoading={cohorts.isLoading} isError={cohorts.isError} months={months} setMonths={setMonths} />}
      {tab === "journey" && <JourneyTab data={journey.data} isLoading={journey.isLoading} isError={journey.isError} />}
      {tab === "products" && <ProductsTab data={products.data} isLoading={products.isLoading} isError={products.isError} />}
    </div>
  );
}

interface TabProps {
  isLoading: boolean;
  isError: boolean;
}

function LoadOrError({ isLoading, isError }: TabProps) {
  if (isLoading) return <div className="mt-6 text-sm text-slate-500">Loading…</div>;
  if (isError)
    return (
      <div role="alert" className="mt-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
        Failed to load analytics.
      </div>
    );
  return null;
}

function CohortsTab({
  cohorts,
  months,
  setMonths,
  isLoading,
  isError,
}: {
  cohorts?: ReturnType<typeof fetchCohorts> extends Promise<infer T> ? T : never;
  months: number;
  setMonths: (v: number) => void;
} & TabProps) {
  const maxLag = cohorts?.cohorts.reduce((m, c) => Math.max(m, c.retention.length), 0) ?? 0;
  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="font-medium text-slate-900">Monthly cohort retention</h2>
          <p className="mt-1 text-sm text-slate-500">
            % of customers in each acquisition month still placing orders in later months.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <span className="text-xs text-slate-500">Window</span>
          <select
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {[6, 12, 24, 36].map((m) => (
              <option key={m} value={m}>
                {m} months
              </option>
            ))}
          </select>
        </label>
      </div>

      <LoadOrError isLoading={isLoading} isError={isError} />

      {cohorts?.cohorts.length ? (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Kpi label="Customers" value={cohorts.summary.total_customers} />
            <Kpi label="Repeat purchase share" value={`${(cohorts.summary.repeat_share * 100).toFixed(1)}%`} />
            <Kpi label="Avg orders / customer" value={cohorts.summary.avg_orders_per_customer.toFixed(1)} />
            <Kpi label="Cohorts analyzed" value={cohorts.summary.cohorts_analyzed} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="px-2 py-2 text-left text-xs font-medium text-slate-400">Cohort</th>
                  {Array.from({ length: maxLag }).map((_, i) => (
                    <th key={i} className="px-2 py-2 text-center text-xs font-medium text-slate-400">
                      {i === 0 ? "Mo 0" : `Mo ${i}`}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cohorts.cohorts.map((row) => (
                  <tr key={row.cohort}>
                    <td className="px-2 py-1.5 font-medium text-slate-700">
                      {row.cohort}
                      <span className="ml-1.5 text-xs font-normal text-slate-400">n={row.customers}</span>
                    </td>
                    {Array.from({ length: maxLag }).map((_, i) => {
                      const value = row.retention[i];
                      if (value === undefined) {
                        return <td key={i} className="px-2 py-1.5 text-center text-xs text-slate-300">—</td>;
                      }
                      return (
                        <td
                          key={i}
                          className="px-2 py-1.5 text-center text-xs font-medium tabular-nums"
                          style={{ backgroundColor: `rgba(16, 185, 129, ${0.08 + value * 0.55})` }}
                          title={`${row.cohort} month ${i}: ${(value * 100).toFixed(0)}%`}
                        >
                          {value > 0 ? `${(value * 100).toFixed(0)}%` : "0%"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-slate-400">As of {cohorts.as_of ?? "latest data"}.</p>
        </>
      ) : (
        !isLoading &&
        !isError && (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-slate-500">
            Process transaction data to compute cohort retention.
          </div>
        )
      )}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-slate-50 px-4 py-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{value}</p>
    </div>
  );
}

function JourneyTab({
  data,
  isLoading,
  isError,
}: {
  data?: ReturnType<typeof fetchJourney> extends Promise<infer T> ? T : never;
} & TabProps) {
  const ordered = (data?.stages ?? []).slice().sort(
    (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage) || b.customers - a.customers,
  );
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="rounded-lg border bg-white p-5 shadow-sm">
        <h2 className="font-medium text-slate-900">Lifecycle stage mix</h2>
        <p className="mt-1 text-sm text-slate-500">
          Where customers sit today based on order count and purchase recency (as of {data?.as_of ?? "latest"}).
        </p>
        <LoadOrError isLoading={isLoading} isError={isError} />
        {ordered.length ? (
          <div className="mt-4 space-y-3">
            {ordered.map((s) => (
              <div key={s.stage}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 font-medium text-slate-700">
                    <span className={`h-2.5 w-2.5 rounded-full ${STAGE_COLORS[s.stage] ?? "bg-slate-300"}`} />
                    {s.stage.replace("_", " ")}
                  </span>
                  <span className="tabular-nums text-slate-500">
                    {s.customers} · {(s.share * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full ${STAGE_COLORS[s.stage] ?? "bg-slate-300"}`}
                    style={{ width: `${s.share * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        ) : (
          !isLoading &&
          !isError && <div className="mt-4 text-sm text-slate-500">No processed customers yet.</div>
        )}

        {data && (
          <div className="mt-6 border-t pt-4">
            <h3 className="text-sm font-medium text-slate-700">Repeat behavior</h3>
            <p className="mt-1 text-sm text-slate-500">
              Avg orders per customer:{" "}
              <span className="font-medium tabular-nums text-slate-900">
                {data.avg_orders_per_customer.toFixed(2)}
              </span>
              {data.avg_time_to_repeat_days != null && (
                <>
                  {" "}· Median days to second purchase:{" "}
                  <span className="font-medium tabular-nums text-slate-900">
                    {data.median_time_to_repeat_days} days
                  </span>
                </>
              )}
            </p>
          </div>
        )}
      </section>

      <section className="rounded-lg border bg-white p-5 shadow-sm">
        <h2 className="font-medium text-slate-900">Milestone funnel</h2>
        <p className="mt-1 text-sm text-slate-500">Customers reaching each repurchase milestone.</p>
        <LoadOrError isLoading={isLoading} isError={isError} />
        {data?.milestones.length ? (
          <div className="mt-4 space-y-2">
            {data.milestones.map((m) => {
              const width = m.share * 100;
              return (
                <div key={m.threshold} className="flex items-center gap-3">
                  <span className="w-24 shrink-0 text-sm text-slate-600">{m.label}</span>
                  <div className="h-6 flex-1 overflow-hidden rounded bg-slate-100">
                    <div
                      className="flex h-full items-center rounded bg-slate-900 px-2 text-xs font-medium text-white"
                      style={{ width: `${Math.max(width, 8)}%` }}
                    >
                      {m.customers}
                    </div>
                  </div>
                  <span className="w-12 shrink-0 text-right text-xs tabular-nums text-slate-400">
                    {width.toFixed(0)}%
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}

        {data?.time_to_repeat.length ? (
          <div className="mt-6 border-t pt-4">
            <h3 className="text-sm font-medium text-slate-700">Days to repeat purchase</h3>
            <div className="mt-3 space-y-1.5">
              {data.time_to_repeat.map((b) => (
                <div key={b.range} className="flex items-center justify-between text-sm">
                  <span className="text-slate-600">{b.range}</span>
                  <span className="tabular-nums text-slate-500">
                    {b.customers} · {(b.share * 100).toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function ProductsTab({
  data,
  isLoading,
  isError,
}: {
  data?: ReturnType<typeof fetchProducts> extends Promise<infer T> ? T : never;
} & TabProps) {
  const [productId, setProductId] = useState<number | null>(null);
  const drill = useQuery({
    queryKey: ["products-drill", productId],
    queryFn: () => fetchProducts({ product_id: productId ?? undefined, limit: 10 }),
    enabled: productId != null,
  });

  const productOptions = data?.recommendations
    .filter((r): r is GlobalPairEntry => isPair(r) && Boolean((r as GlobalPairEntry).product_a?.id))
    .map((r) => (r as GlobalPairEntry).product_a)
    .filter((m, idx, arr) => arr.findIndex((x) => x.id === m.id) === idx);

  const shown = productId != null ? drill.data : data;
  const isPair = (r: RecommendationEntry | GlobalPairEntry): r is GlobalPairEntry => "product_a" in r;

  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-medium text-slate-900">Cross-product affinity</h2>
          <p className="mt-1 text-sm text-slate-500">
            Customer-basket pairs: customers who bought both products, with confidence and lift.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <span className="text-xs text-slate-500">Drill into</span>
          <select
            value={productId ?? ""}
            onChange={(e) => setProductId(e.target.value ? Number(e.target.value) : null)}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Global top pairs</option>
            {(productOptions ?? []).map((m: ProductMeta) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <LoadOrError isLoading={isLoading && !productId} isError={isError} />
      {(drill.isLoading && productId != null) && <div className="mt-4 text-sm text-slate-500">Loading…</div>}

      {!shown ? null : isPair(shown.recommendations[0]) ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-slate-400">
              <th className="py-2 pr-3 font-medium">Pair</th>
              <th className="py-2 pr-3 text-right font-medium">Co-purchases</th>
              <th className="py-2 pr-3 text-right font-medium">Confidence</th>
              <th className="py-2 pr-3 text-right font-medium">Lift</th>
            </tr>
          </thead>
          <tbody>
            {(shown.recommendations as GlobalPairEntry[]).map((r, i) => (
              <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="py-2.5 pr-3 text-slate-700">
                  <span className="font-medium text-slate-900">{r.product_a.name ?? r.product_a.id}</span>
                  {" + "}
                  <span className="font-medium text-slate-900">{r.product_b.name ?? r.product_b.id}</span>
                </td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{r.co_purchases}</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{(r.confidence * 100).toFixed(0)}%</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{r.lift.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-slate-400">
              <th className="py-2 pr-3 font-medium">Recommended product</th>
              <th className="py-2 pr-3 text-right font-medium">Co-purchases</th>
              <th className="py-2 pr-3 text-right font-medium">Confidence</th>
              <th className="py-2 pr-3 text-right font-medium">Score</th>
            </tr>
          </thead>
          <tbody>
            {(shown.recommendations as RecommendationEntry[]).map((r) => (
              <tr key={r.product_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="py-2.5 pr-3 text-slate-700">
                  <span className="font-medium text-slate-900">{r.name ?? "Product"}</span>
                  {r.category ? <span className="ml-1.5 text-xs text-slate-400">{r.category}</span> : null}
                </td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{r.co_purchases}</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{(r.confidence * 100).toFixed(0)}%</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">{r.score.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-3 text-xs text-slate-400">
        {productId != null ? "Item-to-item recommendations for the selected product." : "Top global co-purchase pairs."}
      </p>
    </div>
  );
}