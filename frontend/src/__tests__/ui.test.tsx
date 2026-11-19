import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { safeHref } from "../api";
import { PaperCard } from "../components/PaperCard";
import { validateDoiUrl } from "../components/SearchPanel";
import type { Paper } from "../types";

afterEach(cleanup);

const paper: Paper = {
  id: "p_1",
  doi: "10.1038/nature14539",
  title: "Deep learning <script>alert(1)</script>",
  authors: [{ name: "Yann LeCun" }, { name: "Yoshua Bengio" }],
  year: 2015,
  venue: "Nature",
  abstract: { state: "unavailable", checked_sources: ["crossref"], failed_sources: [], retrieved_at: "" },
  sources: ["crossref", "openalex"],
  source_ids: {},
  provenance: { year: [{ source: "crossref", value: 2015 }, { source: "openalex", value: 2016 }] },
  conflicts: [{ field: "year", values: [{ source: "crossref", value: 2015 }, { source: "openalex", value: 2016 }], note: "Online and print publication years can differ by one." }],
  match: { label: "Exact", score: 1, evidence: ["DOI 10.1038/nature14539 matches the query"], warnings: [] },
  access: { status: "Alternative Version Found", evidence: ["Open Preprint reported by openalex"], checked_at: "" },
  candidates: [],
  retrieved_at: "2026-01-01T00:00:00Z",
};

describe("input validation", () => {
  it("accepts DOIs, arXiv IDs and URLs", () => {
    expect(validateDoiUrl("10.1038/nature14539")).toBeNull();
    expect(validateDoiUrl("https://doi.org/10.1038/nature14539")).toBeNull();
    expect(validateDoiUrl("arXiv:1706.03762")).toBeNull();
    expect(validateDoiUrl("https://example.org/paper")).toBeNull();
  });
  it("explains invalid input", () => {
    expect(validateDoiUrl("")).toMatch(/Enter/);
    expect(validateDoiUrl("10.abc")).toMatch(/incomplete/);
    expect(validateDoiUrl("just words")).toMatch(/DOI/);
  });
});

describe("safeHref", () => {
  it("only allows http(s)", () => {
    expect(safeHref("https://arxiv.org/abs/1")).toBe("https://arxiv.org/abs/1");
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
    expect(safeHref("data:text/html,x")).toBeUndefined();
  });
});

describe("PaperCard", () => {
  it("renders labels, escapes hostile metadata, and shows evidence", () => {
    render(<PaperCard paper={paper} onCite={() => {}} />);
    expect(screen.getByText(/Exact match/)).toBeTruthy();
    expect(screen.getByText("Alternative Version Found")).toBeTruthy();
    // Title is rendered as text, not HTML (R-30)
    expect(screen.getByRole("heading", { level: 3 }).textContent).toContain("<script>");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/metadata conflict/)).toBeTruthy();
    expect(screen.getByText(/matches the query/)).toBeTruthy();
  });

  it("shows an honest unavailable abstract state", () => {
    render(<PaperCard paper={paper} onCite={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show abstract/ }));
    expect(screen.getByText(/Abstract unavailable from the sources checked/)).toBeTruthy();
  });
});
