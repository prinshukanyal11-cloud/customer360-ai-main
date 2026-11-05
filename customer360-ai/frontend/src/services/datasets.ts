import client from "@/services/api";
import type {
  DatasetDetail,
  DatasetListItem,
  MapColumnsResult,
  Mapping,
  ProcessSummary,
  QualityResult,
  UploadResult,
  ValidateResult,
} from "@/types";

export interface DatasetListResponse {
  items: DatasetListItem[];
  total: number;
  page: number;
  page_size: number;
}

async function listDatasets(status?: string): Promise<DatasetListResponse> {
  const res = await client.get<DatasetListResponse>("/datasets", {
    params: status ? { status } : undefined,
  });
  return res.data;
}

async function getDataset(id: number): Promise<DatasetDetail> {
  const res = await client.get<DatasetDetail>(`/datasets/${id}`);
  return res.data;
}

async function uploadDataset(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await client.post<UploadResult>("/datasets/upload", form);
  return res.data;
}

async function mapColumns(datasetId: number, mapping: Mapping): Promise<MapColumnsResult> {
  const res = await client.post<MapColumnsResult>(`/datasets/${datasetId}/map-columns`, { mapping });
  return res.data;
}

async function mappingSuggestion(datasetId: number): Promise<{
  dataset_id: number;
  entities: Mapping;
  required_missing: Record<string, string[]>;
  unmapped_columns: string[];
  columns: string[];
}> {
  const res = await client.get(`/datasets/${datasetId}/mapping-suggestion`);
  return res.data;
}

async function validateDataset(datasetId: number): Promise<ValidateResult> {
  const res = await client.post<ValidateResult>(`/datasets/${datasetId}/validate`);
  return res.data;
}

async function processDataset(datasetId: number, forceValidation = false): Promise<ProcessSummary> {
  const res = await client.post<ProcessSummary>(`/datasets/${datasetId}/process`, undefined, {
    params: forceValidation ? { force_validation: true } : undefined,
  });
  return res.data;
}

async function getDatasetQuality(datasetId: number): Promise<QualityResult> {
  const res = await client.get<QualityResult>(`/datasets/${datasetId}/quality`);
  return res.data;
}

export const datasetsService = {
  listDatasets,
  getDataset,
  uploadDataset,
  mapColumns,
  mappingSuggestion,
  validateDataset,
  processDataset,
  getDatasetQuality,
};