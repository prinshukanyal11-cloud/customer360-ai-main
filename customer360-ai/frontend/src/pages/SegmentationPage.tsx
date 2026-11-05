import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchClusterAnalytics,
  fetchSegments,
  renameSegment,
  trainSegmentation,
  type ClusterAnalytics,
  type Segment,
} from "@/services/segments";

const formatINR = (value: number | null | undefined, digits = 0): string =>
  value == null ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: digits })}`;

const formatPct = (value: number | null | undefined): string =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const METRIC_LABELS: Record<string, string> = {
  recency_days: "Recency (days)",
  frequency: "Orders",
  monetary: "Lifetime spend",
  aov: "Avg order value",
  avg_days_between: "Avg days between orders",
  tenure_days: "Tenure (days)",
  category_diversity: "Categories bought",
  discount_usage: "Discount usage",
  engagement_score: "Engagement score",
  clv_est: "Est. CLV",
};

function SegmentDot({ color }: { color: string | null }) {
  return (
    <span
      className="inline-block h-3 w-3 shrink-0 rounded-full"
      style={{ backgroundColor: color ?? "#cbd5e1" }}
      aria-hidden
    />
  );
}

function ClusterPanel({ segment }: { segment: Segment }) {
  const analytics = useQuery({
    queryKey: ["segments", "clusters", segment.id],
    queryFn: () => fetchClusterAnalytics(segment.id),
    enabled: segment.type === "ML" && segment.member_count > 0,
  });

  const data = analytics.data as ClusterAnalytics | undefined;

  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2">
        <SegmentDot color={segment.color} />
        <h3 className="font-medium text-slate-900">{segment.name}</h3>
        <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
          {segment.member_count} customers
        </span>
      </div>
      {segment.description && <p className="mt-1 text-xs text-slate-500">{segment.description}</p>}
      {segment.rules_json && (
        <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600">
          {JSON.stringify(segment.rules_json)}
        </pre>
      )}
      {analytics.isLoading && <div className="mt-3 text-sm text-slate-500">Loading analytics…</div>}
      {analytics.isError && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          Failed to load cluster analytics — features or segments may not be built yet.
        </p>
      )}
      {data && (
        <div className="mt-4 space-y-4">
          <div>
            <p className="text-xs font-medium text-slate-500">Profile averages</p>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(data.stats ?? []).map((stat) => (
                <div key={stat.metric} className="flex items-center justify-between rounded bg-slate-50 px-3 py-1.5 text-sm">
                  <span className="text-slate-600">{METRIC_LABELS[stat.metric] ?? stat.metric.replace(/_/g, " ")}</span>
                  <span className="font-medium tabular-nums text-slate-900">
                    {stat.metric === "clv_est" || stat.metric === "monetary" || stat.metric === "aov"
                      ? formatINR(stat.avg, 2)
                      : stat.metric === "recency_days" || stat.metric === "avg_days_between" || stat.metric === "tenure_days"
                        ? `${Math.round(stat.avg)}`
                        : stat.metric === "discount_usage"
                          ? formatPct(stat.avg)
                          : stat.avg.toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-xs font-medium text-slate-500">Top channels</p>
              {data.channels.length === 0 && <p className="mt-1 text-xs text-slate-400">—</p>}
              <ul className="mt-1 space-y-1 text-sm text-slate-700">
                {data.channels.map((c) => (
                  <li key={c.channel} className="flex justify-between">
                    <span className="capitalize">{c.channel}</span>
                    <span className="tabular-nums">{c.orders}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500">Top categories</p>
              {data.categories.length === 0 && <p className="mt-1 text-xs text-slate-400">—</p>}
              <ul className="mt-1 space-y-1 text-sm text-slate-700">
                {data.categories.map((c) => (
                  <li key={c.category} className="flex justify-between">
                    <span>{c.category}</span>
                    <span className="tabular-nums">{formatINR(c.revenue, 0)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-slate-500">
              Repeat-purchase share <span className="font-normal text-slate-400">(≥2 orders)</span>
            </p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums text-slate-900">{formatPct(data.repeat_purchase_share)}</p>
          </div>
          {data.top_members.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-500">Top members by CLV</p>
              <div className="mt-1 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-slate-400">
                      <th className="py-1 pr-3 font-medium">Customer</th>
                      <th className="py-1 pr-3 font-medium">Orders</th>
                      <th className="py-1 font-medium">CLV</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_members.map((m) => (
                      <tr key={m.customer_id} className="border-b border-slate-100 last:border-0">
                        <td className="py-1.5 pr-3 text-slate-700">
                          {m.name ?? m.external_customer_id ?? `#${m.customer_id}`}
                        </td>
                        <td className="py-1.5 pr-3 tabular-nums text-slate-600">{m.frequency ?? "—"}</td>
                        <td className="py-1.5 tabular-nums text-slate-600">{formatINR(m.clv_est, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SegmentationPage() {
  const queryClient = useQueryClient();
  const [nClusters, setNClusters] = useState("");
  const [usePca, setUsePca] = useState(true);
  const [randomSeed, setRandomSeed] = useState("");
  const [lastTrained, setLastTrained] = useState<{ version_no: number; n_clusters: number; silhouette: number | null } | null>(null);

  const segments = useQuery({
    queryKey: ["segments", "list"],
    queryFn: () => fetchSegments(),
  });
  const rfmSegments = (segments.data?.items ?? []).filter((s) => s.type === "RFM");
  const mlSegments = (segments.data?.items ?? []).filter((s) => s.type === "ML");

  const train = useMutation({
    mutationFn: () =>
      trainSegmentation({
        n_clusters: nClusters ? Number(nClusters) : undefined,
        use_pca: usePca,
        random_seed: randomSeed ? Number(randomSeed) : undefined,
      }),
    onSuccess: (result) => {
      setLastTrained({ version_no: result.version_no, n_clusters: result.n_clusters, silhouette: result.silhouette_score });
      setNClusters("");
      setRandomSeed("");
      queryClient.invalidateQueries({ queryKey: ["segments"] });
    },
  });

  const rename = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => renameSegment(id, { name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["segments"] }),
  });

  const handleRename = (segment: Segment) => {
    const name = window.prompt("Segment name", segment.name);
    if (!name?.trim()) return;
    rename.mutate({ id: segment.id, name: name.trim() });
  };

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Customer Segmentation</h1>
        <p className="mt-1 text-sm text-slate-500">
          K-Means clustering over Customer 360 features (recency, frequency, monetary, AOV, tenure, engagement, CLV) with
          optional PCA dimensionality reduction.
        </p>
      </header>

      <section className="rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="text-sm font-medium text-slate-500">Train a new model</h2>
        <div className="mt-3 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-slate-500">Clusters (2–8, blank = auto)</span>
            <input
              type="number"
              min={2}
              max={8}
              value={nClusters}
              onChange={(e) => setNClusters(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-slate-500">Random seed</span>
            <input
              type="number"
              min={0}
              value={randomSeed}
              onChange={(e) => setRandomSeed(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm text-slate-700">
            <input type="checkbox" checked={usePca} onChange={(e) => setUsePca(e.target.checked)} className="h-4 w-4" />
            Reduce dimensions with PCA
          </label>
          <button
            onClick={() => train.mutate()}
            disabled={train.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {train.isPending ? "Training…" : "Train segmentation"}
          </button>
        </div>
        {train.isError && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {train.error instanceof Error ? train.error.message : "Training failed."}
          </p>
        )}
        {lastTrained && !train.isError && (
          <p className="mt-3 text-sm text-slate-600">
            Trained v{lastTrained.version_no} · {lastTrained.n_clusters} clusters · silhouette{" "}
            {lastTrained.silhouette == null ? "—" : lastTrained.silhouette.toFixed(3)}
          </p>
        )}
      </section>

      {mlSegments.length > 0 && (
        <section className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-2">
          {mlSegments.map((segment) => (
            <ClusterPanel key={segment.id} segment={segment} />
          ))}
        </section>
      )}

      {rfmSegments.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 text-sm font-medium text-slate-500">Rule-based (RFM) segments</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {rfmSegments.map((segment) => (
              <div key={segment.id} className="rounded-lg border bg-white p-4 shadow-sm">
                <div className="flex items-center gap-2">
                  <SegmentDot color={segment.color} />
                  <h3 className="font-medium capitalize text-slate-900">{segment.name.replace(/_/g, " ")}</h3>
                </div>
                <p className="mt-2 text-sm text-slate-500">{segment.member_count} customers</p>
                <button
                  onClick={() => handleRename(segment)}
                  className="mt-2 text-xs font-medium text-slate-400 hover:text-slate-700"
                >
                  Rename
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {segments.isLoading && <div className="mt-8 text-sm text-slate-500">Loading segments…</div>}
      {!segments.isLoading && segments.data && segments.data.items.length === 0 && (
        <p className="mt-8 text-sm text-slate-500">
          No segments yet — upload and process a dataset to build RFM segments, then train a segmentation model above.
        </p>
      )}
    </div>
  );
}