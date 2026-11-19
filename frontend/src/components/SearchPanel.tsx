import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { AssistantSuggestion, SearchMode, SearchRequest } from "../types";
import { ErrorBox, Spinner } from "./common";

type Tab = SearchMode | "ai";

const DOI_RE = /^(?:doi:\s*|https?:\/\/(?:dx\.)?doi\.org\/)?10\.\d{4,9}\/\S+$/i;
const ARXIV_RE = /^(?:arxiv:)?(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})$/i;

export function validateDoiUrl(v: string): string | null {
  const s = v.trim();
  if (!s) return "Enter a DOI, arXiv ID or paper URL.";
  if (DOI_RE.test(s) || ARXIV_RE.test(s)) return null;
  if (/^https?:\/\//i.test(s)) {
    try {
      new URL(s);
      return null;
    } catch {
      return "That URL is not valid.";
    }
  }
  if (/^10\./.test(s)) return "This looks like a DOI but is incomplete (expected 10.xxxx/suffix).";
  return "Enter a DOI (e.g. 10.1038/nature14539), an arXiv ID (e.g. 1706.03762) or an http(s) URL.";
}

interface Props {
  busy: boolean;
  assistantAvailable: boolean;
  lastSearchId: string | null;
  onSearch: (req: SearchRequest) => void;
  onClear: () => void;
  prefill?: SearchRequest | null;
}

export function SearchPanel({ busy, assistantAvailable, lastSearchId, onSearch, onClear, prefill }: Props) {
  const [tab, setTab] = useState<Tab>("doi_url");
  const [doi, setDoi] = useState("");
  const [title, setTitle] = useState("");
  const [adv, setAdv] = useState({ title: "", authors: "", keywords: "", venue: "", year_from: "", year_to: "" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!prefill) return;
    applyRequest(prefill);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  function applyRequest(r: SearchRequest) {
    setError(null);
    if (r.mode === "doi_url") {
      setDoi(r.query ?? "");
    } else if (r.mode === "title") {
      setTitle(r.query ?? "");
    } else {
      setAdv({
        title: r.title ?? "",
        authors: (r.authors ?? []).join("; "),
        keywords: r.keywords ?? "",
        venue: r.venue ?? "",
        year_from: r.year_from ? String(r.year_from) : "",
        year_to: r.year_to ? String(r.year_to) : "",
      });
    }
    setTab(r.mode);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (tab === "doi_url") {
      const msg = validateDoiUrl(doi);
      if (msg) return setError(msg);
      onSearch({ mode: "doi_url", query: doi.trim() });
    } else if (tab === "title") {
      if (title.trim().length < 3) return setError("Enter at least 3 characters of the title.");
      onSearch({ mode: "title", query: title.trim() });
    } else if (tab === "advanced") {
      const yf = adv.year_from ? Number(adv.year_from) : null;
      const yt = adv.year_to ? Number(adv.year_to) : null;
      const authors = adv.authors.split(/[;\n]/).map((a) => a.trim()).filter(Boolean);
      if (!adv.title.trim() && !adv.keywords.trim() && !adv.venue.trim() && authors.length === 0)
        return setError("Enter at least a title, author, keyword or venue.");
      for (const y of [yf, yt]) {
        if (y !== null && (!Number.isInteger(y) || y < 1000 || y > 2100)) return setError("Years must be between 1000 and 2100.");
      }
      if (yf && yt && yf > yt) return setError("'Year from' must not be after 'Year to'.");
      onSearch({
        mode: "advanced",
        title: adv.title.trim() || null,
        authors,
        keywords: adv.keywords.trim() || null,
        venue: adv.venue.trim() || null,
        year_from: yf,
        year_to: yt,
      });
    }
  }

  function clear() {
    setDoi("");
    setTitle("");
    setAdv({ title: "", authors: "", keywords: "", venue: "", year_from: "", year_to: "" });
    setError(null);
    onClear();
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "doi_url", label: "DOI / URL" },
    { id: "title", label: "Paper title" },
    { id: "advanced", label: "Advanced" },
    { id: "ai", label: "✨ Ask AI" },
  ];

  return (
    <section className="panel search-panel" aria-label="Search">
      <div className="tabs" role="tablist" aria-label="Search mode">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className={`tab ${tab === t.id ? "active" : ""}`}
            onClick={() => {
              setTab(t.id);
              setError(null);
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "ai" ? (
        <AssistantForm
          available={assistantAvailable}
          lastSearchId={lastSearchId}
          onUse={(s) => applyRequest(s)}
        />
      ) : (
        <form onSubmit={submit} noValidate id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab === "doi_url" && (
            <div className="field">
              <label htmlFor="q-doi">DOI, arXiv ID or paper URL</label>
              <input
                id="q-doi"
                value={doi}
                onChange={(e) => setDoi(e.target.value)}
                placeholder="10.1038/nature14539  ·  arXiv:1706.03762  ·  https://www.nature.com/articles/…"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={!!error}
                aria-describedby="q-doi-help"
              />
              <small id="q-doi-help">A DOI gives the most reliable identity match.</small>
            </div>
          )}
          {tab === "title" && (
            <div className="field">
              <label htmlFor="q-title">Exact or partial paper title</label>
              <input
                id="q-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Attention is all you need"
                aria-invalid={!!error}
                aria-describedby="q-title-help"
              />
              <small id="q-title-help">
                Title alone can't confirm identity. Use Advanced to add an author or year for an Exact match.
              </small>
            </div>
          )}
          {tab === "advanced" && (
            <div className="grid-2">
              <div className="field span-2">
                <label htmlFor="a-title">Title (optional)</label>
                <input id="a-title" value={adv.title} onChange={(e) => setAdv({ ...adv, title: e.target.value })}
                  placeholder="Full or partial title" />
              </div>
              <div className="field">
                <label htmlFor="a-authors">Authors</label>
                <input id="a-authors" value={adv.authors} onChange={(e) => setAdv({ ...adv, authors: e.target.value })}
                  placeholder="LeCun; Hinton" aria-describedby="a-authors-help" />
                <small id="a-authors-help">Separate names with semicolons.</small>
              </div>
              <div className="field">
                <label htmlFor="a-keywords">Keywords</label>
                <input id="a-keywords" value={adv.keywords}
                  onChange={(e) => setAdv({ ...adv, keywords: e.target.value })} placeholder="graph neural networks" />
              </div>
              <div className="field">
                <label htmlFor="a-venue">Journal / conference</label>
                <input id="a-venue" value={adv.venue} onChange={(e) => setAdv({ ...adv, venue: e.target.value })}
                  placeholder="Nature, NeurIPS…" />
              </div>
              <div className="field year-range">
                <span className="label" id="a-years">Year range</span>
                <div className="row" role="group" aria-labelledby="a-years">
                  <input aria-label="Year from" inputMode="numeric" value={adv.year_from}
                    onChange={(e) => setAdv({ ...adv, year_from: e.target.value.replace(/\D/g, "").slice(0, 4) })}
                    placeholder="From" />
                  <span aria-hidden>–</span>
                  <input aria-label="Year to" inputMode="numeric" value={adv.year_to}
                    onChange={(e) => setAdv({ ...adv, year_to: e.target.value.replace(/\D/g, "").slice(0, 4) })}
                    placeholder="To" />
                </div>
              </div>
            </div>
          )}
          {error && <p className="field-error" role="alert">{error}</p>}
          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? <><Spinner label="Searching" /> Searching…</> : "Search"}
            </button>
            <button type="button" className="btn" onClick={clear}>
              Clear
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function AssistantForm({
  available, lastSearchId, onUse,
}: { available: boolean; lastSearchId: string | null; onUse: (r: SearchRequest) => void }) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<AssistantSuggestion | null>(null);
  const [useContext, setUseContext] = useState(false);

  async function ask(e: FormEvent) {
    e.preventDefault();
    if (text.trim().length < 3) return setError("Describe what you're looking for.");
    setLoading(true);
    setError(null);
    setSuggestion(null);
    try {
      setSuggestion(await api.assistant(text.trim(), useContext ? lastSearchId : null));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The assistant failed.");
    } finally {
      setLoading(false);
    }
  }

  if (!available) {
    return (
      <div className="ai-off" id="panel-ai" role="tabpanel" aria-labelledby="tab-ai">
        <p>
          <strong>The AI assistant is not set up.</strong> It is optional – all search modes work without it.
        </p>
        <p className="muted">
          To enable it, add <code>ANTHROPIC_API_KEY=…</code> to the <code>.env</code> file and restart PaperFinder.
          It can also be turned off in Settings.
        </p>
      </div>
    );
  }

  const req: SearchRequest | null = suggestion
    ? {
        mode: suggestion.mode,
        query: suggestion.query,
        title: suggestion.title,
        authors: suggestion.authors,
        keywords: suggestion.keywords,
        venue: suggestion.venue,
        year_from: suggestion.year_from,
        year_to: suggestion.year_to,
      }
    : null;

  return (
    <form onSubmit={ask} id="panel-ai" role="tabpanel" aria-labelledby="tab-ai">
      <div className="field">
        <label htmlFor="ai-text">Describe the paper(s) you need – in any language</label>
        <textarea
          id="ai-text"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          placeholder="e.g. 2017 ka Google wala transformer paper, ya: recent reviews on CRISPR off-target effects since 2020"
        />
        <small>
          The assistant only turns your words into search fields for you to review. It never invents paper details –
          results always come from the scholarly sources. Your text is sent to Anthropic's API.
        </small>
      </div>
      {lastSearchId && (
        <label className="check">
          <input type="checkbox" checked={useContext} onChange={(e) => setUseContext(e.target.checked)} />
          Use my last search to suggest refinements (sends its query and result titles)
        </label>
      )}
      {error && <ErrorBox>{error}</ErrorBox>}
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? <><Spinner label="Thinking" /> Interpreting…</> : "Interpret request"}
        </button>
      </div>
      {suggestion && req && (
        <div className="suggestion" aria-live="polite">
          <h3>Suggested search <span className="badge ai-badge">AI suggestion – please review</span></h3>
          <p>{suggestion.explanation}</p>
          <dl className="kv">
            <dt>Mode</dt>
            <dd>{{ doi_url: "DOI / URL", title: "Title", advanced: "Advanced" }[suggestion.mode]}</dd>
            {suggestion.query && (<><dt>Query</dt><dd>{suggestion.query}</dd></>)}
            {suggestion.title && (<><dt>Title</dt><dd>{suggestion.title}</dd></>)}
            {suggestion.authors.length > 0 && (<><dt>Authors</dt><dd>{suggestion.authors.join("; ")}</dd></>)}
            {suggestion.keywords && (<><dt>Keywords</dt><dd>{suggestion.keywords}</dd></>)}
            {suggestion.venue && (<><dt>Venue</dt><dd>{suggestion.venue}</dd></>)}
            {(suggestion.year_from || suggestion.year_to) && (
              <><dt>Years</dt><dd>{suggestion.year_from ?? "…"} – {suggestion.year_to ?? "…"}</dd></>
            )}
          </dl>
          {suggestion.refinements.length > 0 && (
            <>
              <h4>Tips to refine</h4>
              <ul>{suggestion.refinements.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </>
          )}
          <button type="button" className="btn btn-primary" onClick={() => onUse(req)}>
            Review in search form →
          </button>
        </div>
      )}
    </form>
  );
}
