import client from "@/services/api";

export interface FeaturesSummary {
  customers_with_features: number;
  buyers: number;
  total_customers: number;
  coverage: number;
  dataset_version_ids: number[];
  as_of: string | null;
}

export interface RfmDistribution {
  total_customers: number;
  segments: { segment: string; customer_count: number; share: number }[];
}

export interface ClvDistribution {
  avg_clv: number;
  median_clv: number;
  total_clv: number;
  customers: number;
  percentiles: { [key: string]: number };
  buckets: { label: string; count: number }[];
}

export interface TrendPoint {
  month: string;
  orders: number;
  revenue: number;
  active_customers: number;
}

export interface RevenueTrends {
  months: number;
  items: TrendPoint[];
}

export async function fetchFeaturesSummary(): Promise<FeaturesSummary> {
  const res = await client.get<FeaturesSummary>("/analytics/features-summary");
  return res.data;
}

export async function fetchRfmDistribution(): Promise<RfmDistribution> {
  const res = await client.get<RfmDistribution>("/analytics/rfm");
  return res.data;
}

export async function fetchClvDistribution(): Promise<ClvDistribution> {
  const res = await client.get<ClvDistribution>("/analytics/clv");
  return res.data;
}

export async function fetchRevenueTrends(months = 12): Promise<RevenueTrends> {
  const res = await client.get<RevenueTrends>("/analytics/trends", { params: { months } });
  return res.data;
}

export async function buildFeatures(): Promise<{ status: string; buyers: number }> {
  const res = await client.post<{ status: string; buyers: number }>("/features/build");
  return res.data;
}