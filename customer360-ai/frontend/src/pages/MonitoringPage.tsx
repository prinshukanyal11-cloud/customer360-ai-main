import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, Gauge, RefreshCw } from "lucide-react";
import {
  fetchDriftReport,
  fetchModelHealth,
  fetchMonitoringRuns,
  recordMonitoringRun,
  type DriftStatus,
  type FeatureDriftMetric,
} from "@/services/models";

const STATUS_BADGE: Record<DriftStatus, string> = {
  STABLE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  MODERATE: "bg-amber-50 text-amber-700 border-amber-200",
  SIGNIFICANT: "bg-red-50 text-red-700 border-red-200",
  UNKNOWN: "bg-slate-100 text-slate-600 border-slate-200",
  NO_DATA: "bg-slate-50 text-slate-500 border-slate-200",
};

const STATUS_LABEL: Record<DriftStatus, string> = {
  STABLE: "No meaningful drift",
  MODERATE: "Moderate drift",
  SIGNIFICANT: "Significant drift",
  UNKNOWN: "Not enough data yet",
  NO_DATA: "No comparable data",
};

function StatusPill({ status }: { status: DriftStatus }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${STATUS_BADGE[status]}`}
    >
      {status}
    </span>
  );
}

/** PSI bar. The scale is capped at SIGNIFICANT so a huge PSI still renders. */
function PsiBar({ psi, status }: { psi: number | null; status: DriftStatus }) {
  const cap = 0.5;
  const ratio = psi == null ? 0 : Math.min(psi / cap, 1);
  const color =
    status === "SIGNIFICANT" ? "bg-red-500" : status === "MODERATE" ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded bg-slate-100">
        <div className={`h-full ${psi == null ? "bg-slate-300" : color}`} style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className="w-12 text-right font-mono text-xs text-slate-700">
        {psi == null ? "n/a" : psi.toFixed(3)}
      </span>
    </div>
  );
}

function num(value: number | null | undefined, digits = 2): string {
  return value == null ? "n/a" : value.toFixed(digits);
}

function FeatureRow({ metric }: { metric: FeatureDriftMetric }) {
  return (
    <tr className="border-t border-slate-100">
      <td className="px-3 py-2 font-medium text-slate-800">{metric.feature}</td>
      <td className="px-3 py-2">
        <StatusPill status={metric.status} />
      </td>
      <td className="px-3 py-2">
        <PsiBar psi={metric.psi} status={metric.status} />
      </td>
      <td className="px-3 py-2 font-mono text-xs text-slate-600">{num(metric.ks, 3)}</td>
      <td className="px-3 py-2 text-right font-mono text-xs text-slate-600">
        {num(metric.reference.mean)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs text-slate-600">{num(metric.current.mean)}</td>
      <td className="px-3 py-2 text-right font-mono text-xs text-slate-600">
        {num(metric.reference.missing_rate, 3)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs text-slate-600">
        {num(metric.current.missing_rate, 3)}
      </td>
    </tr>
  );
}

export function MonitoringPage() {
  const queryClient = useQueryClient();
  const [versionId, setVersionId] = useState<number | undefined>(undefined);

  const health = useQuery({ queryKey: ["monitoring", "health"], queryFn: fetchModelHealth });
  const drift = useQuery({
    queryKey: ["monitoring", "drift", versionId],
    queryFn: () => fetchDriftReport(versionId),
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["monitoring", "runs"],
    queryFn: () => fetchMonitoringRuns({ limit: 10 }),
  });

  const scan = useMutation({
    mutationFn: () => recordMonitoringRun(versionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["monitoring"] });
    },
  });

  const models = health.data?.items ?? [];
  const report = drift.data;
  const drifted = report?.features.filter((f) => f.status === "SIGNIFICANT" || f.status === "MODERATE") ?? [];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
            <Gauge className="h-5 w-5" />
            Model Monitoring
          </h1>
          <p className="text-sm text-slate-500">
            Feature and prediction drift for the active churn model, measured against its training baseline.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={versionId ?? ""}
            onChange={(event) => setVersionId(event.target.value ? Number(event.target.value) : undefined)}
            className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">Active version</option>
            {models.map((model) => (
              <option key={model.model_version_id} value={model.model_version_id}>
                {model.model_name} v{model.version_no}
              </option>
            ))}
          </select>
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="flex items-center gap-1.5 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${scan.isPending ? "animate-spin" : ""}`} />
            {scan.isPending ? "Scanning…" : "Run drift scan"}
          </button>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {models.length === 0 && (
          <p className="text-sm text-slate-500">No trained model versions yet.</p>
        )}
        {models.map((model) => (
          <div key={model.model_version_id} className="rounded-md border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-900">{model.model_name}</span>
              {model.active && (
                <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] text-white">ACTIVE</span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              v{model.version_no} · {model.model_type}
            </p>
            <dl className="mt-3 space-y-1 text-xs text-slate-600">
              <div className="flex justify-between">
                <dt>Scored customers</dt>
                <dd className="font-mono">{model.n_predictions}</dd>
              </div>
              <div className="flex justify-between">
                <dt>Age</dt>
                <dd className="font-mono">
                  {model.days_since_trained == null ? "n/a" : `${model.days_since_trained}d`}
                </dd>
              </div>
            </dl>
            <div className="mt-3 flex items-center gap-2">
              {model.last_run ? (
                <>
                  <StatusPill status={model.last_run.drift_status ?? "UNKNOWN"} />
                  <span className="font-mono text-[11px] text-slate-500">
                    PSI {model.last_run.psi_overall == null ? "n/a" : model.last_run.psi_overall.toFixed(3)}
                  </span>
                </>
              ) : (
                <span className="text-[11px] text-slate-400">Never scanned</span>
              )}
            </div>
            {model.stale && (
              <p className="mt-2 flex items-center gap-1 text-[11px] text-amber-700">
                <AlertTriangle className="h-3 w-3" />
                Retraining due: older than the staleness limit.
              </p>
            )}
          </div>
        ))}
      </section>

      {drift.isError && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          Drift is not available yet. Train and score a model first — there is nothing to compare against.
        </div>
      )}

      {report && (
        <>
          <section className="grid gap-3 lg:grid-cols-3">
            <div className="rounded-md border border-slate-200 bg-white p-4 lg:col-span-1">
              <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
                <Activity className="h-3.5 w-3.5" />
                Feature drift
              </p>
              <div className="mt-2 flex items-center gap-2">
                <StatusPill status={report.summary.drift_status} />
              </div>
              <p className="mt-1 text-sm text-slate-700">{STATUS_LABEL[report.summary.drift_status]}</p>
              <dl className="mt-3 space-y-1 text-xs text-slate-600">
                <div className="flex justify-between">
                  <dt>Overall PSI</dt>
                  <dd className="font-mono">{num(report.summary.psi_overall, 3)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Features profiled</dt>
                  <dd className="font-mono">{report.summary.n_features_profiled}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Drifted</dt>
                  <dd className="font-mono">{report.summary.n_features_drifted}</dd>
                </div>
              </dl>
              <p className="mt-3 border-t border-slate-100 pt-2 text-[11px] leading-relaxed text-slate-500">
                Baseline: {report.baseline.note}. Moderate at PSI {report.thresholds.MODERATE}, significant at{" "}
                {report.thresholds.SIGNIFICANT}.
              </p>
            </div>

            <div className="rounded-md border border-slate-200 bg-white p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Prediction drift</p>
              <div className="mt-2 flex items-center gap-2">
                <StatusPill status={report.prediction.status} />
                <PsiBar psi={report.prediction.psi} status={report.prediction.status} />
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                {report.prediction.reason ?? "Compared against the first recorded scan."}
              </p>
              <dl className="mt-3 space-y-1 text-xs text-slate-600">
                <div className="flex justify-between">
                  <dt>Scored rows</dt>
                  <dd className="font-mono">{report.prediction.summary.n}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Mean probability</dt>
                  <dd className="font-mono">{num(report.prediction.summary.mean, 3)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Coverage</dt>
                  <dd className="font-mono">
                    {report.health.prediction_coverage == null
                      ? "n/a"
                      : `${(report.health.prediction_coverage * 100).toFixed(1)}%`}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="rounded-md border border-slate-200 bg-white p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Model health</p>
              <dl className="mt-2 space-y-1 text-xs text-slate-600">
                <div className="flex justify-between">
                  <dt>Version</dt>
                  <dd className="font-mono">
                    v{report.model_version.version_no}
                    {report.model_version.active ? " (active)" : ""}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt>Population</dt>
                  <dd className="font-mono">{report.health.n_population}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>Days since training</dt>
                  <dd className="font-mono">
                    {report.health.days_since_trained == null
                      ? "n/a"
                      : `${report.health.days_since_trained} / ${report.health.max_staleness_days}`}
                  </dd>
                </div>
              </dl>
              {report.summary.drifted_features.length > 0 && (
                <div className="mt-3 rounded bg-red-50 p-2">
                  <p className="text-[11px] font-medium text-red-700">
                    Investigate: {report.summary.drifted_features.join(", ")}
                  </p>
                </div>
              )}
            </div>
          </section>

          <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
            <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
              <h2 className="text-sm font-semibold text-slate-900">Per-feature drift</h2>
              <span className="text-xs text-slate-500">
                {drifted.length} of {report.features.length} need attention
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">Feature</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">PSI</th>
                    <th className="px-3 py-2 font-medium">KS</th>
                    <th className="px-3 py-2 text-right font-medium">Ref mean</th>
                    <th className="px-3 py-2 text-right font-medium">Cur mean</th>
                    <th className="px-3 py-2 text-right font-medium">Ref miss</th>
                    <th className="px-3 py-2 text-right font-medium">Cur miss</th>
                  </tr>
                </thead>
                <tbody>
                  {report.features.map((metric) => (
                    <FeatureRow key={metric.feature} metric={metric} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-3 py-2">
          <h2 className="text-sm font-semibold text-slate-900">Scan history</h2>
          <p className="text-xs text-slate-500">
            Recorded scans are what prediction drift is measured against. The first scan for a version becomes its
            baseline.
          </p>
        </div>
        {runs.isLoading ? (
          <p className="px-3 py-4 text-sm text-slate-500">Loading runs…</p>
        ) : (runs.data?.items.length ?? 0) === 0 ? (
          <p className="px-3 py-4 text-sm text-slate-500">No scans recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Drift</th>
                  <th className="px-3 py-2 font-medium">PSI</th>
                  <th className="px-3 py-2 font-medium">Prediction</th>
                  <th className="px-3 py-2 text-right font-medium">Rows</th>
                  <th className="px-3 py-2 font-medium">Recorded</th>
                </tr>
              </thead>
              <tbody>
                {runs.data?.items.map((run) => (
                  <tr key={run.id} className="border-t border-slate-100">
                    <td className="px-3 py-2 font-mono text-xs">#{run.id}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">{run.status}</td>
                    <td className="px-3 py-2">
                      {run.drift_status ? <StatusPill status={run.drift_status} /> : <span className="text-xs">n/a</span>}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{num(run.psi_overall, 3)}</td>
                    <td className="px-3 py-2 text-xs">
                      {run.prediction_drift_status ? (
                        <StatusPill status={run.prediction_drift_status} />
                      ) : (
                        <span className="text-slate-400">n/a</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">{run.n_current_rows ?? "n/a"}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {new Date(run.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
