import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import client from "@/services/api";

interface CustomerRow {
  id: number;
  external_customer_id: string;
  name: string | null;
  email: string | null;
  location: string | null;
  total_orders: number;
  total_revenue: number;
  last_purchase: string | null;
  rfm_score: string | null;
  rfm_segment: string | null;
  clv_est: number | null;
}

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

interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export function CustomersPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["customers", page, query],
    queryFn: async () => {
      const res = await client.get<Page<CustomerRow>>("/customers", {
        params: { page, page_size: 20, search: query ?? undefined },
      });
      return res.data;
    },
  });

  return (
    <div className="p-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Customers</h1>
          <p className="mt-1 text-sm text-slate-500">Server-side pagination — data is fetched from the backend.</p>
        </div>
      </header>

      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setPage(1);
          setQuery(search.trim() || null);
        }}
      >
        <input
          aria-label="Search customers"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by ID, name, email, or location…"
          className="w-80 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
        />
        <button className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800">
          Search
        </button>
      </form>

      {isError && (
        <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {error instanceof Error ? error.message : "Failed to load customers."}
        </div>
      )}
      {isLoading && <div className="text-sm text-slate-500">Loading customers…</div>}

      {!isLoading && data && data.items.length === 0 && (
        <div className="rounded-lg border border-dashed bg-white p-8 text-center">
          <p className="text-sm text-slate-500">
            {query
              ? "No customers match your search."
              : "No customers yet. Upload a dataset in the Data section first."}
          </p>
        </div>
      )}

      {!isLoading && data && data.items.length > 0 && (
        <>
          <div className="overflow-hidden rounded-lg border bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-3">Customer</th>
                  <th className="px-4 py-3">Location</th>
                  <th className="px-4 py-3">Orders</th>
                  <th className="px-4 py-3">Revenue</th>
                  <th className="px-4 py-3">RFM segment</th>
                  <th className="px-4 py-3">CLV estimate</th>
                  <th className="px-4 py-3">Last purchase</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.items.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link to={`/customers/${c.id}`} className="font-medium text-slate-900 hover:underline">
                        {c.name ?? c.external_customer_id}
                      </Link>
                      <div className="text-xs text-slate-400">{c.email ?? c.external_customer_id}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{c.location ?? "—"}</td>
                    <td className="px-4 py-3 text-slate-600">{c.total_orders}</td>
                    <td className="px-4 py-3 text-slate-600">₹{c.total_revenue.toLocaleString("en-IN")}</td>
                    <td className="px-4 py-3">
                      {c.rfm_segment ? (
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium normal-case ${SEGMENT_STYLE[c.rfm_segment] ?? "bg-slate-100 text-slate-600"}`}
                        >
                          {c.rfm_segment.replace(/_/g, " ")}
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                      {c.rfm_score && <span className="ml-1.5 text-xs tabular-nums text-slate-400">{c.rfm_score}</span>}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {c.clv_est != null ? `₹${c.clv_est.toLocaleString("en-IN")}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{c.last_purchase ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
            <span>
              Page {data.page} of {data.pages} · {data.total} customers
            </span>
            <div className="flex gap-2">
              <button
                disabled={data.page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-md border px-3 py-1.5 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                disabled={data.page >= data.pages}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-md border px-3 py-1.5 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}