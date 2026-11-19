import { useEffect, useState } from "react";
import { api, formatBytes, safeHref, sourceLabel } from "../api";
import type { DownloadRecord, Paper, VersionCandidate, VersionsResponse } from "../types";
import { AccessBadge, ErrorBox, Spinner, VerificationBadge, VersionBadge, usePoll } from "./common";

export function VersionsPanel({ paper }: { paper: Paper }) {
  const [data, setData] = useState<VersionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await api.discoverVersions(paper.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paper.id]);

  return (
    <section className="versions" aria-label="Full-text versions" aria-busy={loading}>
      <h4>
        Full-text versions {data && <AccessBadge status={data.access.status} />}
      </h4>
      {loading && (
        <p className="muted"><Spinner label="Searching for open versions" /> Looking for lawful open-access copies…</p>
      )}
      {error && <ErrorBox onRetry={load}>{error}</ErrorBox>}
      {data && !loading && (
        <>
          {data.sources.length > 0 && (
            <p className="muted small">
              Checked:{" "}
              {data.sources.map((s) => `${sourceLabel(s.provider)} (${s.status.replace("_", " ")})`).join(", ")}
            </p>
          )}
          {data.candidates.length === 0 ? (
            <div className="alert alert-info">
              <strong>No open copy found in the sources checked.</strong> This does not prove none exists. You may be
              able to access it through your library or institution, or by requesting it from the authors.
              {paper.doi && (
                <> Publisher page: <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer">doi.org/{paper.doi}</a></>
              )}
            </div>
          ) : (
            <ul className="cand-list">
              {data.candidates.map((c) => <Candidate key={c.id} paper={paper} cand={c} />)}
            </ul>
          )}
          <p className="muted small">
            Only copies that a source reports as openly available are offered. PaperFinder never bypasses paywalls or
            logins.
          </p>
        </>
      )}
    </section>
  );
}

function Candidate({ paper, cand }: { paper: Paper; cand: VersionCandidate }) {
  const [dl, setDl] = useState<DownloadRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = !!dl && ["queued", "downloading", "validating"].includes(dl.status);

  usePoll(async () => {
    if (!dl) return true;
    const d = await api.getDownload(dl.id);
    setDl(d);
    return !["queued", "downloading", "validating"].includes(d.status);
  }, active, 500);

  async function start() {
    setError(null);
    try {
      setDl(await api.startDownload(paper.id, cand.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function cancel() {
    if (dl) setDl(await api.cancelDownload(dl.id));
  }

  let host = "";
  try {
    host = new URL(cand.url).hostname;
  } catch {
    host = "";
  }
  const landing = safeHref(cand.landing_page_url);
  const verification = dl?.verification && dl.verification !== "Not Checked" ? dl.verification : cand.verification;

  return (
    <li className="cand">
      <div className="cand-head">
        <VersionBadge version={cand.version} />
        <VerificationBadge state={verification} />
        <span className="muted">
          via {sourceLabel(cand.source)}
          {host && ` · ${host}`}
          {cand.license && ` · licence: ${cand.license}`}
        </span>
      </div>
      <details>
        <summary className="small">Evidence</summary>
        <ul className="small">{cand.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
        {cand.verification_detail.length > 0 && !dl && (
          <ul className="small">{cand.verification_detail.map((e, i) => <li key={i}>{e}</li>)}</ul>
        )}
      </details>
      {cand.version === "Preprint" && (
        <p className="small warn-text">
          Preprint – may differ from the published version. Cite the published version where possible.
        </p>
      )}
      <div className="cand-actions">
        {!dl && cand.is_oa && (
          <button type="button" className="btn btn-small btn-primary" onClick={start}>
            {cand.is_pdf_url ? "Download & verify PDF" : "Find PDF on page & verify"}
          </button>
        )}
        {!cand.is_oa && <span className="small muted">Not reported as open access – not downloadable here.</span>}
        {landing && (
          <a className="btn btn-small" href={landing} target="_blank" rel="noopener noreferrer">
            Open landing page ↗
          </a>
        )}
        {active && dl && (
          <>
            <span className="small" aria-live="polite">
              <Spinner label="Downloading" />{" "}
              {dl.status === "validating" ? "Validating PDF…" : `Downloading… ${formatBytes(dl.bytes_received)}`}
              {dl.size_bytes ? ` of ${formatBytes(dl.size_bytes)}` : ""}
            </span>
            <button type="button" className="btn btn-small" onClick={cancel}>Cancel</button>
          </>
        )}
        {dl?.status === "ready" && (
          <a className="btn btn-small btn-primary" href={api.fileUrl(dl.id)} download={dl.filename ?? undefined}>
            ⬇ Save PDF ({formatBytes(dl.size_bytes)})
          </a>
        )}
        {dl && ["failed", "blocked", "cancelled"].includes(dl.status) && (
          <button type="button" className="btn btn-small" onClick={start}>Retry</button>
        )}
      </div>
      {dl && !active && (
        <div className={`small dl-result dl-${dl.status}`} aria-live="polite">
          {dl.status === "ready" && dl.verification === "Verification Limited" && (
            <p className="warn-text">⚠ The file is a valid PDF, but it could not be fully matched to this paper. Check it before relying on it.</p>
          )}
          {dl.error && <p>{dl.status === "blocked" ? "⛔ " : "✖ "}{dl.error}</p>}
          {dl.verification_detail.length > 0 && (
            <ul>{dl.verification_detail.map((d, i) => <li key={i}>{d}</li>)}</ul>
          )}
        </div>
      )}
      {error && <ErrorBox>{error}</ErrorBox>}
    </li>
  );
}
