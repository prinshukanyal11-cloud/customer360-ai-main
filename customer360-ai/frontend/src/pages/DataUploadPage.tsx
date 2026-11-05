import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authService } from "@/services/auth";
import { datasetsService } from "@/services/datasets";
import type {
  DqReport,
  Mapping,
  ProcessSummary,
  ValidateResult,
} from "@/types";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function labeled(severity: string) {
  return severity === "ERROR"
    ? "text-red-700 bg-red-50"
    : severity === "WARN"
      ? "text-amber-700 bg-amber-50"
      : "text-slate-600 bg-slate-100";
}

interface FieldSpecUi {
  key: string;
  label: string;
  required?: boolean;
  help?: string;
}

const ENTITY_FIELDS: Record<string, FieldSpecUi[]> = {
  customers: [
    { key: "external_customer_id", label: "Customer ID", required: true, help: "Unique customer identifier" },
    { key: "name", label: "Full name" },
    { key: "email", label: "Email" },
    { key: "phone", label: "Phone" },
    { key: "location", label: "City / location" },
    { key: "age", label: "Age" },
    { key: "gender", label: "Gender" },
    { key: "signup_date", label: "Sign-up date" },
    { key: "acquisition_channel", label: "Acquisition channel" },
  ],
  products: [
    { key: "external_product_id", label: "Product ID", help: "Defaults to product name when empty" },
    { key: "product_name", label: "Product name", required: true },
    { key: "category", label: "Category" },
    { key: "subcategory", label: "Subcategory" },
    { key: "unit_price", label: "Unit price" },
  ],
  transactions: [
    { key: "external_order_id", label: "Order / transaction ID", required: true },
    { key: "customer_id", label: "Customer ID", required: true },
    { key: "product_id", label: "Product ID (in transaction)" },
    { key: "order_date", label: "Order date", required: true },
    { key: "quantity", label: "Quantity" },
    { key: "amount", label: "Amount", required: true },
    { key: "discount", label: "Discount" },
    { key: "currency", label: "Currency" },
    { key: "channel", label: "Channel" },
  ],
};

const STATUS_ORDER = ["UPLOADED", "MAPPED", "VALIDATED", "PROCESSED", "FAILED"];
const STEPS = ["Upload", "Map columns", "Validate", "Process"];

const ENTITY_LABEL: Record<string, string> = {
  customers: "Customers",
  products: "Products",
  transactions: "Transactions",
};

function statusIndex(status: string): number {
  const i = STATUS_ORDER.indexOf(status);
  return i === -1 ? 0 : i;
}

export function DataUploadPage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [lastValidate, setLastValidate] = useState<ValidateResult | null>(null);
  const [lastProcess, setLastProcess] = useState<ProcessSummary | null>(null);

  const me = useQuery({ queryKey: ["me"], queryFn: authService.me });
  const role = me.data?.role ?? "VIEWER";
  const canMutate = role === "ADMIN" || role === "ANALYST";

  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: () => datasetsService.listDatasets(),
  });

  const detail = useQuery({
    queryKey: ["dataset", activeId],
    queryFn: () => datasetsService.getDataset(activeId as number),
    enabled: activeId !== null,
  });

  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [dirty, setDirty] = useState(false);
  const seededFor = useRef<number | null>(null);
  const d = detail.data;
  if (d && !mapping && seededFor.current !== d.id) {
    seededFor.current = d.id;
    setMapping(d.mapping ? clone(d.mapping) : {});
    setDirty(false);
    setError(null);
    setLastValidate(null);
    setLastProcess(null);
  }

  const latestScore: number | null =
    lastValidate?.score ?? (d ? (d.versions[d.versions.length - 1]?.dq_score ?? null) : null);

  const upload = useMutation({
    mutationFn: (f: File) => datasetsService.uploadDataset(f),
    onSuccess: (res) => {
      setMapping(res.mapping_suggestion ? clone(res.mapping_suggestion) : {});
      setActiveId(res.dataset_id);
      setFile(null);
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (err: Error) => setError(err.message ?? "Upload failed."),
  });

  const saveMapping = useMutation({
    mutationFn: (m: Mapping) => datasetsService.mapColumns(activeId as number, m),
    onSuccess: (res) => {
      setMapping(res.mapping);
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["dataset", activeId] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (err: Error) => setError(err.message ?? "Could not save mapping."),
  });

  const validate = useMutation({
    mutationFn: () => datasetsService.validateDataset(activeId as number),
    onSuccess: (res) => {
      setLastValidate(res);
      setLastProcess(null);
      queryClient.invalidateQueries({ queryKey: ["dataset", activeId] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (err: Error) => setError(err.message ?? "Validation failed."),
  });

  const process = useMutation({
    mutationFn: (force: boolean) => datasetsService.processDataset(activeId as number, force),
    onSuccess: (res) => {
      setLastProcess(res);
      queryClient.invalidateQueries({ queryKey: ["dataset", activeId] });
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (err: Error) => setError(err.message ?? "Processing failed."),
  });

  const columns = d?.columns ?? [];
  const status = d?.status ?? "UPLOADED";

  const setField = (entity: string, field: string, column: string) => {
    setMapping((prev) => {
      const next: Mapping = prev ? clone(prev) : {};
      if (column) {
        next[entity] = { ...(next[entity] ?? {}), [field]: column };
      } else if (next[entity]) {
        const copy = { ...next[entity] };
        delete copy[field];
        next[entity] = copy;
      }
      return next;
    });
    setDirty(true);
    setError(null);
  };

  const pickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    setLastProcess(null);
    setFile(e.target.files?.[0] ?? null);
  };

  const stepFor = (s: string) => {
    const i = statusIndex(s);
    return i >= 3 ? 4 : i + 1;
  };

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Data Ingestion</h1>
        <p className="mt-1 text-sm text-slate-500">
          Upload a CSV, map columns to the canonical schema, run data-quality checks, and load cleansed rows into the
          warehouse.
        </p>
      </header>

      {!canMutate && (
        <div className="mb-6 rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-700">
          Your role ({role}) is read-only for datasets. Ask an admin or analyst to upload, map and process data.
        </div>
      )}

      {/* Step indicator for the active dataset */}
      {activeId !== null && d && (
        <div className="mb-6 flex items-center gap-2">
          {STEPS.map((label, i) => {
            const current = stepFor(status);
            const done = i < current;
            const active = i === current - 1;
            return (
              <div key={label} className="flex items-center gap-2">
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${
                    done ? "bg-emerald-600 text-white" : active ? "bg-slate-900 text-white" : "bg-slate-200 text-slate-500"
                  }`}
                >
                  {i + 1}
                </span>
                <span className={`text-sm ${active ? "font-medium text-slate-900" : "text-slate-500"}`}>{label}</span>
                {i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-slate-300" />}
              </div>
            );
          })}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        {/* Left: list of uploaded datasets */}
        <section className="rounded-lg border bg-white p-6 xl:col-span-1">
          <h2 className="text-sm font-medium text-slate-500">Your datasets</h2>
          {datasets.isLoading && <p className="mt-3 text-sm text-slate-400">Loading…</p>}
          {datasets.data && datasets.data.items.length === 0 && (
            <p className="mt-3 text-sm text-slate-400">Nothing uploaded yet.</p>
          )}
          <ul className="mt-3 space-y-2">
            {datasets.data?.items.map((ds) => (
              <li key={ds.id}>
                <button
                  onClick={() => setActiveId(ds.id)}
                  className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
                    activeId === ds.id ? "border-slate-900 bg-slate-50" : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="truncate text-sm font-medium text-slate-900">{ds.name}</span>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                        ds.status === "PROCESSED"
                          ? "bg-emerald-50 text-emerald-700"
                          : ds.status === "FAILED"
                            ? "bg-red-50 text-red-700"
                            : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {ds.status}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {ds.total_rows.toLocaleString()} rows · v{ds.version_no} ·{" "}
                    {ds.dq_score !== null ? `DQ ${ds.dq_score.toFixed(0)}` : "not yet processed"}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* Right: workflow */}
        <section className="space-y-6 xl:col-span-2">
          {/* Step 1: upload */}
          {!canMutate && activeId === null ? (
            <div className="rounded-lg border bg-white p-6 text-sm text-slate-500">
              No dataset selected. Uploading is disabled for your role.
            </div>
          ) : (
            <div className="rounded-lg border bg-white p-6">
              <h2 className="text-sm font-medium text-slate-500">1 · Upload CSV</h2>
              <div className="mt-3 flex items-center gap-3">
                <input id="csv" type="file" accept=".csv" onChange={pickFile} className="hidden" />
                <label
                  htmlFor="csv"
                  className="flex-1 cursor-pointer rounded-md border border-dashed border-slate-300 px-4 py-3 text-sm text-slate-500 hover:border-slate-400"
                >
                  {file ? file.name : "Click to choose a CSV file"}
                </label>
                <button
                  onClick={() => file && upload.mutate(file)}
                  disabled={!file || upload.isPending || !canMutate}
                  className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  {upload.isPending ? "Inspect…" : "Upload & inspect"}
                </button>
              </div>
              {activeId !== null && detail.data && (
                <p className="mt-3 text-xs text-slate-500">
                  Active dataset: <span className="font-medium text-slate-700">{detail.data.name}</span> ({status})
                </p>
              )}
            </div>
          )}

          {error && (
            <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {/* Summary of the active dataset */}
          {activeId !== null && (
            <>
              {d && (
                <div className="rounded-lg border bg-white p-6">
                  <h2 className="text-sm font-medium text-slate-500">Dataset summary</h2>
                  <dl className="mt-3 grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
                    <div>
                      <dt className="text-slate-500">Name</dt>
                      <dd className="font-medium text-slate-900">{d.name}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Rows</dt>
                      <dd className="font-medium text-slate-900">{d.total_rows.toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Version</dt>
                      <dd className="font-medium text-slate-900">{d.versions[d.versions.length - 1]?.version_no}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">DQ score</dt>
                      <dd className="font-medium text-slate-900">{latestScore !== null ? latestScore.toFixed(0) : "—"}</dd>
                    </div>
                  </dl>
                </div>
              )}

              {/* Step 2: mapping editor */}
              {mapping && canMutate && (
                <div className="rounded-lg border bg-white p-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-medium text-slate-500">2 · Map columns</h2>
                    <button
                      onClick={() => saveMapping.mutate(mapping)}
                      disabled={!dirty || saveMapping.isPending}
                      className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                    >
                      {saveMapping.isPending ? "Saving…" : dirty ? "Save mapping" : "Mapping saved"}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Fields marked <span className="text-red-600">*</span> are required before that entity can be stored.
                  </p>
                  <div className="mt-4 space-y-6">
                    {Object.entries(ENTITY_FIELDS).map(([entity, fields]) => {
                      const entityMap = mapping[entity] ?? {};
                      const mappedCount = Object.keys(entityMap).length;
                      if (mappedCount === 0) {
                        return (
                          <details key={entity} className="text-sm">
                            <summary className="cursor-pointer font-medium text-slate-700">
                              {ENTITY_LABEL[entity]}{" "}
                              <span className="text-xs font-normal text-slate-400">(not mapped)</span>
                            </summary>
                            <div className="mt-2 grid gap-2 md:grid-cols-2">
                              {fields.map((f) => (
                                <label key={f.key} className="flex items-center gap-2 text-xs">
                                  <span className="w-40 text-slate-600">{f.label}</span>
                                  <select
                                    value={entityMap[f.key] ?? ""}
                                    onChange={(e) => setField(entity, f.key, e.target.value)}
                                    className="flex-1 rounded border border-slate-200 px-2 py-1"
                                  >
                                    <option value="">— unmapped —</option>
                                    {columns.map((c) => (
                                      <option key={c} value={c}>
                                        {c}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              ))}
                            </div>
                          </details>
                        );
                      }
                      return (
                        <div key={entity}>
                          <h3 className="text-sm font-semibold text-slate-700">
                            {ENTITY_LABEL[entity]}{" "}
                            <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                              {Object.keys(fields).length} fields
                            </span>
                          </h3>
                          <div className="mt-2 grid gap-2 md:grid-cols-2">
                            {fields.map((f) => (
                              <label key={f.key} className="flex items-center gap-2 text-xs" title={f.help}>
                                <span className="w-44 text-slate-600">
                                  {f.label}
                                  {f.required && <span className="text-red-600"> *</span>}
                                </span>
                                <select
                                  value={entityMap[f.key] ?? ""}
                                  onChange={(e) => setField(entity, f.key, e.target.value)}
                                  className="flex-1 rounded border border-slate-200 px-2 py-1"
                                >
                                  <option value="">—</option>
                                  {columns.map((c) => (
                                    <option key={c} value={c}>
                                      {c}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Step 3: validate */}
              {canMutate && (
                <div className="rounded-lg border bg-white p-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-medium text-slate-500">3 · Validate &amp; data quality</h2>
                    <button
                      onClick={() => validate.mutate()}
                      disabled={validate.isPending || status === "PROCESSED"}
                      className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                    >
                      {validate.isPending ? "Running checks…" : "Run quality checks"}
                    </button>
                  </div>
                  <DqPanel report={lastValidate?.report ?? null} />
                </div>
              )}

              {/* Step 4: process */}
              {canMutate && (
                <div className="rounded-lg border bg-white p-6">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-medium text-slate-500">4 · Load into warehouse</h2>
                    <div className="flex gap-2">
                      {lastValidate?.blocked && (
                        <button
                          onClick={() => process.mutate(true)}
                          disabled={process.isPending}
                          className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
                        >
                          {process.isPending ? "Loading…" : "Force process"}
                        </button>
                      )}
                      <button
                        onClick={() => process.mutate(false)}
                        disabled={process.isPending || lastValidate?.blocked === true}
                        className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                      >
                        {process.isPending ? "Processing…" : status === "PROCESSED" ? "Process again" : "Process"}
                      </button>
                    </div>
                  </div>

                  {lastProcess && (
                    <div className="mt-4 rounded-md bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                      <span className="font-semibold">Stored.</span> {lastProcess.rows.toLocaleString()} rows in{" "}
                      {lastProcess.entities_processed.join(", ")} —{" "}
                      {Object.entries(lastProcess.inserted)
                        .filter(([, n]) => n > 0)
                        .map(([e, n]) => `${n.toLocaleString()} ${e}`)
                        .join(", ")}
                    </div>
                  )}
                </div>
              )}

              {!canMutate && (
                <div className="rounded-lg border bg-white p-6 text-sm text-slate-500">
                  Mapping, validation and processing require the ADMIN or ANALYST role.
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function DqPanel({ report }: { report: DqReport | null }) {
  if (!report) {
    return <p className="mt-3 text-sm text-slate-400">No checks run yet.</p>;
  }
  const hasError = report.blocked || report.checks.some((c) => c.severity === "ERROR" && c.status === "FAIL");
  const hasWarn = report.checks.some((c) => c.status === "WARN");
  const severity = hasError ? "ERROR" : hasWarn ? "WARN" : "INFO";
  const tone =
    severity === "ERROR"
      ? "bg-red-50 text-red-700"
      : severity === "WARN"
        ? "bg-amber-50 text-amber-700"
        : "bg-emerald-50 text-emerald-700";
  return (
    <div className="mt-3">
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-3 py-1 text-sm font-semibold ${tone}`}>
          Score: {report.score.toFixed(1)}{report.blocked ? " · blocked" : ""}
        </span>
        <span className="text-xs text-slate-500">
          {report.checks.length} checks · {report.checks.filter((c) => c.status === "FAIL").length} failed ·{" "}
          {report.checks.filter((c) => c.status === "WARN").length} warning
        </span>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="pb-2">Check</th>
              <th className="pb-2">Severity</th>
              <th className="pb-2">Affected</th>
              <th className="pb-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {report.checks.map((c) => (
              <tr key={c.check}>
                <td className="py-2 text-slate-800">
                  {c.label}{" "}
                  {c.samples && c.samples.length > 0 && (
                    <span className="text-xs text-slate-400">e.g. {String((c.samples as unknown[])[0])}</span>
                  )}
                </td>
                <td className="py-2">
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${labeled(c.severity)}`}>
                    {c.severity}
                  </span>
                </td>
                <td className="py-2 text-slate-600">
                  {c.affected}/{c.total}
                </td>
                <td className="py-2">
                  <span
                    className={`font-semibold ${
                      c.status === "PASS" ? "text-emerald-600" : c.status === "WARN" ? "text-amber-600" : "text-red-600"
                    }`}
                  >
                    {c.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}