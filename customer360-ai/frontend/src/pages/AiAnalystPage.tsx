import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Database, Sparkles } from "lucide-react";
import {
  askAiAnalyst,
  runNlToSql,
  type AiAnswerResult,
  type AiHistoryTurn,
  type NlToSqlResult,
} from "@/services/models";

const ANALYST_EXAMPLES = [
  "Which customers should we prioritize?",
  "How much revenue is at risk right now?",
  "What is our repeat purchase rate?",
  "How many customers are dormant?",
];

const SQL_EXAMPLES = [
  "How many customers do we have?",
  "Which customers are at risk of churning?",
  "How has revenue trended month over month?",
  "Which product categories perform best?",
  "What are the most recent orders?",
];

interface Turn extends AiHistoryTurn {
  id: number;
}

export function AiAnalystPage() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);

  const analyst = useMutation({
    mutationFn: (q: string) =>
      askAiAnalyst(
        q,
        turns.map(({ question: prevQuestion, answer }) => ({ question: prevQuestion, answer })),
      ),
    onSuccess: (data: AiAnswerResult, q) => {
      setTurns((prev) => [...prev, { id: prev.length + 1, question: q, answer: data.answer }]);
      setQuestion("");
    },
  });

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">AI Retention Advisor</h1>
        <p className="mt-1 text-sm text-slate-500">
          Grounded in your real backend context - the assistant can only cite retrieved data, never invent numbers.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="rounded-lg border bg-white p-6 shadow-sm">
          <h2 className="flex items-center gap-2 font-medium text-slate-900">
            <Sparkles className="h-4 w-4 text-slate-400" />
            Ask the analyst
          </h2>

          <div className="mt-3 flex flex-wrap gap-2">
            {ANALYST_EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => setQuestion(ex)}
                className="rounded-full border px-3 py-1 text-xs text-slate-600 hover:border-slate-900"
              >
                {ex}
              </button>
            ))}
          </div>

          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (question.trim().length >= 3) analyst.mutate(question.trim());
            }}
          >
            <input
              aria-label="Ask a question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about customers, churn, revenue, or segments..."
              className="w-full max-w-xl rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
            />
            <button
              type="submit"
              disabled={analyst.isPending}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
            >
              {analyst.isPending ? "Analyzing..." : "Ask"}
            </button>
          </form>

          {analyst.isError && (
            <p role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
              {analyst.error instanceof Error ? analyst.error.message : "The AI request failed."}
            </p>
          )}

          <div className="mt-4 max-h-[28rem] space-y-4 overflow-y-auto pr-1">
            {turns.length === 0 && (
              <p className="text-sm text-slate-400">
                Answers appear here. Follow-up questions keep the conversation context.
              </p>
            )}
            {turns.map((turn) => (
              <div key={turn.id} className="space-y-2">
                <p className="text-xs font-medium text-slate-500">{turn.question}</p>
                <div className="rounded-lg bg-slate-50 p-4">
                  <p className="whitespace-pre-wrap text-sm text-slate-700">{turn.answer}</p>
                </div>
              </div>
            ))}
            {analyst.isSuccess && analyst.data && (
              <ContextPanel data={analyst.data} />
            )}
          </div>
        </section>

        <NlToSqlPanel />
      </div>
    </div>
  );
}

function ContextPanel({ data }: { data: AiAnswerResult }) {
  return (
    <details className="rounded-lg border">
      <summary className="cursor-pointer px-4 py-2 text-xs font-medium text-slate-600">
        Retrieved context ({data.provider}) - source of truth
      </summary>
      <div className="space-y-3 border-t px-4 py-3">
        {data.retrieved_context.blocks.map((block) => (
          <div key={block.intent}>
            <p className="text-xs font-medium text-slate-500">{block.label}</p>
            <pre className="mt-1 max-h-48 overflow-auto text-xs text-slate-600">
              {JSON.stringify(block.data, null, 2)}
            </pre>
          </div>
        ))}
        <p className="text-xs text-slate-400">{data.disclaimer}</p>
      </div>
    </details>
  );
}

function NlToSqlPanel() {
  const [question, setQuestion] = useState("");
  const [customSql, setCustomSql] = useState("");

  const query = useMutation({
    mutationFn: () => runNlToSql(question.trim(), customSql),
  });

  return (
    <section className="rounded-lg border bg-white p-6 shadow-sm">
      <h2 className="flex items-center gap-2 font-medium text-slate-900">
        <Database className="h-4 w-4 text-slate-400" />
        Ask your data (NL-to-SQL)
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Curated SELECT queries over your tenant. Read-only, tenant-scoped, and row-capped by the server.
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {SQL_EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => setQuestion(ex)}
            className="rounded-full border px-3 py-1 text-xs text-slate-600 hover:border-slate-900"
          >
            {ex}
          </button>
        ))}
      </div>

      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim().length >= 3) query.mutate();
        }}
      >
        <input
          aria-label="Data question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. how many customers do we have?"
          className="w-full max-w-xl rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
        />
        <button
          type="submit"
          disabled={query.isPending || question.trim().length < 3}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
        >
          {query.isPending ? "Running..." : "Run"}
        </button>
      </form>

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-slate-500">Run your own SELECT</summary>
        <textarea
          aria-label="Custom SQL"
          value={customSql}
          onChange={(e) => setCustomSql(e.target.value)}
          rows={3}
          placeholder="SELECT rfm_segment, COUNT(*) FROM customer_features GROUP BY rfm_segment"
          className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs focus:border-slate-900 focus:outline-none"
        />
        <p className="mt-1 text-xs text-slate-400">
          The server rejects anything that is not a scoped SELECT - see the API docs for the guard chain.
        </p>
      </details>

      {query.isError && (
        <p role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {query.error instanceof Error ? query.error.message : "The query failed."}
        </p>
      )}

      {query.data && <SqlResult data={query.data} />}
    </section>
  );
}

function SqlResult({ data }: { data: NlToSqlResult }) {
  const columns = data.rows.length ? Object.keys(data.rows[0]) : [];
  return (
    <div className="mt-4 space-y-3">
      <div>
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase text-slate-500">Executed SQL</p>
          <span className="text-xs text-slate-400">
            {data.source} {data.intent ? `- ${data.intent}` : ""} - {data.row_count} rows
            {data.truncated ? " (truncated)" : ""}
          </span>
        </div>
        <pre className="mt-1 overflow-x-auto rounded-md bg-slate-900 p-3 text-xs text-emerald-300">{data.sql}</pre>
      </div>

      {columns.length > 0 ? (
        <div className="max-h-80 overflow-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-50">
              <tr>
                {columns.map((col) => (
                  <th key={col} className="px-3 py-2 text-left text-xs font-medium text-slate-500">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="border-t border-slate-100">
                  {columns.map((col) => (
                    <td key={col} className="px-3 py-1.5 tabular-nums text-slate-700">
                      {row[col] === null || row[col] === undefined ? "-" : String(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="rounded-md border border-dashed p-4 text-center text-sm text-slate-500">No rows returned.</p>
      )}
      <p className="text-xs text-slate-400">{data.note}</p>
    </div>
  );
}
