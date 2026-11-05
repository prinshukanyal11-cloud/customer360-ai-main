import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CHURN_FAMILY_LABELS,
  activateVersion,
  fetchAtRisk,
  fetchModels,
  scoreChurn,
  trainChurn,
  type ChurnTrainResult,
  type ModelOut,
} from "@/services/models";

const FAMILIES = Object.keys(CHURN_FAMILY_LABELS);

const formatPct = (value: number | null | undefined): string =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const RISK_BADGE: Record<string, string> = {
  LOW: "bg-emerald-50 text-emerald-700",
  MEDIUM: "bg-yellow-50 text-yellow-700",
  HIGH: "bg-orange-50 text-orange-700",
  CRITICAL: "bg-red-50 text-red-700",
};

function ConfusionGrid({
  matrix,
}: {
  matrix: { tn: number; fp: number; fn: number; tp: number } | undefined;
}) {
  if (!matrix) return null;
  return (
    <div className="mt-2 grid grid-cols-2 gap-1 text-center text-xs">
      <div className="rounded bg-emerald-50 px-2 py-1 text-emerald-700">
        <span className="block font-medium">{matrix.tn}</span>
        <span className="text-[10px] uppercase text-emerald-500">true neg</span>
      </div>
      <div className="rounded bg-red-50 px-2 py-1 text-red-700">
        <span className="block font-medium">{matrix.fp}</span>
        <span className="text-[10px] uppercase text-red-500">false pos</span>
      </div>
      <div className="rounded bg-red-50 px-2 py-1 text-red-700">
        <span className="block font-medium">{matrix.fn}</span>
        <span className="text-[10px] uppercase text-red-500">false neg</span>
      </div>
      <div className="rounded bg-emerald-50 px-2 py-1 text-emerald-700">
        <span className="block font-medium">{matrix.tp}</span>
        <span className="text-[10px] uppercase text-emerald-500">true pos</span>
      </div>
    </div>
  );
}

function VersionCard({ model, version }: { model: ModelOut; version: ModelOut["versions"][number] }) {
  const queryClient = useQueryClient();
  const activate = useMutation({
    mutationFn: () => activateVersion(model.id, version.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["models"] }),
  });

  const metricEntries = version.metrics ? Object.entries(version.metrics).filter(([k]) => k.length > 0) : [];

  return (
    <div
      className={`rounded-md border p-4 ${version.active ? "border-slate-900 bg-slate-50" : "border-slate-200 bg-white"}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-900">v{version.version_no}</span>
          {version.family && (
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {CHURN_FAMILY_LABELS[version.family] ?? version.family}
            </span>
          )}
        </div>
        {version.active ? (
          <span className="rounded bg-slate-900 px-2 py-0.5 text-xs text-white">ACTIVE</span>
        ) : (
          <button
            onClick={() => activate.mutate()}
            disabled={activate.isPending}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          >
            {activate.isPending ? "Activating…" : "Activate"}
          </button>
        )}
      </div>
      {version.metrics && (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-1 text-xs">
            {metricEntries.map(([k, val]) => {
              if (typeof val !== "number") return null;
              const isPct = k === "accuracy" || k === "precision" || k === "recall" || k === "f1";
              return (
                <div key={k} className="flex justify-between">
                  <dt className="text-slate-500">{k.replace(/_/g, " ")}</dt>
                  <dd className="font-medium tabular-nums text-slate-900">
                    {isPct ? formatPct(val) : val.toFixed(3)}
                  </dd>
                </div>
              );
            })}
          </dl>
          <ConfusionGrid matrix={version.metrics.confusion_matrix as { tn: number; fp: number; fn: number; tp: number } | undefined} />
        </>
      )}
      {!version.metrics && (
        <p className="mt-2 text-xs text-slate-500">Trained {version.trained_at ?? "—"}</p>
      )}
    </div>
  );
}

export function ModelManagementPage() {
  const queryClient = useQueryClient();
  const [selectedFamilies, setSelectedFamilies] = useState<string[]>(FAMILIES);
  const [churnWindow, setChurnWindow] = useState("");
  const [randomSeed, setRandomSeed] = useState("");
  const [lastTrained, setLastTrained] = useState<ChurnTrainResult | null>(null);
  const [minRisk, setMinRisk] = useState("HIGH");

  const models = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
  });
  const atRisk = useQuery({
    queryKey: ["at-risk", minRisk],
    queryFn: () => fetchAtRisk(minRisk),
    enabled: Boolean((models.data?.items ?? []).some((m) => m.model_type === "churn")),
  });

  const train = useMutation({
    mutationFn: () =>
      trainChurn({
        families: selectedFamilies,
        churn_window_days: churnWindow ? Number(churnWindow) : undefined,
        random_seed: randomSeed ? Number(randomSeed) : undefined,
      }),
    onSuccess: (result) => {
      setLastTrained(result);
      setChurnWindow("");
      setRandomSeed("");
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["at-risk"] });
    },
  });

  const score = useMutation({
    mutationFn: () => scoreChurn(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["at-risk"] });
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
  });

  const toggleFamily = (family: string) => {
    setSelectedFamilies((prev) =>
      prev.includes(family) ? prev.filter((f) => f !== family) : [...prev, family],
    );
  };

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Model Management</h1>
        <p className="mt-1 text-sm text-slate-500">
          Train a churn prediction model across algorithm families, compare versions, and activate the best performer.
        </p>
      </header>

      <section className="rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="text-sm font-medium text-slate-500">Train churn model</h2>
        <div className="mt-3">
          <p className="text-xs font-medium text-slate-500">Families</p>
          <div className="mt-1 flex flex-wrap gap-2">
            {FAMILIES.map((family) => (
              <label
                key={family}
                className="flex cursor-pointer items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
              >
                <input
                  type="checkbox"
                  checked={selectedFamilies.includes(family)}
                  onChange={() => toggleFamily(family)}
                  className="h-4 w-4"
                />
                {CHURN_FAMILY_LABELS[family]}
              </label>
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-slate-500">Churn window (days, default 90)</span>
            <input
              type="number"
              min={1}
              max={365}
              value={churnWindow}
              onChange={(e) => setChurnWindow(e.target.value)}
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
          <button
            onClick={() => train.mutate()}
            disabled={selectedFamilies.length === 0 || train.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {train.isPending ? "Training…" : "Train churn model"}
          </button>
        </div>
        {train.isError && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {train.error instanceof Error ? train.error.message : "Training failed."}
          </p>
        )}
        {lastTrained && !train.isError && (
          <p className="mt-3 text-sm text-slate-600">
            Trained v{lastTrained.champion.version_no} champion ({lastTrained.champion.family}, n={lastTrained.n_samples},
            window={lastTrained.window_days}d, ROC-AUC {formatPct(
              (lastTrained.champion.metrics.roc_auc as number) ?? null,
            )})
          </p>
        )}
      </section>

      {score.isError && (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {score.error instanceof Error ? score.error.message : "Scoring failed."}
        </p>
      )}

      {models.isLoading && <div className="mt-8 text-sm text-slate-500">Loading models…</div>}
      {models.isError && (
        <div role="alert" className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {models.error instanceof Error ? models.error.message : "Failed to load models."}
        </div>
      )}
      {!models.isLoading && models.data && models.data.items.length === 0 && (
        <div className="mt-8 rounded-lg border border-dashed bg-white p-8 text-center">
          <h2 className="font-medium text-slate-700">No models registered</h2>
          <p className="mt-1 text-sm text-slate-500">
            Train a churn model above after uploading and processing a dataset to unlock predictions.
          </p>
        </div>
      )}

      {!models.isLoading && models.data && models.data.items.length > 0 && (
        <div className="mt-8 space-y-4">
          {models.data.items.map((model) => (
            <div key={model.id} className="rounded-lg border bg-white p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="font-semibold text-slate-900">{model.name}</h2>
                  <p className="text-xs capitalize text-slate-500">
                    {model.model_type.replace(/_/g, " ")} · {model.family ?? "unknown family"} · {model.status}
                  </p>
                </div>
              </div>
              {model.versions.length === 0 ? (
                <p className="mt-4 text-sm text-slate-500">No versions yet.</p>
              ) : (
                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {model.versions.map((v) => (
                    <VersionCard key={v.id} model={model} version={v} />
                  ))}
                </div>
              )}
              {model.model_type === "churn" && (
                <div className="mt-4 flex items-center gap-3 border-t pt-4">
                  <button
                    onClick={() => score.mutate()}
                    disabled={score.isPending}
                    className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    {score.isPending ? "Scoring customers…" : "Score all customers"}
                  </button>
                  <label className="flex items-center gap-1.5 text-sm text-slate-600">
                    <span className="text-xs text-slate-500">Min risk</span>
                    <select
                      value={minRisk}
                      onChange={(e) => setMinRisk(e.target.value)}
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
              )}
            </div>
          ))}
        </div>
      )}

      {atRisk.data && atRisk.data.total > 0 && (
        <section className="mt-8 rounded-lg border bg-white p-6 shadow-sm">
          <h2 className="font-medium text-slate-900">
            At-risk customers <span className="text-sm font-normal text-slate-500">({atRisk.data.total} scored)</span>
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-slate-400">
                  <th className="py-1 pr-3 font-medium">Customer</th>
                  <th className="py-1 pr-3 font-medium">Probability</th>
                  <th className="py-1 font-medium">Risk</th>
                </tr>
              </thead>
              <tbody>
                {atRisk.data.items.map((item) => (
                  <tr key={item.prediction_id} className="border-b border-slate-100 last:border-0">
                    <td className="py-1.5 pr-3 text-slate-700">{item.name ?? "—"}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-slate-600">{formatPct(item.probability)}</td>
                    <td className="py-1.5">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${RISK_BADGE[item.risk_level] ?? ""}`}>
                        {item.risk_level}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}