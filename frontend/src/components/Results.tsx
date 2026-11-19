import { useEffect, useMemo, useState } from "react";
import type { AccessStatus, MatchLabel, Paper } from "../types";
import { PaperCard } from "./PaperCard";

const PAGE_SIZE = 10;
const MATCH_ORDER: MatchLabel[] = ["Exact", "Probable", "Similar", "Unverified"];
type Sort = "relevance" | "year_desc" | "year_asc" | "title";

export function Results({ papers, running, onCite }: { papers: Paper[]; running: boolean; onCite: (p: Paper) => void }) {
  const [sort, setSort] = useState<Sort>("relevance");
  const [match, setMatch] = useState<MatchLabel | "">("");
  const [access, setAccess] = useState<AccessStatus | "">("");
  const [type, setType] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [page, setPage] = useState(0);

  const types = useMemo(() => [...new Set(papers.map((p) => p.type).filter(Boolean) as string[])].sort(), [papers]);

  const filtered = useMemo(() => {
    let out = papers.filter((p) =>
      (!match || p.match.label === match) &&
      (!access || p.access.status === access) &&
      (!type || p.type === type) &&
      (!yearFrom || (p.year ?? 0) >= Number(yearFrom)) &&
      (!yearTo || (p.year ?? 9999) <= Number(yearTo)),
    );
    if (sort !== "relevance") {
      out = [...out].sort((a, b) => {
        if (sort === "title") return (a.title ?? "").localeCompare(b.title ?? "");
        const ya = a.year ?? (sort === "year_desc" ? -Infinity : Infinity);
        const yb = b.year ?? (sort === "year_desc" ? -Infinity : Infinity);
        return sort === "year_desc" ? yb - ya : ya - yb;
      });
    }
    return out;
  }, [papers, sort, match, access, type, yearFrom, yearTo]);

  useEffect(() => setPage(0), [sort, match, access, type, yearFrom, yearTo, papers.length]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const anyFilter = !!(match || access || type || yearFrom || yearTo);

  if (papers.length === 0) {
    if (running) return null;
    return (
      <section className="panel empty" aria-live="polite">
        <h2>No matching papers found in the sources searched</h2>
        <ul>
          <li>Check the DOI or title for typos.</li>
          <li>Try fewer or broader keywords, or remove the year/venue filter.</li>
          <li>Use Advanced search with an author surname.</li>
          <li>Some sources may have failed – see the source status above and retry.</li>
        </ul>
      </section>
    );
  }

  return (
    <section aria-label="Results">
      <div className="toolbar">
        <strong aria-live="polite">
          {filtered.length === papers.length ? `${papers.length} result${papers.length !== 1 ? "s" : ""}` :
            `${filtered.length} of ${papers.length} results`}
          {running && " (more sources still searching…)"}
        </strong>
        <div className="spacer" />
        <label className="inline">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="relevance">Best match</option>
            <option value="year_desc">Newest first</option>
            <option value="year_asc">Oldest first</option>
            <option value="title">Title A–Z</option>
          </select>
        </label>
      </div>
      <div className="filters" role="group" aria-label="Filter results">
        <label className="inline">
          Match
          <select value={match} onChange={(e) => setMatch(e.target.value as MatchLabel | "")}>
            <option value="">All</option>
            {MATCH_ORDER.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <label className="inline">
          Access
          <select value={access} onChange={(e) => setAccess(e.target.value as AccessStatus | "")}>
            <option value="">All</option>
            {(["Open Access", "Alternative Version Found", "Closed", "Unknown"] as AccessStatus[]).map((a) => <option key={a}>{a}</option>)}
          </select>
        </label>
        {types.length > 1 && (
          <label className="inline">
            Type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">All</option>
              {types.map((t) => <option key={t} value={t}>{t.replace(/[-_]/g, " ")}</option>)}
            </select>
          </label>
        )}
        <label className="inline">
          Year
          <input className="year" inputMode="numeric" placeholder="from" value={yearFrom} aria-label="Filter year from"
            onChange={(e) => setYearFrom(e.target.value.replace(/\D/g, "").slice(0, 4))} />
          –
          <input className="year" inputMode="numeric" placeholder="to" value={yearTo} aria-label="Filter year to"
            onChange={(e) => setYearTo(e.target.value.replace(/\D/g, "").slice(0, 4))} />
        </label>
        {anyFilter && (
          <button type="button" className="link-btn" onClick={() => { setMatch(""); setAccess(""); setType(""); setYearFrom(""); setYearTo(""); }}>
            Clear filters
          </button>
        )}
      </div>
      {visible.length === 0 ? (
        <p className="panel muted">No results match these filters.</p>
      ) : (
        <div className="cards">{visible.map((p) => <PaperCard key={p.id} paper={p} onCite={onCite} />)}</div>
      )}
      {pages > 1 && (
        <nav className="pager" aria-label="Result pages">
          <button type="button" className="btn btn-small" disabled={page === 0} onClick={() => setPage(page - 1)}>← Previous</button>
          <span>Page {page + 1} of {pages}</span>
          <button type="button" className="btn btn-small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next →</button>
        </nav>
      )}
    </section>
  );
}
