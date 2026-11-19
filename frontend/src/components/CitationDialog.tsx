import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Citation, Paper } from "../types";
import { ErrorBox, Spinner, downloadText, useCopy } from "./common";

const STYLES = [
  { id: "apa7", label: "APA 7" },
  { id: "ieee", label: "IEEE" },
  { id: "bibtex", label: "BibTeX" },
] as const;

type Overrides = {
  title: string; authors: string; year: string; venue: string; volume: string; issue: string; pages: string; doi: string;
};

function initialOverrides(p: Paper): Overrides {
  return {
    title: p.title ?? "",
    authors: p.authors.map((a) => a.name).join("\n"),
    year: p.year ? String(p.year) : "",
    venue: p.venue ?? "",
    volume: p.volume ?? "",
    issue: p.issue ?? "",
    pages: p.pages ?? "",
    doi: p.doi ?? "",
  };
}

export function CitationDialog({ paper, onClose }: { paper: Paper; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [style, setStyle] = useState<(typeof STYLES)[number]["id"]>("apa7");
  const [cit, setCit] = useState<Citation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [ov, setOv] = useState<Overrides>(() => initialOverrides(paper));
  const [applied, setApplied] = useState<Record<string, unknown>>({});
  const [copy, copied] = useCopy();

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .citation(paper.id, style, applied)
      .then((c) => !cancelled && setCit(c))
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [paper.id, style, applied]);

  function applyEdits() {
    setApplied({
      title: ov.title,
      authors: ov.authors.split("\n").map((s) => s.trim()).filter(Boolean),
      year: ov.year,
      venue: ov.venue,
      volume: ov.volume,
      issue: ov.issue,
      pages: ov.pages,
      doi: ov.doi,
    });
  }

  async function exportBib() {
    const c = style === "bibtex" ? cit : await api.citation(paper.id, "bibtex", applied);
    if (c) downloadText(`${(paper.doi ?? paper.id).replace(/[^A-Za-z0-9]+/g, "_")}.bib`, c.text + "\n", "application/x-bibtex");
  }

  return (
    <dialog ref={ref} className="dialog" aria-labelledby="cite-title" onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current?.close()}>
      <div className="dialog-inner">
        <header className="dialog-head">
          <h2 id="cite-title">Cite this paper</h2>
          <button type="button" className="btn btn-small" onClick={() => ref.current?.close()} aria-label="Close">✕</button>
        </header>
        <p className="muted small">{paper.title}</p>
        <div className="tabs" role="tablist" aria-label="Citation style">
          {STYLES.map((s) => (
            <button key={s.id} type="button" role="tab" aria-selected={style === s.id}
              className={`tab ${style === s.id ? "active" : ""}`} onClick={() => setStyle(s.id)}>
              {s.label}
            </button>
          ))}
        </div>
        <div className="citation-box" aria-live="polite" aria-busy={loading}>
          {loading && !cit && <Spinner />}
          {error && <ErrorBox>{error}</ErrorBox>}
          {cit &&
            (style === "bibtex" ? (
              <pre className="bibtex">{cit.text}</pre>
            ) : (
              <p className="citation-text">
                {cit.segments.map((s, i) => (s.italic ? <i key={i}>{s.text}</i> : <span key={i}>{s.text}</span>))}
              </p>
            ))}
        </div>
        {cit && cit.warnings.length > 0 && (
          <ul className="warn-list small">{cit.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>
        )}
        <div className="actions">
          <button type="button" className="btn btn-primary" disabled={!cit} onClick={() => cit && copy(cit.text)}>
            {copied ? "✓ Copied" : "Copy"}
          </button>
          <button type="button" className="btn" onClick={exportBib}>Export .bib</button>
          <button type="button" className="btn" aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
            {editing ? "Hide editor" : "Correct details"}
          </button>
        </div>
        {editing && (
          <form className="cite-edit" onSubmit={(e) => { e.preventDefault(); applyEdits(); }}>
            <p className="small muted">Corrections apply to this citation only; the stored record is not changed.</p>
            <div className="grid-2">
              <div className="field span-2">
                <label htmlFor="ce-title">Title</label>
                <input id="ce-title" value={ov.title} onChange={(e) => setOv({ ...ov, title: e.target.value })} />
              </div>
              <div className="field span-2">
                <label htmlFor="ce-authors">Authors (one per line)</label>
                <textarea id="ce-authors" rows={3} value={ov.authors} onChange={(e) => setOv({ ...ov, authors: e.target.value })} />
              </div>
              {(["year", "venue", "volume", "issue", "pages", "doi"] as const).map((f) => (
                <div className="field" key={f}>
                  <label htmlFor={`ce-${f}`}>{f === "venue" ? "Journal / venue" : f === "doi" ? "DOI" : f[0].toUpperCase() + f.slice(1)}</label>
                  <input id={`ce-${f}`} value={ov[f]} onChange={(e) => setOv({ ...ov, [f]: e.target.value })} />
                </div>
              ))}
            </div>
            <div className="actions">
              <button type="submit" className="btn btn-primary">Update citation</button>
              <button type="button" className="btn" onClick={() => { setOv(initialOverrides(paper)); setApplied({}); }}>
                Reset
              </button>
            </div>
          </form>
        )}
      </div>
    </dialog>
  );
}
