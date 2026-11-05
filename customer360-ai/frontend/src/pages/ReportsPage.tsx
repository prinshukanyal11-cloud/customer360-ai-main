import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet, FileText, FileJson, Loader2 } from "lucide-react";
import { exportReport, fetchReportCatalog, type ReportCatalogItem } from "@/services/models";

const FORMAT_META: Record<string, { label: string; icon: typeof FileText }> = {
  csv: { label: "CSV", icon: FileText },
  xlsx: { label: "Excel", icon: FileSpreadsheet },
  json: { label: "JSON", icon: FileJson },
};

/** The few filters worth exposing as a control; everything else stays empty. */
const FILTER_CONTROLS: Record<string, { label: string; placeholder?: string; type?: string }> = {
  search: { label: "Search", placeholder: "name, email or external id" },
  min_risk_level: { label: "Minimum risk", type: "select", placeholder: "HIGH" },
  status: { label: "Status", type: "select", placeholder: "COMPLETED" },
  channel: { label: "Channel", type: "select", placeholder: "email" },
};

function saveDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function ReportCard({ item, formats }: { item: ReportCatalogItem; formats: string[] }) {
  const [format, setFormat] = useState(formats[0] ?? "csv");
  const [filters, setFilters] = useState<Record<string, string>>({});

  const download = useMutation({
    mutationFn: () =>
      exportReport(
        item.key,
        format,
        Object.fromEntries(
          Object.entries(filters).filter(([, value]) => value.trim() !== ""),
        ),
      ),
    onSuccess: (result) => saveDownload(result.blob, result.filename),
  });

  const controls = item.filters.filter((name) => name in FILTER_CONTROLS && name !== "limit");

  return (
    <div className="flex flex-col rounded-md border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">{item.label}</h2>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">{item.key}</span>
      </div>
      <p className="mt-1 flex-1 text-xs leading-relaxed text-slate-500">{item.description}</p>

      {controls.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {controls.map((name) => {
            const control = FILTER_CONTROLS[name];
            return (
              <label key={name} className="flex flex-col text-[11px] text-slate-600">
                {control.label}
                {control.type === "select" ? (
                  <select
                    value={filters[name] ?? ""}
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, [name]: event.target.value }))
                    }
                    className="mt-0.5 rounded border border-slate-300 bg-white px-1.5 py-1 text-xs"
                  >
                    <option value="">Any</option>
                    <option value={control.placeholder}>{control.placeholder}</option>
                  </select>
                ) : (
                  <input
                    value={filters[name] ?? ""}
                    placeholder={control.placeholder}
                    onChange={(event) =>
                      setFilters((current) => ({ ...current, [name]: event.target.value }))
                    }
                    className="mt-0.5 rounded border border-slate-300 px-1.5 py-1 text-xs"
                  />
                )}
              </label>
            );
          })}
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        <div className="flex overflow-hidden rounded border border-slate-300">
          {formats.map((option) => {
            const Icon = FORMAT_META[option]?.icon ?? FileText;
            return (
              <button
                key={option}
                onClick={() => setFormat(option)}
                title={FORMAT_META[option]?.label ?? option}
                className={`flex items-center gap-1 px-2 py-1.5 text-xs font-medium ${
                  format === option ? "bg-slate-900 text-white" : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {FORMAT_META[option]?.label ?? option}
              </button>
            );
          })}
        </div>
        <button
          onClick={() => download.mutate()}
          disabled={download.isPending}
          className="ml-auto flex items-center gap-1.5 rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {download.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          {download.isPending ? "Preparing…" : "Download"}
        </button>
      </div>

      {download.isSuccess && (
        <p className="mt-2 text-[11px] text-emerald-700">
          Saved {download.data.filename} · {download.data.rowCount} rows
          {download.data.truncated ? " (truncated at the row cap)" : ""}.
        </p>
      )}
      {download.isError && (
        <p className="mt-2 text-[11px] text-red-600">
          {(download.error as Error)?.message ?? "Export failed."}
        </p>
      )}
    </div>
  );
}

export function ReportsPage() {
  const catalog = useQuery({ queryKey: ["reports", "catalog"], queryFn: fetchReportCatalog });
  const data = catalog.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
          <Download className="h-5 w-5" />
          Reports & Exports
        </h1>
        <p className="text-sm text-slate-500">
          Download your own organisation&rsquo;s data as CSV, Excel, or JSON. Every export is logged.
        </p>
      </header>

      {catalog.isLoading && <p className="text-sm text-slate-500">Loading reports…</p>}

      {catalog.isError && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          Could not load the report catalogue.
        </div>
      )}

      {data && (
        <>
          <p className="text-xs text-slate-500">
            Exports are capped at {data.max_rows.toLocaleString()} rows.
          </p>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {data.items.map((item) => (
              <ReportCard key={item.key} item={item} formats={data.formats} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}
