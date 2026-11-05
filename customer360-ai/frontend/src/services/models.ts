import client from "@/services/api";

export interface ModelVersionOut {
  id: number;
  version_no: number;
  family: string | null;
  metrics: Record<string, number | null | { tn: number; fp: number; fn: number; tp: number }> | null;
  features: string[] | null;
  trained_at: string | null;
  active: boolean;
}

export interface ModelOut {
  id: number;
  name: string;
  model_type: string;
  family: string | null;
  status: string;
  versions: ModelVersionOut[];
}

export const CHURN_FAMILY_LABELS: Record<string, string> = {
  logistic: "Logistic Regression",
  random_forest: "Random Forest",
  xgboost: "XGBoost",
  lightgbm: "LightGBM",
};

export interface ChurnTrainResult {
  status: string;
  model_id: number;
  champion: {
    family: string;
    model_version_id: number;
    version_no: number;
    metrics: Record<string, number | { tn: number; fp: number; fn: number; tp: number }>;
  };
  n_samples: number;
  window_days: number;
  families: Array<Record<string, unknown>>;
  trained_at: string;
}

export interface ChurnScoreResult {
  model_id: number;
  model_version_id: number;
  version_no: number;
  n_scored: number;
  items: Array<{ customer_id: number; probability: number; risk_level: string }>;
}

export interface AtRiskCustomer {
  prediction_id: number;
  customer_id: number;
  name: string | null;
  email: string | null;
  external_customer_id: string | null;
  probability: number;
  risk_level: string;
  risk_category: string;
  clv_est: number | null;
  value_at_risk: number | null;
}

export interface AtRiskSummary {
  total_value_at_risk: number;
  by_risk_level: Record<string, number>;
}

export interface AtRiskResult {
  items: AtRiskCustomer[];
  total: number;
  model_version_id: number | null;
  min_risk_level: string;
  median_clv: number;
  summary: AtRiskSummary;
}

export interface ExplanationContribution {
  feature: string;
  label: string;
  value: number | null;
  contribution: number;
  share: number;
}

export interface CustomerExplanation {
  prediction_id: number;
  customer_id: number;
  risk_level: string;
  probability: number;
  base_value: number;
  global_importance: Array<{ feature: string; label: string; importance: number; share: number }>;
  contributions: ExplanationContribution[];
}

export interface ChurnExplainResult {
  model_id: number;
  version_no: number;
  family: string;
  n_explained: number;
  base_value: number;
  global_importance: Array<{ feature: string; label: string; importance: number; share: number }>;
  items: Array<{
    prediction_id: number;
    customer_id: number;
    risk_level: string;
    probability: number;
    base_value: number;
    contributions: ExplanationContribution[];
  }>;
}

export async function fetchModels(): Promise<{ items: ModelOut[]; total: number }> {
  const res = await client.get<{ items: ModelOut[]; total: number }>("/models");
  return res.data;
}

export async function trainChurn(body: {
  families?: string[];
  churn_window_days?: number;
  random_seed?: number;
}): Promise<ChurnTrainResult> {
  const res = await client.post<ChurnTrainResult>("/models/churn/train", body);
  return res.data;
}

export async function activateVersion(modelId: number, versionId: number): Promise<ModelVersionOut> {
  const res = await client.post<ModelVersionOut>(`/models/${modelId}/versions/${versionId}/activate`);
  return res.data;
}

export async function scoreChurn(customerIds?: number[]): Promise<ChurnScoreResult> {
  const res = await client.post<ChurnScoreResult>("/predictions/churn", customerIds ? { customer_ids: customerIds } : {});
  return res.data;
}

export async function fetchAtRisk(minRiskLevel = "HIGH"): Promise<AtRiskResult> {
  const res = await client.get<AtRiskResult>("/predictions/at-risk", { params: { min_risk_level: minRiskLevel } });
  return res.data;
}

export async function explainChurn(customerIds?: number[]): Promise<ChurnExplainResult> {
  const res = await client.post<ChurnExplainResult>(
    "/predictions/churn/explain",
    customerIds ? { customer_ids: customerIds } : {},
  );
  return res.data;
}

export async function fetchCustomerExplanation(customerId: number): Promise<CustomerExplanation> {
  const res = await client.get<CustomerExplanation>(`/customers/${customerId}/explanation`);
  return res.data;
}

export interface RecommendationItem {
  id: number;
  customer_id: number;
  name: string;
  type: string;
  action: string;
  priority: string;
  priority_score: number;
  confidence: number;
  expected_objective: string | null;
  rules: string[];
  reason: string;
}

export interface RecommendationsResult {
  total: number;
  items: RecommendationItem[];
}

export const RECOMMENDATION_ACTION_LABELS: Record<string, string> = {
  WIN_BACK_OFFER: "Win-back offer",
  LOYALTY_PROGRAM: "Loyalty program",
  REENGAGEMENT: "Re-engagement campaign",
  CROSS_SELL: "Cross-sell",
  SUBSCRIPTION_PUSH: "Subscription push",
  CONCIERGE_SAVE: "Concierge save",
  CYCLE_REDUCE: "Order-cycle reduction",
  NOURISH: "Nurture (stay healthy)",
};

export async function generateRecommendations(body: {
  customer_ids?: number[];
  min_risk_level?: string;
}): Promise<RecommendationsResult & { n_generated: number; model_version_id: number | null }> {
  const res = await client.post<RecommendationsResult & { n_generated: number; model_version_id: number | null }>(
    "/recommendations/generate",
    body,
  );
  return res.data;
}

export async function fetchRecommendations(params?: {
  customer_id?: number;
  min_priority?: string;
}): Promise<RecommendationsResult> {
  const res = await client.get<RecommendationsResult>("/recommendations", { params });
  return res.data;
}

export interface WhatIfState {
  probability: number;
  risk_level: string;
  risk_category: string;
  value_at_risk: number | null;
}

export interface WhatIfResult {
  customer_id: number;
  model_version_id: number;
  family: string | null;
  scenario: string | null;
  changes: Record<string, number>;
  baseline: WhatIfState & { risk_level_changed?: never };
  scenario_output: WhatIfState & { risk_level_changed: boolean };
  delta: { probability: number; value_at_risk: number };
  median_clv: number;
  note: string;
}

export async function runCustomerWhatIf(
  customerId: number,
  body: { scenario?: string; changes?: Record<string, number> },
): Promise<WhatIfResult> {
  const res = await client.post<WhatIfResult>(`/customers/${customerId}/what-if`, body);
  return res.data;
}

export interface CohortRow {
  cohort: string;
  customers: number;
  retention: number[];
}

export interface CohortResult {
  cohorts: CohortRow[];
  months: number;
  as_of: string | null;
  summary: {
    repeat_share: number;
    avg_orders_per_customer: number;
    median_orders_per_customer: number;
    cohorts_analyzed: number;
    total_customers: number;
  };
}

export interface JourneyItem {
  stage: string;
  customers: number;
  share: number;
}

export interface JourneyMilestone {
  threshold: number;
  label: string;
  customers: number;
  share: number;
}

export interface TimeToRepeatBucket {
  range: string;
  days: number;
  customers: number;
  share: number;
}

export interface JourneyResult {
  stages: JourneyItem[];
  milestones: JourneyMilestone[];
  time_to_repeat: TimeToRepeatBucket[];
  avg_time_to_repeat_days: number | null;
  median_time_to_repeat_days: number | null;
  avg_orders_per_customer: number;
  as_of: string | null;
}

export interface ProductMeta {
  id: number;
  external_product_id: string | null;
  name: string | null;
  category: string | null;
}

export interface RecommendationEntry {
  product_id: number;
  external_product_id: string | null;
  name: string | null;
  category: string | null;
  co_purchases: number;
  confidence: number;
  lift: number;
  score: number;
}

export interface GlobalPairEntry {
  product_a: ProductMeta;
  product_b: ProductMeta;
  co_purchases: number;
  confidence: number;
  lift: number;
}

export interface ProductsResult {
  basis: { type: string; product_id: number | null; customer_id: number | null };
  total_baskets: number;
  recommendations: RecommendationEntry[] | GlobalPairEntry[];
  customer_purchased_products?: ProductMeta[];
}

export async function fetchCohorts(months = 12): Promise<CohortResult> {
  const res = await client.get<CohortResult>("/analytics/cohorts", { params: { months } });
  return res.data;
}

export async function fetchJourney(): Promise<JourneyResult> {
  const res = await client.get<JourneyResult>("/analytics/journey");
  return res.data;
}

export async function fetchProducts(params?: {
  product_id?: number;
  customer_id?: number;
  limit?: number;
}): Promise<ProductsResult> {
  const res = await client.get<ProductsResult>("/analytics/products", { params });
  return res.data;
}
export interface AiContextBlock {
  label: string;
  intent: string;
  data: Record<string, unknown>;
}

export interface AiContextResult {
  blocks: AiContextBlock[];
  matched_intents: string[];
}

export interface AiHistoryTurn {
  question: string;
  answer: string;
}

export interface AiAnswerResult {
  answer: string;
  retrieved_context: AiContextResult;
  context_labels: string[];
  provider: string;
  disclaimer: string;
}

export interface NlToSqlResult {
  question: string;
  sql: string;
  executed: boolean;
  intent: string | null;
  source: string;
  row_count: number;
  truncated: boolean;
  rows: Record<string, unknown>[];
  note: string;
}

export async function askAiAnalyst(
  question: string,
  history: AiHistoryTurn[] = [],
): Promise<AiAnswerResult> {
  const res = await client.post<AiAnswerResult>("/ai/query", { question, history });
  return res.data;
}

export async function fetchAiContext(question: string): Promise<AiContextResult> {
  const res = await client.get<AiContextResult>("/ai/context", { params: { question } });
  return res.data;
}

export async function runNlToSql(question: string, generatedSql?: string): Promise<NlToSqlResult> {
  const res = await client.post<NlToSqlResult>("/ai/nl-to-sql", {
    question,
    generated_sql: generatedSql?.trim() ? generatedSql.trim() : null,
  });
  return res.data;
}

// ---------------------------------------------------------------------------
// Campaigns

export const CAMPAIGN_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  ACTIVE: "Running",
  PAUSED: "Paused",
  COMPLETED: "Completed",
};

export const CAMPAIGN_STATUS_TONES: Record<string, string> = {
  DRAFT: "bg-slate-100 text-slate-700",
  ACTIVE: "bg-emerald-100 text-emerald-700",
  PAUSED: "bg-amber-100 text-amber-700",
  COMPLETED: "bg-sky-100 text-sky-700",
};

export const CAMPAIGN_CHANNELS = [
  { value: "email", label: "Email" },
  { value: "sms", label: "SMS" },
  { value: "push", label: "Push" },
  { value: "in_app", label: "In-app" },
] as const;

export const RFM_SEGMENT_OPTIONS = [
  { value: "champions", label: "Champions" },
  { value: "loyal_customers", label: "Loyal customers" },
  { value: "potential_loyalists", label: "Potential loyalists" },
  { value: "new_customers", label: "New customers" },
  { value: "promising", label: "Promising" },
  { value: "need_attention", label: "Need attention" },
  { value: "at_risk", label: "At risk" },
  { value: "cannot_lose_them", label: "Cannot lose them" },
  { value: "hibernating", label: "Hibernating" },
  { value: "lost", label: "Lost" },
];

export const RISK_LEVEL_OPTIONS = [
  { value: "CRITICAL", label: "Critical" },
  { value: "HIGH", label: "High" },
  { value: "MEDIUM", label: "Medium" },
  { value: "LOW", label: "Low" },
];

export interface AudienceFilterInput {
  segment_ids?: number[];
  rfm_segments?: string[];
  risk_levels?: string[];
  channels?: string[];
  customer_ids?: number[];
  exclude_customer_ids?: number[];
  min_clv?: number;
  max_clv?: number;
  min_frequency?: number;
  max_recency_days?: number;
  min_recency_days?: number;
  variant_split?: number;
}

export interface SimulationAssumptionsInput {
  response_rate?: number;
  conversion_rate?: number;
  revenue_per_conversion?: number;
  cost_per_contact?: number;
  control_discount_depth?: number;
  treatment_discount_depth?: number;
  confidence?: number;
}

export interface VariantMetricsInput {
  delivered?: number;
  responses?: number;
  conversions?: number;
  revenue?: number;
}

export interface CampaignResultSummary {
  id: number;
  response_rate: number | null;
  conversion_rate: number | null;
  revenue: number | null;
  cost: number | null;
  roi: number | null;
  measured_at: string;
}

export interface CampaignListItem {
  id: number;
  name: string;
  objective: string | null;
  channel: string | null;
  status: string;
  target_count: number | null;
  member_count: number;
  cost_est: number | null;
  start_date: string | null;
  end_date: string | null;
  latest_result: CampaignResultSummary | null;
  created_at: string | null;
}

export interface CampaignListResult {
  total: number;
  limit: number;
  items: CampaignListItem[];
  by_status: Record<string, number>;
}

export interface AudienceBreakdown {
  size: number;
  total_clv: number;
  avg_clv: number;
  median_clv: number;
  by_risk_level: Record<string, number>;
  with_features: number;
  scored_for_risk: number;
  capped_at: number;
}

export interface CampaignDetail {
  id: number;
  name: string;
  objective: string | null;
  offer: string | null;
  channel: string | null;
  status: string;
  audience_filter: AudienceFilterInput;
  assumptions: SimulationAssumptionsInput;
  target_count: number | null;
  member_count: number;
  variant_counts: Record<string, number>;
  cost_est: number | null;
  start_date: string | null;
  end_date: string | null;
  created_at: string | null;
  results: CampaignResultSummary[];
}

export interface SimulationForecast {
  contacts: number;
  response_rate: number;
  expected_responses: number;
  response_range: [number, number];
  expected_conversions: number;
  revenue_per_conversion: number;
  expected_revenue: number;
  estimated_cost: number;
  roi: number | null;
  break_even_response_rate: number | null;
}

export interface ExperimentPreview {
  control_size: number;
  treatment_size: number;
  expected_lift: number;
  p_value: number;
  significant: boolean;
  required_sample_size: number | null;
  verdict: string;
  split: string;
}

export interface SimulationResult {
  campaign_id: number;
  status: string;
  audience: AudienceBreakdown;
  assumptions: SimulationAssumptionsInput;
  by_risk_level?: Record<string, { customers: number; assumed_response_rate: number }>;
  forecast: SimulationForecast | null;
  experiment: ExperimentPreview | null;
  note: string;
}

export interface ExperimentArm {
  successes: number;
  trials: number;
  rate: number;
  ci_low: number;
  ci_high: number;
}

export interface ProportionTest {
  test: string;
  significant: boolean;
  p_value: number;
  z: number | null;
  confidence_level: number;
  confidence_interval?: [number, number];
  control: ExperimentArm;
  treatment: ExperimentArm;
  lift_absolute: number | null;
  lift_relative: number | null;
  required_sample_size: number | null;
  verdict: string;
  reason?: string;
}

export interface AbAnalysisResult {
  campaign_id: number;
  campaign_status: string;
  members: number;
  variant_split: number;
  variants: Record<string, { size: number; role: string }>;
  metric_compared: string;
  result_id: number | null;
  measured_at: string | null;
  test: ProportionTest;
  revenue_lift: number | null;
  note: string;
}

export interface CampaignMember {
  customer_id: number;
  label: string | null;
  status: string;
  variant: string;
  assigned_at: string | null;
}

export interface CampaignMembersResult {
  campaign_id: number;
  total: number;
  limit: number;
  offset: number;
  items: CampaignMember[];
}

export interface RecordResultsInput {
  response_rate?: number;
  conversion_rate?: number;
  revenue?: number;
  cost?: number;
  variant_metrics?: Record<string, VariantMetricsInput>;
  complete?: boolean;
}

export interface RecordResultsResponse {
  campaign_id: number;
  result_id: number;
  status: string;
  response_rate: number | null;
  conversion_rate: number | null;
  revenue: number | null;
  cost: number | null;
  roi: number | null;
  ab_analysis: AbAnalysisResult;
}

export async function fetchCampaigns(params?: {
  status?: string;
  channel?: string;
  limit?: number;
}): Promise<CampaignListResult> {
  const res = await client.get<CampaignListResult>("/campaigns", { params });
  return res.data;
}

export async function fetchCampaign(id: number): Promise<CampaignDetail> {
  const res = await client.get<CampaignDetail>(`/campaigns/${id}`);
  return res.data;
}

export async function createCampaign(payload: {
  name: string;
  objective?: string;
  offer?: string;
  channel?: string;
  audience_filter?: AudienceFilterInput;
  assumptions?: SimulationAssumptionsInput;
  start_date?: string;
  end_date?: string;
}): Promise<CampaignDetail> {
  const res = await client.post<CampaignDetail>("/campaigns", payload);
  return res.data;
}

export async function updateCampaign(
  id: number,
  payload: {
    name?: string;
    objective?: string;
    offer?: string;
    channel?: string;
    audience_filter?: AudienceFilterInput;
    assumptions?: SimulationAssumptionsInput;
    start_date?: string;
    end_date?: string;
  },
): Promise<CampaignDetail> {
  const res = await client.patch<CampaignDetail>(`/campaigns/${id}`, payload);
  return res.data;
}

export async function simulateCampaign(
  id: number,
  assumptions?: SimulationAssumptionsInput,
): Promise<SimulationResult> {
  const res = await client.post<SimulationResult>(`/campaigns/${id}/simulate`, { assumptions });
  return res.data;
}

export async function startCampaign(id: number): Promise<CampaignDetail> {
  const res = await client.post<CampaignDetail>(`/campaigns/${id}/start`);
  return res.data;
}

export async function recordCampaignResults(
  id: number,
  payload: RecordResultsInput,
): Promise<RecordResultsResponse> {
  const res = await client.post<RecordResultsResponse>(`/campaigns/${id}/results`, payload);
  return res.data;
}

export async function fetchCampaignMembers(
  id: number,
  params?: { limit?: number; offset?: number },
): Promise<CampaignMembersResult> {
  const res = await client.get<CampaignMembersResult>(`/campaigns/${id}/members`, { params });
  return res.data;
}

export async function fetchAbAnalysis(
  id: number,
  confidence?: number,
): Promise<AbAnalysisResult> {
  const res = await client.get<AbAnalysisResult>(`/campaigns/${id}/ab-analysis`, {
    params: confidence ? { confidence } : undefined,
  });
  return res.data;
}

// ---------------------------------------------------------------- monitoring

export type DriftStatus = "STABLE" | "MODERATE" | "SIGNIFICANT" | "UNKNOWN" | "NO_DATA";

export interface DistributionSummary {
  n: number;
  n_total: number;
  missing: number;
  missing_rate: number | null;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  p05: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
}

export interface FeatureDriftMetric {
  feature: string;
  status: DriftStatus;
  psi: number | null;
  ks: number | null;
  reference: DistributionSummary;
  current: DistributionSummary;
  mean_delta: number | null;
  missing_rate_delta: number | null;
  reason: string | null;
}

export interface PredictionDrift {
  metric: string;
  status: DriftStatus;
  psi: number | null;
  baseline_run_id: number | null;
  baseline_source: string;
  baseline_n: number | null;
  summary: DistributionSummary & { risk_mix: Record<string, number> };
  reason: string | null;
}

export interface ModelHealthBlock {
  days_since_trained: number | null;
  max_staleness_days: number;
  stale: boolean;
  training_duration_s: number | null;
  training_metrics: Record<string, unknown> | null;
  n_population: number;
  n_predictions: number;
  prediction_coverage: number | null;
  active: boolean;
}

export interface DriftReport {
  organization_id: number;
  model: {
    id: number;
    name: string;
    model_type: string;
    family: string | null;
    status: string;
  };
  model_version: {
    id: number;
    version_no: number;
    trained_at: string | null;
    active: boolean;
    train_dataset_version_id: number | null;
    features: string[];
  };
  baseline: { source: string; dataset_version_id: number | null; note: string };
  summary: {
    drift_status: DriftStatus;
    psi_overall: number | null;
    n_features_profiled: number;
    n_features_drifted: number;
    drifted_features: string[];
  };
  features: FeatureDriftMetric[];
  prediction: PredictionDrift;
  health: ModelHealthBlock;
  thresholds: Record<string, number>;
  min_samples: number;
  last_run_id: number | null;
  generated_at: string;
}

export interface MonitoringRun {
  id: number;
  model_id: number;
  model_version_id: number;
  reference_dataset_version_id: number | null;
  baseline_run_id: number | null;
  triggered_by: number | null;
  created_at: string;
  finished_at: string | null;
  status: string;
  drift_status: DriftStatus | null;
  psi_overall: number | null;
  n_features_drifted: number;
  drifted_features: string[];
  prediction_psi: number | null;
  prediction_drift_status: DriftStatus | null;
  n_reference_rows: number | null;
  n_current_rows: number | null;
  duration_s: number | null;
  error: string | null;
  feature_metrics?: FeatureDriftMetric[];
  prediction_summary?: Record<string, unknown>;
  health?: ModelHealthBlock;
}

export interface MonitoringRunList {
  items: MonitoringRun[];
  total: number;
  limit: number;
  offset: number;
}

export interface ModelHealthItem {
  model_id: number;
  model_name: string;
  model_type: string;
  family: string | null;
  model_version_id: number;
  version_no: number;
  active: boolean;
  trained_at: string | null;
  n_predictions: number;
  days_since_trained: number | null;
  stale: boolean;
  prediction_coverage: number | null;
  training_metrics: Record<string, unknown> | null;
  last_run: {
    id: number;
    drift_status: DriftStatus | null;
    psi_overall: number | null;
    prediction_drift_status: DriftStatus | null;
    status: string;
    created_at: string;
  } | null;
}

export interface ModelHealthResponse {
  items: ModelHealthItem[];
  total: number;
  thresholds: Record<string, number>;
}

export async function fetchDriftReport(modelVersionId?: number): Promise<DriftReport> {
  const res = await client.get<DriftReport>("/monitoring/drift", {
    params: modelVersionId ? { model_version_id: modelVersionId } : undefined,
  });
  return res.data;
}

export async function fetchMonitoringRuns(params?: {
  modelVersionId?: number;
  limit?: number;
  offset?: number;
}): Promise<MonitoringRunList> {
  const res = await client.get<MonitoringRunList>("/monitoring/runs", {
    params: {
      model_version_id: params?.modelVersionId,
      limit: params?.limit,
      offset: params?.offset,
    },
  });
  return res.data;
}

export async function fetchMonitoringRun(id: number): Promise<MonitoringRun> {
  const res = await client.get<MonitoringRun>(`/monitoring/runs/${id}`);
  return res.data;
}

export async function recordMonitoringRun(modelVersionId?: number): Promise<MonitoringRun> {
  const res = await client.post<MonitoringRun>("/monitoring/runs", null, {
    params: modelVersionId ? { model_version_id: modelVersionId } : undefined,
  });
  return res.data;
}

export async function fetchModelHealth(): Promise<ModelHealthResponse> {
  const res = await client.get<ModelHealthResponse>("/monitoring/models");
  return res.data;
}

// ------------------------------------------------------------------- reports

export interface ReportCatalogItem {
  key: string;
  label: string;
  description: string;
  filters: string[];
  formats: string[];
}

export interface ReportCatalog {
  items: ReportCatalogItem[];
  formats: string[];
  max_rows: number;
  pdf_status: string;
}

export interface ReportDownload {
  blob: Blob;
  filename: string;
  rowCount: number;
  truncated: boolean;
}

export async function fetchReportCatalog(): Promise<ReportCatalog> {
  const res = await client.get<ReportCatalog>("/reports/catalog");
  return res.data;
}

function filenameFromHeader(header: string | undefined, fallback: string): string {
  if (!header) return fallback;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8) return decodeURIComponent(utf8[1]);
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : fallback;
}

export async function exportReport(
  report: string,
  format: string,
  filters?: Record<string, unknown>,
): Promise<ReportDownload> {
  const res = await client.post<Blob>("/reports/export", { report, format, filters }, {
    responseType: "blob",
  });
  return {
    blob: res.data,
    filename: filenameFromHeader(res.headers["content-disposition"], `${report}.${format}`),
    rowCount: Number(res.headers["x-report-rows"] ?? 0),
    truncated: res.headers["x-report-truncated"] === "true",
  };
}
