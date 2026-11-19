import { useState } from "react";
import { api, safeHref, sourceLabel } from "../api";
import type { Paper } from "../types";
import { AccessBadge, MatchBadge } from "./common";
import { VersionsPanel } from "./VersionsPanel";

const FIELD_LABELS: Record<string, string> = {
  title: "Title", year: "Year", venue: "Venue", publisher: "Publisher", type: "Type", volume: "Volume",
  issue: "Issue", pages: "Pages", article_number: "Article no.", url: "URL", authors: "Authors", doi: "DOI",
  first_author: "First author",
};

function fmt(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  return v == null ? "–" : String(v);
}

interface Props {
  paper: Paper;
  onCite: (p: Paper) => void;
  onSavedChange?: (id: string, saved: boolean) => void;
}

export function PaperCard({ paper, onCite, onSavedChange }: Props) {
  const [showAbstract, setShowAbstract] = useState(false);
  const [showAllAuthors, setShowAllAuthors] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [saved, setSaved] = useState(!!paper.saved);
  const [saveError, setSaveError] = useState<string | null>(null);

  const authors = paper.authors;
  const shownAuthors = showAllAuthors ? authors : authors.slice(0, 6);
  const doiHref = paper.doi ? `https://doi.org/${encodeURIComponent(paper.doi).replace(/%2F/g, "/")}` : undefined;
  const landing = safeHref(paper.url);
  const abstractId = `abs-${paper.id}`;

  async function toggleSave() {
    setSaveError(null);
    try {
      if (saved) await api.unsave(paper.id);
      else await api.save(paper.id);
      setSaved(!saved);
      onSavedChange?.(paper.id, !saved);
    } catch (e) {
      setSaveError((e as Error).message);
    }
  }

  const meta = [
    paper.venue,
    paper.year ? String(paper.year) : null,
    paper.volume ? `vol. ${paper.volume}${paper.issue ? `(${paper.issue})` : ""}` : null,
    paper.pages ? `pp. ${paper.pages}` : paper.article_number ? `art. ${paper.article_number}` : null,
    paper.publisher,
  ].filter(Boolean);

  return (
    <article className="card" aria-labelledby={`t-${paper.id}`}>
      <div className="card-actions">
        <button type="button" className="btn btn-primary btn-block" aria-expanded={showVersions}
          onClick={() => setShowVersions((v) => !v)}>
          {showVersions ? "Hide full text" : "📄 Find PDF"}
        </button>
        <button type="button" className="btn btn-block" onClick={() => onCite(paper)}>
          ❝ Cite
        </button>
        <button type="button" className={`btn btn-block ${saved ? "btn-saved" : ""}`} aria-pressed={saved}
          onClick={toggleSave}>
          {saved ? "★ Saved" : "☆ Save"}
        </button>
        {saveError && <small className="field-error">{saveError}</small>}
      </div>

      <div className="card-body">
        <div className="badges">
          <MatchBadge label={paper.match.label} />
          <AccessBadge status={paper.access.status} />
          {paper.type && <span className="badge badge-muted">{paper.type.replace(/[-_]/g, " ")}</span>}
          {paper.conflicts.length > 0 && (
            <span className="badge badge-warn" title="Sources disagree on some fields – see Evidence">
              ⚠ metadata conflict
            </span>
          )}
        </div>
        <h3 className="card-title" id={`t-${paper.id}`}>
          {paper.title ?? <em>Untitled record</em>}
        </h3>
        {authors.length > 0 ? (
          <p className="authors">
            {shownAuthors.map((a) => a.name).join(", ")}
            {authors.length > 6 && (
              <button type="button" className="link-btn" onClick={() => setShowAllAuthors((v) => !v)}>
                {showAllAuthors ? " show fewer" : ` +${authors.length - 6} more`}
              </button>
            )}
          </p>
        ) : (
          <p className="authors muted">Authors not available</p>
        )}
        {meta.length > 0 && <p className="meta">{meta.join(" · ")}</p>}
        <p className="ids">
          {paper.doi && (
            <a href={doiHref} target="_blank" rel="noopener noreferrer">DOI: {paper.doi}</a>
          )}
          {paper.arxiv_id && (
            <a href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noopener noreferrer">
              arXiv: {paper.arxiv_id}
            </a>
          )}
          {!paper.doi && landing && (
            <a href={landing} target="_blank" rel="noopener noreferrer">Source page</a>
          )}
          <span className="muted">Sources: {paper.sources.map(sourceLabel).join(", ")}</span>
        </p>

        <div className="abstract">
          <button type="button" className="link-btn" aria-expanded={showAbstract} aria-controls={abstractId}
            onClick={() => setShowAbstract((v) => !v)}>
            {showAbstract ? "▾ Hide abstract" : "▸ Show abstract"}
          </button>
          {showAbstract && (
            <div id={abstractId} className="abstract-body">
              {paper.abstract.state === "available" ? (
                <>
                  <p>{paper.abstract.text}</p>
                  <small className="muted">Abstract source: {sourceLabel(paper.abstract.source ?? "")}</small>
                </>
              ) : paper.abstract.state === "retrieval_failed" ? (
                <p className="muted">
                  Abstract could not be retrieved – {paper.abstract.failed_sources.map(sourceLabel).join(", ")} failed
                  during the search. Try searching again later.
                </p>
              ) : (
                <p className="muted">
                  Abstract unavailable from the sources checked ({paper.abstract.checked_sources.map(sourceLabel).join(", ")}).
                </p>
              )}
            </div>
          )}
        </div>

        <details className="evidence">
          <summary>Evidence &amp; provenance</summary>
          <h4>Why “{paper.match.label}”</h4>
          <ul>{paper.match.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
          {paper.match.warnings.length > 0 && (
            <ul className="warn-list">{paper.match.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>
          )}
          <h4>Access: {paper.access.status}</h4>
          <ul>{paper.access.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
          {paper.conflicts.length > 0 && (
            <>
              <h4>Conflicting metadata</h4>
              {paper.conflicts.map((c) => (
                <div key={c.field} className="conflict">
                  <strong>{FIELD_LABELS[c.field] ?? c.field}</strong> – {c.note}
                  <ul>
                    {c.values.map((v, i) => <li key={i}>{sourceLabel(v.source)}: {fmt(v.value)}</li>)}
                  </ul>
                </div>
              ))}
            </>
          )}
          <h4>Field provenance</h4>
          <table className="prov">
            <thead>
              <tr><th scope="col">Field</th><th scope="col">Value per source</th></tr>
            </thead>
            <tbody>
              {Object.entries(paper.provenance)
                .filter(([f]) => f !== "url")
                .map(([field, vals]) => (
                  <tr key={field}>
                    <th scope="row">{FIELD_LABELS[field] ?? field}</th>
                    <td>
                      {vals.map((v, i) => (
                        <div key={i}>
                          <span className="src-tag">{sourceLabel(v.source)}</span> {fmt(v.value)}
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <small className="muted">Retrieved {new Date(paper.retrieved_at).toLocaleString()}</small>
        </details>

        {showVersions && <VersionsPanel paper={paper} />}
      </div>
    </article>
  );
}
