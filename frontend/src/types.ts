// Mirrors backend/app/models.py

export type SearchMode = "doi_url" | "title" | "advanced";
export type MatchLabel = "Exact" | "Probable" | "Similar" | "Unverified";
export type AccessStatus = "Open Access" | "Closed" | "Alternative Version Found" | "Unknown";
export type VersionLabel = "Version of Record" | "Accepted Manuscript" | "Preprint" | "Unknown";
export type VerificationState = "Not Checked" | "Verified" | "Verification Limited" | "Rejected" | "Failed";
export type SourceStatus =
  | "pending" | "running" | "success" | "no_results" | "failed" | "timeout" | "rate_limited" | "skipped" | "cancelled";
export type JobStatus = "running" | "completed" | "partial" | "failed" | "cancelled";

export interface Author { name: string; given?: string | null; family?: string | null; orcid?: string | null }
export interface ProvValue { source: string; value: unknown }
export interface Conflict { field: string; values: ProvValue[]; note: string }

export interface VersionCandidate {
  id: string;
  url: string;
  is_pdf_url: boolean;
  landing_page_url?: string | null;
  version: VersionLabel;
  source: string;
  host_type?: string | null;
  license?: string | null;
  is_oa?: boolean | null;
  evidence: string[];
  verification: VerificationState;
  verification_detail: string[];
  checked_at: string;
}

export interface AbstractRecord {
  state: "available" | "unavailable" | "retrieval_failed";
  text?: string | null;
  source?: string | null;
  checked_sources: string[];
  failed_sources: string[];
  retrieved_at: string;
}

export interface MatchInfo { label: MatchLabel; score: number; evidence: string[]; warnings: string[] }
export interface AccessInfo { status: AccessStatus; evidence: string[]; checked_at: string }

export interface Paper {
  id: string;
  doi?: string | null;
  arxiv_id?: string | null;
  title?: string | null;
  authors: Author[];
  year?: number | null;
  venue?: string | null;
  publisher?: string | null;
  type?: string | null;
  volume?: string | null;
  issue?: string | null;
  pages?: string | null;
  article_number?: string | null;
  url?: string | null;
  abstract: AbstractRecord;
  sources: string[];
  source_ids: Record<string, string>;
  provenance: Record<string, ProvValue[]>;
  conflicts: Conflict[];
  match: MatchInfo;
  access: AccessInfo;
  candidates: VersionCandidate[];
  retrieved_at: string;
  saved?: boolean;
}

export interface SourceOutcome {
  provider: string;
  status: SourceStatus;
  count: number;
  duration_ms?: number | null;
  error?: string | null;
  notes: string[];
  attempts: number;
}

export interface SearchRequest {
  mode: SearchMode;
  query?: string | null;
  title?: string | null;
  authors?: string[];
  keywords?: string | null;
  year_from?: number | null;
  year_to?: number | null;
  venue?: string | null;
}

export interface SearchJob {
  id: string;
  mode: SearchMode;
  request: SearchRequest;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  sources: SourceOutcome[];
  results: Paper[];
  notes: string[];
  cached: boolean;
}

export interface VersionsResponse {
  paper_id: string;
  access: AccessInfo;
  candidates: VersionCandidate[];
  sources: SourceOutcome[];
}

export interface DownloadRecord {
  id: string;
  paper_id: string;
  candidate_id: string;
  source_url: string;
  final_url?: string | null;
  status: "queued" | "downloading" | "validating" | "ready" | "blocked" | "failed" | "cancelled";
  verification: VerificationState;
  verification_detail: string[];
  filename?: string | null;
  size_bytes?: number | null;
  bytes_received: number;
  error?: string | null;
  file_deleted: boolean;
  file_available?: boolean;
  paper_title?: string | null;
  created_at: string;
}

export interface CitationSegment { text: string; italic: boolean }
export interface Citation { style: string; text: string; segments: CitationSegment[]; warnings: string[] }

export interface HistoryItem {
  id: string;
  mode: SearchMode;
  summary: string;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  result_count: number;
}

export interface SavedItem { paper: Paper; note?: string | null; saved_at: string }

export interface Preferences {
  enabled_providers: string[];
  contact_email: string;
  search_budget_seconds: number;
  provider_timeout_seconds: number;
  max_results_per_provider: number;
  max_retries: number;
  max_pdf_mb: number;
  download_timeout_seconds: number;
  assistant_enabled: boolean;
}

export interface SettingsResponse {
  preferences: Preferences;
  credentials: { openalex_api_key: boolean; s2_api_key: boolean; anthropic_api_key: boolean };
  data_dir: string;
}

export interface ProviderInfo {
  name: string;
  label: string;
  description: string;
  homepage: string;
  enabled: boolean;
  unavailable_reason?: string | null;
  paused: boolean;
  supports_search: boolean;
}

export interface StorageInfo {
  downloads_dir: string;
  download_files: number;
  download_bytes: number;
  database_bytes: number;
  disk_free_bytes: number;
  searches: number;
  papers: number;
  saved: number;
  downloads: number;
}

export interface AssistantSuggestion {
  mode: SearchMode;
  query?: string | null;
  title?: string | null;
  authors: string[];
  keywords?: string | null;
  year_from?: number | null;
  year_to?: number | null;
  venue?: string | null;
  explanation: string;
  refinements: string[];
}
