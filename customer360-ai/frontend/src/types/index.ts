export interface User {
  id: number;
  email: string;
  full_name: string | null;
  role: "ADMIN" | "ANALYST" | "VIEWER";
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface ApiEnvelope<T> {
  data: T;
  meta?: Record<string, unknown>;
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

// Placeholder domain types (expanded in later phases)
export interface CustomerSummary {
  id: number;
  external_customer_id: string;
  name: string | null;
  location: string | null;
  [key: string]: unknown;
}

// ---- Datasets (Phase 2) ----

export type Mapping = Record<string, Record<string, string>>;

export interface UploadColumnInfo {
  name: string;
  detected_type: string;
  missing: number;
  sample_values: string[];
}

export interface UploadResult {
  dataset_id: number;
  version_id: number;
  status: string;
  file_name: string;
  checksum: string;
  total_rows: number;
  mapping_suggestion: Mapping;
  preview: Record<string, unknown>[];
  columns: UploadColumnInfo[];
  uploaded_at: string;
}

export interface DatasetListItem {
  id: number;
  name: string;
  source_type: string | null;
  status: string;
  total_rows: number;
  columns: string[];
  version_no: number | null;
  dq_score: number | null;
  processed_at: string | null;
  created_at: string;
}

export interface DatasetVersionInfo {
  id: number;
  version_no: number;
  row_count: number;
  checksum: string | null;
  dq_score: number | null;
  processed_at: string | null;
  created_at: string;
}

export interface DatasetDetail {
  id: number;
  name: string;
  source_type: string | null;
  status: string;
  total_rows: number;
  columns: string[];
  detected_dtypes: Record<string, string>;
  mapping: Mapping;
  required_missing: Record<string, string[]>;
  versions: DatasetVersionInfo[];
}

export interface MapColumnsResult {
  dataset_id: number;
  status: string;
  mapping: Mapping;
  required_missing: Record<string, string[]>;
  unmapped_columns: string[];
  eligible_entities: string[];
}

export interface DqCheck {
  check: string;
  label: string;
  category: string;
  severity: string;
  status: string;
  affected: number;
  total: number;
  samples: unknown[];
}

export interface DqReport {
  score: number;
  blocked: boolean;
  checks: DqCheck[];
  summary: Record<string, unknown>;
  generated_at: string;
}

export interface ValidateResult {
  dataset_id: number;
  status: string;
  score: number;
  blocked: boolean;
  report: DqReport;
  mapping: Mapping;
}

export interface ProcessSummary {
  dataset_id: number;
  status: string;
  rows: number;
  entities_processed: string[];
  inserted: Record<string, number>;
  dq_score: number;
  report: DqReport;
  processed_at: string;
}

export interface QualityResult {
  dataset_id: number;
  status: string;
  score: number | null;
  blocked: boolean;
  report: DqReport | null;
}