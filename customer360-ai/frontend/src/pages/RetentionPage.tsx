import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Wand2 } from "lucide-react";
import {
  RECOMMENDATION_ACTION_LABELS,
  fetchRecommendations,
  generateRecommendations,
  type RecommendationItem,
} from "@/services/models";
import { fetchAtRisk } from "@/services/models";

const PRIORITY_BADGE: Record<string, string> = {
  HIGH: "bg-red-50 text-red-700",
  MEDIUM: "bg-yellow-50 text-yellow-700",
  LOW: "bg-emerald-50 text-emerald-700",
};

export function RetentionPage() {
  const queryClient = useQueryClient();
  const [minPriority, setMinPriority] = useState("LOW");

  const recommendations = useQuery({
    queryKey: ["recommendations", minPriority],
    queryFn: () => fetchRecommendations({ min_priority: minPriority }),
  });

  const atRisk = useQuery({
    queryKey: ["at-risk-count"],
    queryFn: () => fetchAtRisk("HIGH"),
  });

  const generate = useMutation({
    mutationFn: () => generateRecommendations({}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recommendations"] });
    },
  });

  return (
    <div className="p-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Retention Recommendations</h1>
          <p className="mt-1 text-sm text-slate-500">
            Rule-based next-best-action suggestions on top of the churn model: priority score, driver, and expected
            objective for every at-risk account.
          </p>
        </div>
        <button
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          <Wand2 className="h-4 w-4" />
          {generate.isPending ? "Generating…" : "Generate recommendations"}
        </button>
      </header>

      {generate.isError && (
        <p role="alert" className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {generate.error instanceof Error ? generate.error.message : "Generation failed."}
        </p>
      )}

      <section className="rounded-lg border bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-medium text-slate-500">At-risk customers (high risk)</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
              {atRisk.data?.total ?? "—"}
            </p>
          </div>
          <p className="text-xs text-slate-400">
            Priority = churn probability × action weight + CLV influence. Generate refreshes all recommendations.
          </p>
        </div>
      </section>

      {recommendations.isLoading && <div className="mt-8 text-sm text-slate-500">Loading recommendations…</div>}
      {recommendations.isError && (
        <div role="alert" className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {recommendations.error instanceof Error
            ? recommendations.error.message
            : "Failed to load recommendations."}
        </div>
      )}
      {!recommendations.isLoading && !recommendations.isError && recommendations.data?.total === 0 && (
        <div className="mt-8 rounded-lg border border-dashed bg-white p-8 text-center">
          <h2 className="font-medium text-slate-700">No recommendations yet</h2>
          <p className="mt-1 text-sm text-slate-500">
            Train, score, then generate recommendations to populate the retention playbook.
          </p>
          <button
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            className="mt-3 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            Generate now
          </button>
        </div>
      )}
      {recommendations.data && recommendations.data.total > 0 && (
        <section className="mt-6 rounded-lg border bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3">
            <h2 className="font-medium text-slate-900">
              Next best actions
              <span className="ml-2 text-sm font-normal text-slate-500">
                ({recommendations.data.items.length} shown of {recommendations.data.total})
              </span>
            </h2>
            <label className="flex items-center gap-1.5 text-sm text-slate-600">
              <span className="text-xs text-slate-500">Min priority</span>
              <select
                value={minPriority}
                onChange={(e) => setMinPriority(e.target.value)}
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {["LOW", "MEDIUM", "HIGH"].map((level) => (
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
                  <th className="py-2 pr-3 font-medium">Priority</th>
                  <th className="py-2 pr-3 font-medium">Suggested action</th>
                  <th className="py-2 pr-3 text-right font-medium">Score</th>
                  <th className="py-2 pr-3 font-medium">Confidence</th>
                  <th className="py-2 pr-5 font-medium">Why</th>
                </tr>
              </thead>
              <tbody>
                {recommendations.data.items.map((item) => (
                  <Row key={item.id} item={item} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Row({ item }: { item: RecommendationItem }) {
  const [showRules, setShowRules] = useState(false);
  return (
    <>
      <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
        <td className="px-5 py-2.5">
          <Link
            to={`/customers/${item.customer_id}`}
            className="font-medium text-slate-900 hover:underline"
          >
            {item.name}
          </Link>
        </td>
        <td className="py-2.5 pr-3">
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${PRIORITY_BADGE[item.priority] ?? ""}`}>
            {item.priority}
          </span>
        </td>
        <td className="py-2.5 pr-3 text-slate-700">
          {RECOMMENDATION_ACTION_LABELS[item.action] ?? item.action}
        </td>
        <td className="py-2.5 pr-3 text-right tabular-nums text-slate-700">
          {item.priority_score.toFixed(0)}
        </td>
        <td className="py-2.5 pr-3 text-slate-600">
          {(item.confidence * 100).toFixed(0)}%
        </td>
        <td className="py-2.5 pr-5">
          <button
            onClick={() => setShowRules((v) => !v)}
            className="max-w-md truncate text-left text-xs text-slate-500 hover:text-slate-900"
            title={item.reason}
          >
            {item.reason}
          </button>
        </td>
      </tr>
      {showRules && (
        <tr className="border-b border-slate-100 bg-slate-50 last:border-0">
          <td colSpan={6} className="px-5 py-2.5">
            <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
              <span>
                <span className="font-medium text-slate-900">Objective:</span>{" "}
                {item.expected_objective ?? "Retain the customer"}
              </span>
              <span>
                <span className="font-medium text-slate-900">Rules:</span>{" "}
                {item.rules.join(", ") || "—"}
              </span>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}