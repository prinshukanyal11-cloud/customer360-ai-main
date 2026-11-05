import client from "@/services/api";

export type SegmentType = "RFM" | "ML";

export interface Segment {
  id: number;
  name: string;
  type: SegmentType;
  description: string | null;
  color: string | null;
  active: boolean;
  member_count: number;
  rules_json: Record<string, unknown> | null;
}

export interface TrainSegmentationRequest {
  n_clusters?: number;
  use_pca?: boolean;
  random_seed?: number;
}

export interface TrainedSegment {
  name: string;
  size: number;
  profile: string;
}

export interface TrainResult {
  status: string;
  model_id: number;
  model_version_id: number;
  version_no: number;
  n_clusters: number;
  n_samples: number;
  silhouette_score: number | null;
  inertia: number;
  auto_selected: boolean;
  segments: TrainedSegment[];
  trained_at: string;
}

export interface SegmentStat {
  metric: string;
  avg: number;
  median: number;
}

export interface SegmentChannel {
  channel: string;
  orders: number;
}

export interface SegmentCategory {
  category: string;
  revenue: number;
}

export interface SegmentMember {
  customer_id: number;
  name: string | null;
  external_customer_id: string | null;
  clv_est: number | null;
  engagement_score: number | null;
  frequency: number | null;
}

export interface ClusterAnalytics {
  segment: Pick<Segment, "id" | "name" | "type" | "color" | "description">;
  member_count: number;
  repeat_purchase_share: number;
  stats: SegmentStat[] | null;
  channels: SegmentChannel[];
  categories: SegmentCategory[];
  top_members: SegmentMember[];
}

export async function fetchSegments(segmentType?: SegmentType): Promise<{ items: Segment[]; total: number }> {
  const res = await client.get<{ items: Segment[]; total: number }>("/segments", {
    params: segmentType ? { segment_type: segmentType } : undefined,
  });
  return res.data;
}

export async function trainSegmentation(body: TrainSegmentationRequest = {}): Promise<TrainResult> {
  const res = await client.post<TrainResult>("/segments/train", body);
  return res.data;
}

export async function fetchClusterAnalytics(segmentId: number): Promise<ClusterAnalytics> {
  const res = await client.get<ClusterAnalytics>(`/segments/${segmentId}/clusters`);
  return res.data;
}

export async function renameSegment(
  segmentId: number,
  body: { name: string; description?: string | null; color?: string | null },
): Promise<Pick<Segment, "id" | "name" | "type" | "description" | "color">> {
  const res = await client.patch<Pick<Segment, "id" | "name" | "type" | "description" | "color">>(
    `/segments/${segmentId}`,
    body,
  );
  return res.data;
}