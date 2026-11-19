import type {
  AssistantSuggestion, Citation, DownloadRecord, HistoryItem, Paper, Preferences, ProviderInfo, SavedItem,
  SearchJob, SearchRequest, SettingsResponse, StorageInfo, VersionsResponse,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Cannot reach the PaperFinder backend. Is it running?", 0);
  }
  if (!res.ok) {
    let detail = `Request failed (HTTP ${res.status}).`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string; version: string; assistant_configured: boolean }>("GET", "/api/health"),
  startSearch: (req: SearchRequest) => request<SearchJob>("POST", "/api/search", req),
  getSearch: (id: string, signal?: AbortSignal) => request<SearchJob>("GET", `/api/search/${id}`, undefined, signal),
  cancelSearch: (id: string) => request<SearchJob>("POST", `/api/search/${id}/cancel`, {}),
  getPaper: (id: string) => request<Paper>("GET", `/api/papers/${id}`),
  discoverVersions: (id: string) => request<VersionsResponse>("POST", `/api/papers/${id}/versions`, {}),
  startDownload: (paper_id: string, candidate_id: string) =>
    request<DownloadRecord>("POST", "/api/downloads", { paper_id, candidate_id }),
  getDownload: (id: string) => request<DownloadRecord>("GET", `/api/downloads/${id}`),
  cancelDownload: (id: string) => request<DownloadRecord>("POST", `/api/downloads/${id}/cancel`, {}),
  listDownloads: () => request<DownloadRecord[]>("GET", "/api/downloads"),
  deleteDownloadFile: (id: string) => request<DownloadRecord>("DELETE", `/api/downloads/${id}/file`),
  deleteDownload: (id: string, deleteFile: boolean) =>
    request<{ record_deleted: boolean; file_deleted: boolean }>("DELETE", `/api/downloads/${id}?delete_file=${deleteFile}`),
  fileUrl: (id: string) => `/api/downloads/${id}/file`,
  citation: (paper_id: string, style: string, overrides: Record<string, unknown> = {}) =>
    request<Citation>("POST", "/api/citations", { paper_id, style, overrides }),
  history: () => request<HistoryItem[]>("GET", "/api/history?limit=200"),
  deleteHistory: (id: string) => request<{ deleted: number }>("DELETE", `/api/history/${id}`),
  clearHistory: () => request<{ deleted: number }>("DELETE", "/api/history"),
  saved: () => request<SavedItem[]>("GET", "/api/saved"),
  save: (paper_id: string, note?: string) => request<{ saved: boolean }>("POST", "/api/saved", { paper_id, note }),
  unsave: (paper_id: string) => request<{ saved: boolean }>("DELETE", `/api/saved/${paper_id}`),
  settings: () => request<SettingsResponse>("GET", "/api/settings"),
  saveSettings: (p: Preferences) => request<{ preferences: Preferences }>("PUT", "/api/settings", p),
  providers: () => request<ProviderInfo[]>("GET", "/api/providers"),
  storage: () => request<StorageInfo>("GET", "/api/storage"),
  assistant: (text: string, previous_search_id?: string | null) =>
    request<AssistantSuggestion>("POST", "/api/assistant/interpret", { text, previous_search_id }),
};

export const SOURCE_LABELS: Record<string, string> = {
  crossref: "Crossref",
  openalex: "OpenAlex",
  semanticscholar: "Semantic Scholar",
  arxiv: "arXiv",
  unpaywall: "Unpaywall",
};

export const sourceLabel = (s: string) => SOURCE_LABELS[s] ?? s;

export function formatBytes(n?: number | null): string {
  if (n == null) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Only allow http(s) links from provider data to be rendered as hrefs (R-30). */
export function safeHref(url?: string | null): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : undefined;
  } catch {
    return undefined;
  }
}
