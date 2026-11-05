import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CAMPAIGN_CHANNELS,
  CAMPAIGN_STATUS_LABELS,
  CAMPAIGN_STATUS_TONES,
  RISK_LEVEL_OPTIONS,
  RFM_SEGMENT_OPTIONS,
  createCampaign,
  fetchAbAnalysis,
  fetchCampaign,
  fetchCampaignMembers,
  fetchCampaigns,
  recordCampaignResults,
  simulateCampaign,
  startCampaign,
  updateCampaign,
  type AudienceFilterInput,
  type SimulationAssumptionsInput,
} from "@/services/models";

type DetailTab = "plan" | "audience" | "results";

const DETAIL_TABS: { id: DetailTab; label: string }[] = [
  { id: "plan", label: "Plan & simulation" },
  { id: "audience", label: "Audience" },
  { id: "results", label: "Results & A/B" },
];

const STATUS_FILTERS = ["", "DRAFT", "ACTIVE", "COMPLETED"];

const DEFAULT_ASSUMPTIONS: Required<
  Pick<SimulationAssumptionsInput, "response_rate" | "conversion_rate" | "cost_per_contact" | "treatment_discount_depth">
> = {
  response_rate: 0.12,
  conversion_rate: 0.2,
  cost_per_contact: 0.35,
  treatment_discount_depth: 0.15,
};

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function CampaignsPage() {
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [building, setBuilding] = useState(false);

  const campaigns = useQuery({
    queryKey: ["campaigns", status],
    queryFn: () => fetchCampaigns(status ? { status } : undefined),
  });

  const items = campaigns.data?.items ?? [];
  const activeId = selectedId ?? items[0]?.id ?? null;

  return (
    <div className="p-8">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Campaigns</h1>
          <p className="mt-1 text-sm text-slate-500">
            Target a segment, forecast the response, then prove the offer worked with a real A/B test.
          </p>
        </div>
        <button
          onClick={() => setBuilding((open) => !open)}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          {building ? "Close builder" : "New campaign"}
        </button>
      </header>

      {building && (
        <div className="mb-6">
          <CampaignBuilder
            onCreated={(id) => {
              setBuilding(false);
              setSelectedId(id);
            }}
          />
        </div>
      )}

      <div className="mb-4 flex items-center gap-1 rounded-lg border bg-white p-1 shadow-sm sm:w-fit">
        {STATUS_FILTERS.map((value) => (
          <button
            key={value || "all"}
            onClick={() => setStatus(value)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${
              status === value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {value ? CAMPAIGN_STATUS_LABELS[value] : "All"}
          </button>
        ))}
      </div>

      {campaigns.isError && (
        <p role="alert" className="mb-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {campaigns.error instanceof Error ? campaigns.error.message : "Failed to load campaigns."}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <section className="rounded-lg border bg-white shadow-sm">
          <h2 className="border-b px-4 py-3 text-sm font-semibold text-slate-700">
            {campaigns.data ? `${campaigns.data.total} campaigns` : "Loading…"}
          </h2>
          {campaigns.isLoading && <p className="px-4 py-6 text-sm text-slate-500">Loading…</p>}
          {!campaigns.isLoading && items.length === 0 && (
            <p className="px-4 py-6 text-sm text-slate-500">No campaigns yet. Build one to see the forecast.</p>
          )}
          <ul className="divide-y">
            {items.map((campaign) => (
              <li key={campaign.id}>
                <button
                  onClick={() => setSelectedId(campaign.id)}
                  className={`w-full px-4 py-3 text-left transition-colors ${
                    campaign.id === activeId ? "bg-slate-50" : "hover:bg-slate-50"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-900">{campaign.name}</span>
                    <StatusPill status={campaign.status} />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {campaign.member_count} targeted
                    {campaign.latest_result ? ` · ROI ${percent(campaign.latest_result.roi, 0)}` : ""}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          {activeId === null ? (
            <div className="rounded-lg border border-dashed bg-white p-10 text-center text-sm text-slate-500">
              Select a campaign to review its plan, audience, and experiment.
            </div>
          ) : (
            <CampaignDetailPanel key={activeId} campaignId={activeId} />
          )}
        </section>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        CAMPAIGN_STATUS_TONES[status] ?? "bg-slate-100 text-slate-700"
      }`}
    >
      {CAMPAIGN_STATUS_LABELS[status] ?? status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// builder

interface BuilderState {
  name: string;
  objective: string;
  offer: string;
  channel: string;
  rfmSegments: string[];
  riskLevels: string[];
  minClv: string;
  maxRecencyDays: string;
  variantSplit: number;
  responseRate: string;
  conversionRate: string;
  costPerContact: string;
  treatmentDiscount: string;
}

const INITIAL_BUILDER: BuilderState = {
  name: "",
  objective: "",
  offer: "",
  channel: "email",
  rfmSegments: [],
  riskLevels: [],
  minClv: "",
  maxRecencyDays: "",
  variantSplit: 0.5,
  responseRate: String(DEFAULT_ASSUMPTIONS.response_rate),
  conversionRate: String(DEFAULT_ASSUMPTIONS.conversion_rate),
  costPerContact: String(DEFAULT_ASSUMPTIONS.cost_per_contact),
  treatmentDiscount: String(DEFAULT_ASSUMPTIONS.treatment_discount_depth),
};

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function CampaignBuilder({ onCreated }: { onCreated: (id: number) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BuilderState>(INITIAL_BUILDER);

  const set = <K extends keyof BuilderState>(key: K, value: BuilderState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const audience = useMemo<AudienceFilterInput>(() => {
    const filter: AudienceFilterInput = { variant_split: form.variantSplit };
    if (form.rfmSegments.length) filter.rfm_segments = form.rfmSegments;
    if (form.riskLevels.length) filter.risk_levels = form.riskLevels;
    if (form.minClv.trim()) filter.min_clv = Number(form.minClv);
    if (form.maxRecencyDays.trim()) filter.max_recency_days = Number(form.maxRecencyDays);
    return filter;
  }, [form.rfmSegments, form.riskLevels, form.minClv, form.maxRecencyDays, form.variantSplit]);

  const create = useMutation({
    mutationFn: () =>
      createCampaign({
        name: form.name.trim(),
        objective: form.objective.trim() || undefined,
        offer: form.offer.trim() || undefined,
        channel: form.channel,
        audience_filter: audience,
        assumptions: {
          response_rate: Number(form.responseRate),
          conversion_rate: Number(form.conversionRate),
          cost_per_contact: Number(form.costPerContact),
          treatment_discount_depth: Number(form.treatmentDiscount),
        },
      }),
    onSuccess: (campaign) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      setForm(INITIAL_BUILDER);
      onCreated(campaign.id);
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
      className="space-y-5 rounded-lg border bg-white p-5 shadow-sm"
    >
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Campaign name" required>
          <input
            value={form.name}
            onChange={(event) => set("name", event.target.value)}
            required
            maxLength={255}
            placeholder="Lapsed win-back"
            className={INPUT}
          />
        </Field>
        <Field label="Objective">
          <input
            value={form.objective}
            onChange={(event) => set("objective", event.target.value)}
            maxLength={255}
            placeholder="Bring dormant buyers back"
            className={INPUT}
          />
        </Field>
        <Field label="Offer">
          <input
            value={form.offer}
            onChange={(event) => set("offer", event.target.value)}
            maxLength={255}
            placeholder="20% off next order"
            className={INPUT}
          />
        </Field>
      </div>

      <fieldset>
        <legend className="mb-2 text-sm font-medium text-slate-700">Channel</legend>
        <div className="flex flex-wrap gap-2">
          {CAMPAIGN_CHANNELS.map((channel) => (
            <Chip
              key={channel.value}
              active={form.channel === channel.value}
              onClick={() => set("channel", channel.value)}
            >
              {channel.label}
            </Chip>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium text-slate-700">RFM segment</legend>
        <div className="flex flex-wrap gap-2">
          {RFM_SEGMENT_OPTIONS.map((segment) => (
            <Chip
              key={segment.value}
              active={form.rfmSegments.includes(segment.value)}
              onClick={() => set("rfmSegments", toggle(form.rfmSegments, segment.value))}
            >
              {segment.label}
            </Chip>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium text-slate-700">Churn risk</legend>
        <div className="flex flex-wrap gap-2">
          {RISK_LEVEL_OPTIONS.map((level) => (
            <Chip
              key={level.value}
              active={form.riskLevels.includes(level.value)}
              onClick={() => set("riskLevels", toggle(form.riskLevels, level.value))}
            >
              {level.label}
            </Chip>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Minimum CLV" hint="Leave blank for no value floor">
          <input
            type="number"
            min={0}
            step="1"
            value={form.minClv}
            onChange={(event) => set("minClv", event.target.value)}
            placeholder="0"
            className={INPUT}
          />
        </Field>
        <Field label="Max days since last order" hint="Blank for no recency cap">
          <input
            type="number"
            min={0}
            step="1"
            value={form.maxRecencyDays}
            onChange={(event) => set("maxRecencyDays", event.target.value)}
            placeholder="90"
            className={INPUT}
          />
        </Field>
        <Field label={`Treatment share: ${Math.round(form.variantSplit * 100)}%`} hint="The rest is control">
          <input
            type="range"
            min={0.1}
            max={0.9}
            step={0.05}
            value={form.variantSplit}
            onChange={(event) => set("variantSplit", Number(event.target.value))}
            className="w-full"
          />
        </Field>
      </div>

      <fieldset className="rounded-md border border-dashed bg-slate-50 p-4">
        <legend className="px-1 text-sm font-medium text-slate-700">Simulation assumptions</legend>
        <div className="grid gap-4 md:grid-cols-4">
          <Field label="Response rate">
            <input
              type="number"
              min={0}
              max={1}
              step="0.01"
              value={form.responseRate}
              onChange={(event) => set("responseRate", event.target.value)}
              className={INPUT}
            />
          </Field>
          <Field label="Conversion rate">
            <input
              type="number"
              min={0}
              max={1}
              step="0.01"
              value={form.conversionRate}
              onChange={(event) => set("conversionRate", event.target.value)}
              className={INPUT}
            />
          </Field>
          <Field label="Cost per contact">
            <input
              type="number"
              min={0}
              step="0.05"
              value={form.costPerContact}
              onChange={(event) => set("costPerContact", event.target.value)}
              className={INPUT}
            />
          </Field>
          <Field label="Treatment discount">
            <input
              type="number"
              min={0}
              max={1}
              step="0.05"
              value={form.treatmentDiscount}
              onChange={(event) => set("treatmentDiscount", event.target.value)}
              className={INPUT}
            />
          </Field>
        </div>
      </fieldset>

      {create.isError && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {create.error instanceof Error ? create.error.message : "Could not create the campaign."}
        </p>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={create.isPending || !form.name.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {create.isPending ? "Creating…" : "Create draft"}
        </button>
        <p className="text-xs text-slate-500">
          Drafts are free to edit. Nothing is sent until you launch.
        </p>
      </div>
    </form>
  );
}

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-900 focus:outline-none";

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-slate-300 bg-white text-slate-600 hover:border-slate-400"
      }`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// detail

function CampaignDetailPanel({ campaignId }: { campaignId: number }) {
  const [tab, setTab] = useState<DetailTab>("plan");
  const queryClient = useQueryClient();

  const detail = useQuery({ queryKey: ["campaign", campaignId], queryFn: () => fetchCampaign(campaignId) });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
  };

  if (detail.isError) {
    return (
      <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
        {detail.error instanceof Error ? detail.error.message : "Failed to load the campaign."}
      </p>
    );
  }
  if (!detail.data) {
    return <div className="rounded-lg border bg-white p-10 text-center text-sm text-slate-500">Loading…</div>;
  }

  const campaign = detail.data;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-slate-900">{campaign.name}</h2>
              <StatusPill status={campaign.status} />
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {campaign.objective ?? "No objective set"} · {campaign.channel ?? "email"}
              {campaign.offer ? ` · offer: ${campaign.offer}` : ""}
            </p>
          </div>
          <div className="text-right text-xs text-slate-500">
            <p>{campaign.member_count} in the audience</p>
            <p>Estimated cost {money(campaign.cost_est)}</p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 rounded-lg border bg-white p-1 shadow-sm sm:w-fit">
        {DETAIL_TABS.map(({ id, label }) => (
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

      {tab === "plan" && <PlanTab campaign={campaign} onChanged={refresh} />}
      {tab === "audience" && <AudienceTab campaignId={campaignId} />}
      {tab === "results" && <ResultsTab campaignId={campaignId} onChanged={refresh} />}
    </div>
  );
}

function PlanTab({
  campaign,
  onChanged,
}: {
  campaign: Awaited<ReturnType<typeof fetchCampaign>>;
  onChanged: () => void;
}) {
  const [name, setName] = useState(campaign.name);
  const [responseRate, setResponseRate] = useState(String(campaign.assumptions.response_rate ?? 0.12));
  const [costPerContact, setCostPerContact] = useState(String(campaign.assumptions.cost_per_contact ?? 0.35));

  const simulate = useMutation({
    mutationFn: () =>
      simulateCampaign(campaign.id, {
        response_rate: Number(responseRate),
        cost_per_contact: Number(costPerContact),
      }),
  });

  const save = useMutation({
    mutationFn: () =>
      updateCampaign(campaign.id, {
        name: name.trim(),
        assumptions: { response_rate: Number(responseRate), cost_per_contact: Number(costPerContact) },
      }),
    onSuccess: onChanged,
  });

  const launch = useMutation({
    mutationFn: () => startCampaign(campaign.id),
    onSuccess: onChanged,
  });

  const isDraft = campaign.status === "DRAFT";
  const forecast = simulate.data?.forecast ?? null;
  const experiment = simulate.data?.experiment ?? null;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-5 shadow-sm">
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="Campaign name">
            <input value={name} onChange={(event) => setName(event.target.value)} className={INPUT} />
          </Field>
          <Field label="Response rate">
            <input
              type="number"
              min={0}
              max={1}
              step="0.01"
              value={responseRate}
              onChange={(event) => setResponseRate(event.target.value)}
              className={INPUT}
            />
          </Field>
          <Field label="Cost per contact">
            <input
              type="number"
              min={0}
              step="0.05"
              value={costPerContact}
              onChange={(event) => setCostPerContact(event.target.value)}
              className={INPUT}
            />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            onClick={() => simulate.mutate()}
            disabled={simulate.isPending}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {simulate.isPending ? "Simulating…" : "Simulate"}
          </button>
          {isDraft && (
            <>
              <button
                onClick={() => save.mutate()}
                disabled={save.isPending || !name.trim()}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Save draft
              </button>
              <button
                onClick={() => launch.mutate()}
                disabled={launch.isPending}
                className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {launch.isPending ? "Launching…" : "Launch campaign"}
              </button>
            </>
          )}
          {!isDraft && (
            <p className="text-xs text-slate-500">
              The audience is frozen once launched, so this campaign can no longer be edited.
            </p>
          )}
        </div>
        {(simulate.isError || save.isError || launch.isError) && (
          <p role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
            {(simulate.error ?? save.error ?? launch.error) instanceof Error
              ? ((simulate.error ?? save.error ?? launch.error) as Error).message
              : "The request failed."}
          </p>
        )}
      </div>

      {simulate.isError === false && simulate.data && !forecast && (
        <p className="rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800">{simulate.data.note}</p>
      )}

      {forecast && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Contacts" value={forecast.contacts.toLocaleString()} />
            <Stat
              label="Expected responses"
              value={`${forecast.expected_responses} (${percent(forecast.response_rate)})`}
              hint={`likely ${forecast.response_range[0]}–${forecast.response_range[1]}`}
            />
            <Stat
              label="Expected revenue"
              value={money(forecast.expected_revenue)}
              hint={`cost ${money(forecast.estimated_cost)}`}
            />
            <Stat
              label="Projected ROI"
              value={percent(forecast.roi, 0)}
              hint={`break-even at ${percent(forecast.break_even_response_rate)} response`}
            />
          </div>
          {experiment && (
            <div className="rounded-lg border bg-white p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-800">Will this test be conclusive?</h3>
              <p className="mt-1 text-sm text-slate-600">
                {experiment.split} · projected lift {percent(experiment.expected_lift)} · p-value{" "}
                {experiment.p_value}
              </p>
              <p className="mt-2 text-sm text-slate-600">{experiment.verdict}</p>
              {experiment.required_sample_size && (
                <p className="mt-1 text-xs text-slate-500">
                  Roughly {experiment.required_sample_size.toLocaleString()} contacts per arm would be needed to
                  detect this effect.
                </p>
              )}
            </div>
          )}
          <p className="text-xs text-slate-500">{simulate.data?.note}</p>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function AudienceTab({ campaignId }: { campaignId: number }) {
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const members = useQuery({
    queryKey: ["campaign-members", campaignId, offset, limit],
    queryFn: () => fetchCampaignMembers(campaignId, { limit, offset }),
  });

  const data = members.data;
  const pages = data ? Math.ceil(data.total / limit) : 0;

  return (
    <div className="rounded-lg border bg-white shadow-sm">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-700">
          {data ? `${data.total} members` : "Loading members…"}
        </h3>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <button
            onClick={() => setOffset(Math.max(0, offset - limit))}
            disabled={offset === 0}
            className="rounded border px-2 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          <span>
            {pages === 0 ? 0 : offset / limit + 1} / {pages}
          </span>
          <button
            onClick={() => setOffset(offset + limit)}
            disabled={!data || offset + limit >= data.total}
            className="rounded border px-2 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
      {members.isError && (
        <p role="alert" className="px-4 py-4 text-sm text-red-700">
          {members.error instanceof Error ? members.error.message : "Failed to load members."}
        </p>
      )}
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2">Customer</th>
            <th className="px-4 py-2">Variant</th>
            <th className="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {(data?.items ?? []).map((member) => (
            <tr key={member.customer_id}>
              <td className="px-4 py-2 text-slate-800">
                {member.label ?? `#${member.customer_id}`}
                <span className="ml-2 text-xs text-slate-400">#{member.customer_id}</span>
              </td>
              <td className="px-4 py-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    member.variant === "B"
                      ? "bg-violet-100 text-violet-700"
                      : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {member.variant === "B" ? "B · treatment" : "A · control"}
                </span>
              </td>
              <td className="px-4 py-2 text-slate-500">{member.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.total === 0 && (
        <p className="px-4 py-6 text-center text-sm text-slate-500">
          No members yet — launch the campaign to snapshot the audience.
        </p>
      )}
    </div>
  );
}

interface ArmForm {
  delivered: string;
  responses: string;
  conversions: string;
  revenue: string;
}

const EMPTY_ARM: ArmForm = { delivered: "", responses: "", conversions: "", revenue: "" };

function ResultsTab({ campaignId, onChanged }: { campaignId: number; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [control, setControl] = useState<ArmForm>(EMPTY_ARM);
  const [treatment, setTreatment] = useState<ArmForm>(EMPTY_ARM);
  const [revenue, setRevenue] = useState("");
  const [cost, setCost] = useState("");
  const [complete, setComplete] = useState(false);

  const analysis = useQuery({
    queryKey: ["campaign-ab", campaignId],
    queryFn: () => fetchAbAnalysis(campaignId),
  });

  const record = useMutation({
    mutationFn: () =>
      recordCampaignResults(campaignId, {
        revenue: revenue.trim() ? Number(revenue) : undefined,
        cost: cost.trim() ? Number(cost) : undefined,
        variant_metrics: {
          A: armPayload(control),
          B: armPayload(treatment),
        },
        complete,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaign-ab", campaignId] });
      onChanged();
      setControl(EMPTY_ARM);
      setTreatment(EMPTY_ARM);
      setRevenue("");
      setCost("");
      setComplete(false);
    },
  });

  const test = analysis.data?.test;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-800">Record what happened</h3>
        <p className="mt-1 text-sm text-slate-500">
          Per-arm numbers are what turn the split into a verdict. Without them the analysis reports sizes only.
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <ArmFields label="A · control" value={control} onChange={setControl} />
          <ArmFields label="B · treatment" value={treatment} onChange={setTreatment} />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Field label="Total revenue">
            <input
              type="number"
              min={0}
              step="0.01"
              value={revenue}
              onChange={(event) => setRevenue(event.target.value)}
              className={INPUT}
            />
          </Field>
          <Field label="Total cost">
            <input
              type="number"
              min={0}
              step="0.01"
              value={cost}
              onChange={(event) => setCost(event.target.value)}
              className={INPUT}
            />
          </Field>
        </div>
        <label className="mt-4 flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={complete} onChange={(event) => setComplete(event.target.checked)} />
          Close the campaign after recording
        </label>
        {record.isError && (
          <p role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
            {record.error instanceof Error ? record.error.message : "Could not record the results."}
          </p>
        )}
        <button
          onClick={() => record.mutate()}
          disabled={record.isPending}
          className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {record.isPending ? "Recording…" : "Record results"}
        </button>
      </div>

      {analysis.isError && (
        <p role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
          {analysis.error instanceof Error ? analysis.error.message : "Failed to load the experiment."}
        </p>
      )}

      {test && (
        <div className="rounded-lg border bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">A/B verdict</h3>
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                test.significant
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-slate-100 text-slate-600"
              }`}
            >
              {test.significant ? "Significant" : "Not significant"}
            </span>
          </div>
          {analysis.data && (
            <p className="mt-1 text-sm text-slate-500">
              Comparing {analysis.data.metric_compared} across{" "}
              {analysis.data.variants.A?.size ?? 0} control and {analysis.data.variants.B?.size ?? 0} treatment
              {analysis.data.measured_at ? ` · measured ${analysis.data.measured_at.slice(0, 10)}` : ""}
            </p>
          )}
          <p className="mt-3 text-sm text-slate-700">{test.verdict}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Control rate" value={percent(test.control.rate)} hint={`n=${test.control.trials}`} />
            <Stat label="Treatment rate" value={percent(test.treatment.rate)} hint={`n=${test.treatment.trials}`} />
            <Stat
              label="Relative lift"
              value={percent(test.lift_relative, 0)}
              hint={`${percent(test.lift_absolute)} absolute`}
            />
            <Stat label="p-value" value={String(test.p_value)} hint={`${Math.round(test.confidence_level * 100)}% confidence`} />
          </div>
          {analysis.data?.revenue_lift !== null && analysis.data?.revenue_lift !== undefined && (
            <p className="mt-3 text-sm text-slate-600">
              Revenue lift: {percent(analysis.data.revenue_lift, 0)}
            </p>
          )}
          {test.required_sample_size && (
            <p className="mt-1 text-xs text-slate-500">
              To detect an effect this size with confidence, aim for about{" "}
              {test.required_sample_size.toLocaleString()} contacts per arm.
            </p>
          )}
          <p className="mt-3 text-xs text-slate-400">{analysis.data?.note}</p>
        </div>
      )}
    </div>
  );
}

function ArmFields({
  label,
  value,
  onChange,
}: {
  label: string;
  value: ArmForm;
  onChange: (next: ArmForm) => void;
}) {
  const set = (key: keyof ArmForm) => (event: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...value, [key]: event.target.value });
  return (
    <fieldset className="rounded-md border border-dashed p-4">
      <legend className="px-1 text-sm font-medium text-slate-700">{label}</legend>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Delivered">
          <input type="number" min={0} value={value.delivered} onChange={set("delivered")} className={INPUT} />
        </Field>
        <Field label="Responses">
          <input type="number" min={0} value={value.responses} onChange={set("responses")} className={INPUT} />
        </Field>
        <Field label="Conversions">
          <input type="number" min={0} value={value.conversions} onChange={set("conversions")} className={INPUT} />
        </Field>
        <Field label="Revenue">
          <input type="number" min={0} step="0.01" value={value.revenue} onChange={set("revenue")} className={INPUT} />
        </Field>
      </div>
    </fieldset>
  );
}

function armPayload(form: ArmForm) {
  const payload: Record<string, number> = {};
  if (form.delivered.trim()) payload.delivered = Number(form.delivered);
  if (form.responses.trim()) payload.responses = Number(form.responses);
  if (form.conversions.trim()) payload.conversions = Number(form.conversions);
  if (form.revenue.trim()) payload.revenue = Number(form.revenue);
  return payload;
}
