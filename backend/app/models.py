"""Domain and API models.

Core rule (SRS §9): missing data stays missing (``None``), never guessed.
Every field value that came from a provider keeps its provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- enums


class SearchMode(str, Enum):
    doi_url = "doi_url"
    title = "title"
    advanced = "advanced"


class MatchLabel(str, Enum):
    exact = "Exact"
    probable = "Probable"
    similar = "Similar"
    unverified = "Unverified"


class AccessStatus(str, Enum):
    open_access = "Open Access"
    closed = "Closed"
    alternative = "Alternative Version Found"
    unknown = "Unknown"


class VersionLabel(str, Enum):
    vor = "Version of Record"
    accepted = "Accepted Manuscript"
    preprint = "Preprint"
    unknown = "Unknown"


class VerificationState(str, Enum):
    not_checked = "Not Checked"
    verified = "Verified"
    limited = "Verification Limited"
    rejected = "Rejected"
    failed = "Failed"


class SourceStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    no_results = "no_results"
    failed = "failed"
    timeout = "timeout"
    rate_limited = "rate_limited"
    skipped = "skipped"
    cancelled = "cancelled"


class JobStatus(str, Enum):
    running = "running"
    completed = "completed"
    partial = "partial"  # finished, but at least one source failed
    failed = "failed"  # every source failed
    cancelled = "cancelled"


# ---------------------------------------------------------------- building blocks


class Author(BaseModel):
    name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None


class ProvValue(BaseModel):
    source: str
    value: Any


class Conflict(BaseModel):
    field: str
    values: list[ProvValue]
    note: str = ""


class VersionCandidate(BaseModel):
    id: str = ""
    url: str  # PDF url, or landing page when no direct PDF is known
    is_pdf_url: bool
    landing_page_url: str | None = None
    version: VersionLabel = VersionLabel.unknown
    source: str
    host_type: str | None = None  # publisher / repository
    license: str | None = None
    is_oa: bool | None = None
    evidence: list[str] = Field(default_factory=list)
    verification: VerificationState = VerificationState.not_checked
    verification_detail: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utcnow)


class AbstractRecord(BaseModel):
    state: Literal["available", "unavailable", "retrieval_failed"]
    text: str | None = None
    source: str | None = None
    checked_sources: list[str] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)
    retrieved_at: str = Field(default_factory=utcnow)


class MatchInfo(BaseModel):
    label: MatchLabel = MatchLabel.unverified
    score: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AccessInfo(BaseModel):
    status: AccessStatus = AccessStatus.unknown
    evidence: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utcnow)


class ProviderRecord(BaseModel):
    """One normalised record returned by one provider."""

    source: str
    source_id: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    type: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    abstract: str | None = None
    url: str | None = None
    is_oa: bool | None = None
    candidates: list[VersionCandidate] = Field(default_factory=list)


class Paper(BaseModel):
    id: str
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    type: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    url: str | None = None
    abstract: AbstractRecord = Field(
        default_factory=lambda: AbstractRecord(state="unavailable")
    )
    sources: list[str] = Field(default_factory=list)
    source_ids: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, list[ProvValue]] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)
    match: MatchInfo = Field(default_factory=MatchInfo)
    access: AccessInfo = Field(default_factory=AccessInfo)
    candidates: list[VersionCandidate] = Field(default_factory=list)
    retrieved_at: str = Field(default_factory=utcnow)


class SourceOutcome(BaseModel):
    provider: str
    status: SourceStatus = SourceStatus.pending
    count: int = 0
    duration_ms: int | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    attempts: int = 0
    finished_at: str | None = None


# ---------------------------------------------------------------- API: search


class SearchRequest(BaseModel):
    mode: SearchMode
    query: str | None = Field(None, max_length=2000)  # DOI/URL or title
    title: str | None = Field(None, max_length=1000)
    authors: list[str] = Field(default_factory=list, max_length=20)
    keywords: str | None = Field(None, max_length=500)
    year_from: int | None = Field(None, ge=1000, le=2100)
    year_to: int | None = Field(None, ge=1000, le=2100)
    venue: str | None = Field(None, max_length=300)
    providers: list[str] | None = None  # override enabled providers for this search

    @model_validator(mode="after")
    def _check(self) -> "SearchRequest":
        def blank(s: str | None) -> bool:
            return not (s and s.strip())

        if self.mode in (SearchMode.doi_url, SearchMode.title) and blank(self.query):
            raise ValueError("Please enter a DOI, URL or title to search for.")
        if self.mode == SearchMode.title and len(self.query.strip()) < 3:
            raise ValueError("Title is too short – enter at least 3 characters.")
        if self.mode == SearchMode.advanced:
            self.authors = [a.strip() for a in self.authors if a and a.strip()]
            if all(blank(x) for x in (self.title, self.keywords, self.venue)) and not self.authors:
                raise ValueError("Enter at least a title, author, keyword or venue.")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("'Year from' must not be after 'Year to'.")
        return self


class SearchJob(BaseModel):
    id: str
    mode: SearchMode
    request: SearchRequest
    status: JobStatus
    started_at: str
    finished_at: str | None = None
    sources: list[SourceOutcome]
    results: list[Paper] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    cached: bool = False


# ---------------------------------------------------------------- API: other


class DownloadRequest(BaseModel):
    paper_id: str
    candidate_id: str


class DownloadRecord(BaseModel):
    id: str
    paper_id: str
    candidate_id: str
    source_url: str
    final_url: str | None = None
    status: Literal["queued", "downloading", "validating", "ready", "blocked", "failed", "cancelled"]
    verification: VerificationState = VerificationState.not_checked
    verification_detail: list[str] = Field(default_factory=list)
    filename: str | None = None
    size_bytes: int | None = None
    bytes_received: int = 0
    sha256: str | None = None
    error: str | None = None
    file_deleted: bool = False
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class CitationRequest(BaseModel):
    paper_id: str | None = None
    style: Literal["apa7", "ieee", "bibtex"] = "apa7"
    overrides: dict[str, Any] = Field(default_factory=dict)


class CitationSegment(BaseModel):
    text: str
    italic: bool = False


class CitationResponse(BaseModel):
    style: str
    text: str
    segments: list[CitationSegment]
    warnings: list[str]


class SavedPaperIn(BaseModel):
    paper_id: str
    note: str | None = Field(None, max_length=2000)


class AssistantRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=2000)
    previous_search_id: str | None = None


class AssistantSuggestion(BaseModel):
    mode: SearchMode
    query: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    keywords: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    venue: str | None = None
    explanation: str = ""
    refinements: list[str] = Field(default_factory=list)
