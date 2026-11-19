import { sourceLabel } from "../api";
import type { SearchJob, SourceOutcome } from "../types";
import { Spinner } from "./common";

const STATUS_TEXT: Record<SourceOutcome["status"], string> = {
  pending: "waiting",
  running: "searching…",
  success: "found",
  no_results: "no results",
  failed: "failed",
  timeout: "timed out",
  rate_limited: "rate-limited",
  skipped: "skipped",
  cancelled: "cancelled",
};

const JOB_TEXT: Record<SearchJob["status"], string> = {
  running: "Searching sources…",
  completed: "Search complete",
  partial: "Partial results – some sources failed",
  failed: "Search failed",
  cancelled: "Search cancelled – showing results received so far",
};

export function SearchStatus({ job, onCancel, onRetry }: { job: SearchJob; onCancel: () => void; onRetry: () => void }) {
  const running = job.status === "running";
  const failedSources = job.sources.filter((s) => ["failed", "timeout", "rate_limited"].includes(s.status));
  return (
    <section className={`panel status status-${job.status}`} aria-live="polite" aria-label="Source coverage">
      <div className="status-head">
        <strong>
          {running && <Spinner label="Searching" />} {JOB_TEXT[job.status]}
        </strong>
        {job.cached && <span className="badge badge-muted" title="Loaded from local history">From history · {new Date(job.started_at).toLocaleString()}</span>}
        <div className="spacer" />
        {running && (
          <button type="button" className="btn btn-small" onClick={onCancel}>
            Cancel
          </button>
        )}
        {!running && (failedSources.length > 0 || job.cached) && (
          <button type="button" className="btn btn-small" onClick={onRetry}>
            {job.cached ? "Search again (refresh)" : "Retry search"}
          </button>
        )}
      </div>
      <ul className="sources">
        {job.sources.map((s) => (
          <li key={s.provider} className={`src src-${s.status}`} title={[s.error, ...s.notes].filter(Boolean).join("\n")}>
            <span className="src-dot" aria-hidden />
            <span className="src-name">{sourceLabel(s.provider)}</span>
            <span className="src-state">
              {STATUS_TEXT[s.status]}
              {s.status === "success" ? ` (${s.count})` : ""}
              {s.duration_ms != null && s.status !== "skipped" ? ` · ${(s.duration_ms / 1000).toFixed(1)}s` : ""}
            </span>
          </li>
        ))}
      </ul>
      {(failedSources.length > 0 || job.sources.some((s) => s.notes.length)) && (
        <details className="source-details">
          <summary>Source details</summary>
          <ul>
            {job.sources.flatMap((s) =>
              [s.error, ...s.notes].filter(Boolean).map((m, i) => (
                <li key={s.provider + i}>
                  <strong>{sourceLabel(s.provider)}:</strong> {m}
                </li>
              )),
            )}
          </ul>
        </details>
      )}
      {job.notes.length > 0 && (
        <ul className="job-notes">
          {job.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}
    </section>
  );
}
