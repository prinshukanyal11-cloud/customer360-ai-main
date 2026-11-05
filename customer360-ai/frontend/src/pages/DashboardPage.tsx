import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import client from "@/services/api";
import {
  buildFeatures,
  fetchClvDistribution,
  fetchFeaturesSummary,
  fetchRevenueTrends,
  fetchRfmDistribution,
} from "@/services/analytics";

interface DashboardSummary {
  total_customers: number;
  revenue: number;
  total_orders: number;
  avg_order_value: number | null;
  repeat_purchase_rate: number | null;
  churn_rate: number | null;
  avg_clv: number | null;
  at_risk_customers: number | null;
  revenue_at_risk: number | null;
  currency: string;
}

const SEGMENT_COLORS: Record<string, string> = {
  champions: "bg-emerald-500",
  loyal_customers: "bg-teal-500",
  potential_loyalists: "bg-cyan-500",
  new_customers: "bg-sky-500",
  promising: "bg-indigo-500",
  need_attention: "bg-amber-500",
  at_risk: "bg-orange-500",
  cannot_lose_them: "bg-rose-500",
  hibernating: "bg-purple-500",
  lost: "bg-slate-400",
};

const formatINR = (value: number | null | undefined, digits = 0): string =>
  value == null ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: digits })}`;

const formatPct = (value: number | null | undefined): string =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export function DashboardPage() {
  const queryClient = useQueryClient();

  const summary = useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: async () => (await client.get<DashboardSummary>("/dashboard/summary")).data,
  });
  const rfm = useQuery({ queryKey: ["analytics", "rfm"], queryFn: fetchRfmDistribution });
  const clv = useQuery({ queryKey: ["analytics", "clv"], queryFn: fetchClvDistribution });
  const trends = useQuery({ queryKey: ["analytics", "trends"], queryFn: () => fetchRevenueTrends() });
  const features = useQuery({ queryKey: ["analytics", "features"], queryFn: fetchFeaturesSummary });

  const data = summary.data;
  const cards = [
    { label: "Total Customers", value: data?.total_customers.toLocaleString() ?? "—" },
    { label: "Revenue", value: formatINR(data?.revenue) },
    { label: "Avg Order Value", value: formatINR(data?.avg_order_value, 2) },
    { label: "Repeat Purchase Rate", value: formatPct(data?.repeat_purchase_rate) },
    { label: "Churn Rate (90d)", value: formatPct(data?.churn_rate) },
    { label: "Average CLV", value: formatINR(data?.avg_clv ?? clv.data?.avg_clv, 2) },
    { label: "At-Risk Customers", value: data?.at_risk_customers?.toLocaleString() ?? "—" },
    { label: "Revenue at Risk", value: formatINR(data?.revenue_at_risk) },
  ];

  const maxSegment = Math.max(1, ...(rfm.data?.segments.map((s) => s.customer_count) ?? []));

  return (
    <div className="p-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Executive Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">
            KPIs are computed live from processed datasets · features rebuilt automatically after each process run.
          </p>
        </div>
        {features.data && features.data.buyers > 0 && (
          <button
            onClick={async () => {
              await buildFeatures();
              await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["analytics"] }),
                queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
              ]);
            }}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Rebuild features
          </button>
        )}
      </header>

      {summary.isError && (
        <div role="alert" className="mb-6 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {summary.error instanceof Error ? summary.error.message : "Failed to load dashboard."}
        </div>
      )}

      {summary.isLoading && <div className="text-sm text-slate-500">Loading dashboard…</div>}

      {!summary.isLoading && data && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {cards.map((card) => (
              <div key={card.label} className="rounded-lg border bg-white p-5 shadow-sm">
                <p className="text-sm font-medium text-slate-500">{card.label}</p>
                <p className="mt-2 text-2xl font-semibold text-slate-900">{card.value}</p>
              </div>
            ))}
          </div>

          {features.data !== undefined && features.data.as_of && (
            <p className="mt-4 text-xs text-slate-500">
              Features as of {features.data.as_of} · {features.data.buyers} buyers with RFM, coverage{" "}
              {Math.round(features.data.coverage * 100)}%
            </p>
          )}

          {data.total_customers === 0 ? (
            <div className="mt-8 rounded-lg border border-dashed bg-white p-8 text-center">
              <h2 className="font-medium text-slate-700">No customer data yet</h2>
              <p className="mt-1 text-sm text-slate-500">
                Upload a CSV dataset to unlock Customer 360, segmentation, churn prediction, and retention intelligence.
              </p>
            </div>
          ) : (
            <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <section className="rounded-lg border bg-white p-6">
                <h2 className="text-sm font-medium text-slate-500">Monthly revenue</h2>
                {trends.isLoading && <div className="mt-4 text-sm text-slate-500">Loading trend…</div>}
                {!trends.isLoading && trends.data && trends.data.items.length === 0 && (
                  <p className="mt-4 text-sm text-slate-500">No dated transactions yet.</p>
                )}
                {!trends.isLoading && trends.data && trends.data.items.length > 0 && (
                  <div className="mt-4 h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={trends.data.items}>
                        <defs>
                          <linearGradient id="revenue" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#0f172a" stopOpacity={0.25} />
                            <stop offset="95%" stopColor="#0f172a" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="month" tick={{ fontSize: 12 }} tickLine={false} />
                        <YAxis
                          tick={{ fontSize: 12 }}
                          tickLine={false}
                          axisLine={false}
                          tickFormatter={(v: number) =>
                            v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${v / 1_000}K` : String(v)
                          }
                        />
                        <Tooltip formatter={(v) => formatINR(Number(v), 2)} />
                        <Area
                          type="monotone"
                          dataKey="revenue"
                          stroke="#0f172a"
                          strokeWidth={2}
                          fill="url(#revenue)"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </section>

              <section className="rounded-lg border bg-white p-6">
                <h2 className="text-sm font-medium text-slate-500">RFM segment mix</h2>
                {rfm.isLoading && <div className="mt-4 text-sm text-slate-500">Loading segments…</div>}
                {!rfm.isLoading && rfm.data && rfm.data.segments.length === 0 && (
                  <p className="mt-4 text-sm text-slate-500">
                    No RFM segments yet — features are built automatically when a dataset is processed.
                  </p>
                )}
                {!rfm.isLoading && rfm.data && rfm.data.segments.length > 0 && (
                  <ul className="mt-4 space-y-2.5">
                    {rfm.data.segments.map((s) => (
                      <li key={s.segment} className="flex items-center gap-3 text-sm">
                        <span className="w-36 shrink-0 truncate text-slate-600 normal-case">{s.segment.replace(/_/g, " ")}</span>
                        <div className="h-2.5 flex-1 rounded-full bg-slate-100">
                          <div
                            className={`h-2.5 rounded-full ${SEGMENT_COLORS[s.segment] ?? "bg-slate-500"}`}
                            style={{ width: `${(s.customer_count / maxSegment) * 100}%` }}
                          />
                        </div>
                        <span className="w-16 shrink-0 text-right tabular-nums text-slate-900">{s.customer_count}</span>
                        <span className="w-12 shrink-0 text-right tabular-nums text-slate-400">
                          {Math.round(s.share * 100)}%
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}