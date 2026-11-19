import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { CitationDialog } from "./components/CitationDialog";
import { ErrorBox, usePoll } from "./components/common";
import { Results } from "./components/Results";
import { SearchPanel } from "./components/SearchPanel";
import { SearchStatus } from "./components/SearchStatus";
import { DownloadsView, HistoryView, SavedView, SettingsView } from "./components/Views";
import type { Paper, SearchJob, SearchRequest } from "./types";

type View = "search" | "history" | "saved" | "downloads" | "settings";
const VIEWS: { id: View; label: string }[] = [
  { id: "search", label: "Search" },
  { id: "history", label: "History" },
  { id: "saved", label: "Saved" },
  { id: "downloads", label: "Downloads" },
  { id: "settings", label: "Settings" },
];

function viewFromHash(): View {
  const h = window.location.hash.replace("#", "") as View;
  return VIEWS.some((v) => v.id === h) ? h : "search";
}

export default function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [job, setJob] = useState<SearchJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [citing, setCiting] = useState<Paper | null>(null);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [assistant, setAssistant] = useState(false);
  const [prefill, setPrefill] = useState<SearchRequest | null>(null);
  const lastReq = useRef<SearchRequest | null>(null);

  const refreshHealth = () =>
    Promise.all([api.health(), api.settings()])
      .then(([h, s]) => {
        setBackendOk(true);
        setAssistant(h.assistant_configured && s.preferences.assistant_enabled);
      })
      .catch(() => setBackendOk(false));

  useEffect(() => {
    refreshHealth();
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function go(v: View) {
    window.location.hash = v === "search" ? "" : v;
    setView(v);
  }

  const running = job?.status === "running";
  usePoll(async () => {
    if (!job) return true;
    const j = await api.getSearch(job.id);
    setJob(j);
    return j.status !== "running";
  }, running, 500);

  async function runSearch(req: SearchRequest) {
    setError(null);
    setStarting(true);
    lastReq.current = req;
    try {
      setJob(await api.startSearch(req));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Search could not be started.");
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (job) {
      try {
        setJob(await api.cancelSearch(job.id));
      } catch (e) {
        setError((e as Error).message);
      }
    }
  }

  async function openHistory(id: string) {
    setError(null);
    try {
      const j = await api.getSearch(id);
      setJob(j);
      lastReq.current = j.request;
      setPrefill(j.request);
      go("search");
    } catch (e) {
      setError((e as Error).message);
      go("search");
    }
  }

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>📑</span>
          <span>PaperFinder <b>AI</b></span>
        </div>
        <nav aria-label="Main">
          {VIEWS.map((v) => (
            <button key={v.id} type="button" className={`nav ${view === v.id ? "active" : ""}`}
              aria-current={view === v.id ? "page" : undefined} onClick={() => go(v.id)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>

      <main id="main" className="container">
        {backendOk === false && (
          <ErrorBox onRetry={refreshHealth}>
            The PaperFinder backend is not responding. Start it with <code>start.bat</code> (Windows) or{" "}
            <code>python -m app</code> in the backend folder.
          </ErrorBox>
        )}

        {view === "search" && (
          <>
            <SearchPanel busy={starting || running} assistantAvailable={assistant} lastSearchId={job?.id ?? null}
              onSearch={runSearch} prefill={prefill}
              onClear={() => { setJob(null); setError(null); }} />
            {error && <ErrorBox>{error}</ErrorBox>}
            {job && (
              <SearchStatus job={job} onCancel={cancel}
                onRetry={() => runSearch(lastReq.current ?? job.request)} />
            )}
            {job && <Results papers={job.results} running={running} onCite={setCiting} />}
            {!job && !error && (
              <section className="panel intro">
                <h2>Find a paper you can trust</h2>
                <p>
                  Search by DOI, title or advanced criteria. PaperFinder queries several scholarly sources, merges
                  duplicates, and shows <strong>why</strong> each result is labelled Exact, Probable, Similar or
                  Unverified. It finds lawful open-access PDFs, verifies them before download, and builds citations
                  from structured metadata.
                </p>
                <p className="muted small">
                  Your history and files stay on this computer. Only your search query is sent to the scholarly sources
                  you enable in Settings.
                </p>
              </section>
            )}
          </>
        )}
        {view === "history" && <HistoryView onOpen={openHistory} />}
        {view === "saved" && <SavedView onCite={setCiting} />}
        {view === "downloads" && <DownloadsView />}
        {view === "settings" && <SettingsView onSaved={refreshHealth} />}
      </main>

      <footer className="footer muted small">
        Results come only from the sources searched; coverage is never exhaustive. No paywall or access-control bypass.
      </footer>

      {citing && <CitationDialog paper={citing} onClose={() => setCiting(null)} />}
    </div>
  );
}
