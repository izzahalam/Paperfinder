import { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatDate } from "../api";
import type {
  DownloadRecord, HistoryItem, Paper, Preferences, ProviderInfo, SavedItem, SettingsResponse, StorageInfo,
} from "../types";
import { ConfirmButton, ErrorBox, Notice, Spinner, VerificationBadge } from "./common";
import { PaperCard } from "./PaperCard";

function useLoad<T>(fn: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  return { data, error, loading, load, setData };
}

const MODE_LABEL = { doi_url: "DOI/URL", title: "Title", advanced: "Advanced" } as const;

// ------------------------------------------------------------------ History

export function HistoryView({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error, loading, load } = useLoad(api.history);
  const [msg, setMsg] = useState<string | null>(null);

  async function remove(id: string) {
    try {
      await api.deleteHistory(id);
      setMsg("History entry deleted.");
      load();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  async function clearAll() {
    try {
      const r = await api.clearHistory();
      setMsg(`Deleted ${r.deleted} history entr${r.deleted === 1 ? "y" : "ies"}. Saved papers and downloads were kept.`);
      load();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  return (
    <section className="panel">
      <div className="toolbar">
        <h2>Search history</h2>
        <div className="spacer" />
        {data && data.length > 0 && (
          <ConfirmButton label="Delete all history" confirmLabel="Click again to delete all" onConfirm={clearAll} />
        )}
      </div>
      <p className="muted small">Stored only on this computer. Opening an entry shows the cached results; you can refresh them.</p>
      {msg && <Notice>{msg}</Notice>}
      {loading && <Spinner />}
      {error && <ErrorBox onRetry={load}>{error}</ErrorBox>}
      {data && data.length === 0 && <p className="muted">No searches yet.</p>}
      {data && data.length > 0 && (
        <ul className="list">
          {data.map((h: HistoryItem) => (
            <li key={h.id} className="list-row">
              <button type="button" className="link-btn list-main" onClick={() => onOpen(h.id)}>
                <span className="badge badge-muted">{MODE_LABEL[h.mode]}</span> {h.summary || "(empty query)"}
              </button>
              <span className="muted small">
                {h.result_count} result{h.result_count !== 1 ? "s" : ""} · {h.status} · {formatDate(h.started_at)}
              </span>
              <ConfirmButton label="Delete" confirmLabel="Confirm" onConfirm={() => remove(h.id)} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ Saved

export function SavedView({ onCite }: { onCite: (p: Paper) => void }) {
  const { data, error, loading, load } = useLoad(api.saved);
  return (
    <section>
      <div className="panel">
        <h2>Saved papers</h2>
        <p className="muted small">Saved papers are kept even if you clear your search history.</p>
        {loading && <Spinner />}
        {error && <ErrorBox onRetry={load}>{error}</ErrorBox>}
        {data && data.length === 0 && <p className="muted">No saved papers yet. Use ☆ Save on any result.</p>}
      </div>
      {data && data.length > 0 && (
        <div className="cards">
          {data.map((s: SavedItem) => (
            <div key={s.paper.id}>
              <p className="muted small saved-meta">
                Saved {formatDate(s.saved_at)}{s.note ? ` – ${s.note}` : ""}
              </p>
              <PaperCard paper={{ ...s.paper, saved: true }} onCite={onCite}
                onSavedChange={(_id, saved) => !saved && load()} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ Downloads

export function DownloadsView() {
  const { data, error, loading, load } = useLoad(api.listDownloads);
  const [msg, setMsg] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      setMsg(ok);
    } catch (e) {
      setMsg((e as Error).message);
    }
    load();
  }

  return (
    <section className="panel">
      <div className="toolbar">
        <h2>Downloads</h2>
        <div className="spacer" />
        <button type="button" className="btn btn-small" onClick={load}>Refresh</button>
      </div>
      <p className="muted small">
        “Delete file” removes the PDF from disk but keeps the record. “Delete record” removes the entry (and the file
        only if you choose so).
      </p>
      {msg && <Notice>{msg}</Notice>}
      {loading && <Spinner />}
      {error && <ErrorBox onRetry={load}>{error}</ErrorBox>}
      {data && data.length === 0 && <p className="muted">No downloads yet.</p>}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table className="dl-table">
            <thead>
              <tr>
                <th scope="col">Paper / file</th>
                <th scope="col">Status</th>
                <th scope="col">Size</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d: DownloadRecord) => (
                <tr key={d.id}>
                  <td>
                    <div>{d.paper_title ?? d.paper_id}</div>
                    <div className="muted small">{d.filename ?? d.source_url}</div>
                    {d.error && <div className="small warn-text">{d.error}</div>}
                    <div className="muted small">{formatDate(d.created_at)}</div>
                  </td>
                  <td>
                    <div>{d.status}{d.file_deleted ? " (file deleted)" : ""}</div>
                    <VerificationBadge state={d.verification} />
                  </td>
                  <td>{formatBytes(d.size_bytes)}</td>
                  <td className="row-actions">
                    {d.file_available && (
                      <a className="btn btn-small" href={api.fileUrl(d.id)} download={d.filename ?? undefined}>⬇ Save</a>
                    )}
                    {d.file_available && (
                      <ConfirmButton label="Delete file" confirmLabel="Confirm"
                        onConfirm={() => act(() => api.deleteDownloadFile(d.id), "File deleted from disk; record kept.")} />
                    )}
                    <ConfirmButton label="Delete record" confirmLabel="Confirm (keeps file)"
                      onConfirm={() => act(() => api.deleteDownload(d.id, false), "Record deleted; any file was kept.")} />
                    {d.file_available && (
                      <ConfirmButton label="Delete both" confirmLabel="Confirm"
                        onConfirm={() => act(() => api.deleteDownload(d.id, true), "Record and file deleted.")} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ Settings

export function SettingsView({ onSaved }: { onSaved: () => void }) {
  const settings = useLoad(api.settings);
  const providers = useLoad(api.providers);
  const storage = useLoad(api.storage);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [msg, setMsg] = useState<{ kind: "info" | "warn"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settings.data) setPrefs(settings.data.preferences);
  }, [settings.data]);

  if (settings.loading || !prefs) return <section className="panel"><Spinner /></section>;
  if (settings.error) return <ErrorBox onRetry={settings.load}>{settings.error}</ErrorBox>;
  const s = settings.data as SettingsResponse;

  function toggle(name: string, on: boolean) {
    setPrefs((p) => p && { ...p, enabled_providers: on ? [...p.enabled_providers, name] : p.enabled_providers.filter((x) => x !== name) });
  }
  async function save() {
    if (!prefs) return;
    setSaving(true);
    setMsg(null);
    try {
      await api.saveSettings(prefs);
      setMsg({ kind: "info", text: "Settings saved." });
      providers.load();
      onSaved();
    } catch (e) {
      setMsg({ kind: "warn", text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }
  const num = (k: keyof Preferences, v: string) => setPrefs({ ...prefs, [k]: Number(v) });

  return (
    <section className="settings">
      <div className="panel">
        <h2>Sources</h2>
        <p className="muted small">Each enabled source receives your search query. Only reviewed scholarly APIs are available.</p>
        <ul className="list">
          {(providers.data ?? []).map((p: ProviderInfo) => (
            <li key={p.name} className="list-row">
              <label className="check list-main">
                <input type="checkbox" checked={prefs.enabled_providers.includes(p.name)}
                  onChange={(e) => toggle(p.name, e.target.checked)} />
                <span>
                  <strong>{p.label}</strong> – {p.description}
                  {p.unavailable_reason && <span className="small warn-text"> {p.unavailable_reason}</span>}
                  {p.paused && <span className="small warn-text"> Temporarily paused after repeated failures.</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      <div className="panel">
        <h2>Contact email (optional)</h2>
        <p className="muted small">
          Sent to Crossref and OpenAlex for their “polite” service pools, and required by Unpaywall. Leave empty to send
          no personal information (Unpaywall will then be skipped).
        </p>
        <div className="field">
          <label htmlFor="s-email">Email</label>
          <input id="s-email" type="email" value={prefs.contact_email}
            onChange={(e) => setPrefs({ ...prefs, contact_email: e.target.value })} placeholder="you@university.edu" />
        </div>
      </div>

      <div className="panel">
        <h2>Limits</h2>
        <div className="grid-2">
          <NumField id="budget" label="Overall search time budget (s)" value={prefs.search_budget_seconds} min={5} max={180} onChange={(v) => num("search_budget_seconds", v)} />
          <NumField id="ptimeout" label="Per-source timeout (s)" value={prefs.provider_timeout_seconds} min={2} max={60} onChange={(v) => num("provider_timeout_seconds", v)} />
          <NumField id="maxres" label="Max results per source" value={prefs.max_results_per_provider} min={1} max={50} onChange={(v) => num("max_results_per_provider", v)} />
          <NumField id="retries" label="Retries for temporary errors" value={prefs.max_retries} min={0} max={4} onChange={(v) => num("max_retries", v)} />
          <NumField id="maxpdf" label="Maximum PDF size (MB)" value={prefs.max_pdf_mb} min={1} max={500} onChange={(v) => num("max_pdf_mb", v)} />
          <NumField id="dltimeout" label="Download timeout (s)" value={prefs.download_timeout_seconds} min={5} max={300} onChange={(v) => num("download_timeout_seconds", v)} />
        </div>
      </div>

      <div className="panel">
        <h2>AI assistant</h2>
        <label className="check">
          <input type="checkbox" checked={prefs.assistant_enabled}
            onChange={(e) => setPrefs({ ...prefs, assistant_enabled: e.target.checked })} />
          Enable the “Ask AI” search assistant
        </label>
        <p className="muted small">
          API key {s.credentials.anthropic_api_key ? "is configured" : "is not configured"} (set ANTHROPIC_API_KEY in .env).
          The assistant only suggests search fields; it never supplies paper metadata.
        </p>
        <h3>Optional API keys</h3>
        <ul className="small">
          <li>OpenAlex key: {s.credentials.openalex_api_key ? "configured" : "not set (shared free budget)"}</li>
          <li>Semantic Scholar key: {s.credentials.s2_api_key ? "configured" : "not set (shared rate limit)"}</li>
        </ul>
        <p className="muted small">Keys live only in the local .env file and are never shown or sent to the browser.</p>
      </div>

      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="actions sticky-actions">
        <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
          {saving ? "Saving…" : "Save settings"}
        </button>
        <button type="button" className="btn" onClick={() => setPrefs(s.preferences)}>Discard changes</button>
      </div>

      <div className="panel">
        <h2>Local storage</h2>
        {storage.data ? (
          <dl className="kv">
            <dt>Data folder</dt><dd><code>{s.data_dir}</code></dd>
            <dt>Downloaded PDFs</dt><dd>{(storage.data as StorageInfo).download_files} files · {formatBytes(storage.data.download_bytes)}</dd>
            <dt>Database</dt><dd>{formatBytes(storage.data.database_bytes)} ({storage.data.searches} searches, {storage.data.papers} paper records, {storage.data.saved} saved)</dd>
            <dt>Free disk space</dt><dd>{formatBytes(storage.data.disk_free_bytes)}</dd>
          </dl>
        ) : storage.loading ? <Spinner /> : <ErrorBox onRetry={storage.load}>{storage.error}</ErrorBox>}
      </div>
    </section>
  );
}

function NumField({ id, label, value, min, max, onChange }: {
  id: string; label: string; value: number; min: number; max: number; onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor={`n-${id}`}>{label}</label>
      <input id={`n-${id}`} type="number" min={min} max={max} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
