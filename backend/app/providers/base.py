"""Provider adapter interface (SRS §8.2, NFR-25/28)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import Preferences, Settings
from ..models import ProviderRecord, VersionLabel
from ..net import ProviderClient


class NotSupported(Exception):
    """The provider cannot handle this kind of query (reported as 'skipped')."""


@dataclass
class Query:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    keywords: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    venue: str | None = None
    limit: int = 10

    def text(self) -> str:
        return " ".join(p for p in (self.title, self.keywords) if p).strip()


@dataclass
class Ctx:
    client: ProviderClient
    prefs: Preferences
    settings: Settings
    deadline: float | None = None
    on_attempt: Callable[[], None] | None = None
    notes: list[str] = field(default_factory=list)

    def get(self, provider: str, url: str, **kw):
        return self.client.get(provider, url, deadline=self.deadline, attempts_cb=self.on_attempt, **kw)


class Provider:
    name: str = ""
    label: str = ""
    description: str = ""
    homepage: str = ""
    # Which operations this adapter supports
    supports_doi: bool = True
    supports_search: bool = True
    supports_arxiv: bool = False

    def unavailable_reason(self, prefs: Preferences, settings: Settings) -> str | None:
        """Return a human reason if the provider cannot run with current config."""
        return None

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        raise NotSupported(f"{self.label} does not support DOI lookup.")

    async def lookup_arxiv(self, ctx: Ctx, arxiv_id: str) -> list[ProviderRecord]:
        raise NotSupported(f"{self.label} does not support arXiv ID lookup.")

    async def search(self, ctx: Ctx, q: Query) -> list[ProviderRecord]:
        raise NotSupported(f"{self.label} does not support search.")


def version_from_label(v: str | None) -> VersionLabel:
    """Map OpenAlex/Unpaywall version vocab to our labels."""
    return {
        "publishedVersion": VersionLabel.vor,
        "acceptedVersion": VersionLabel.accepted,
        "submittedVersion": VersionLabel.preprint,
    }.get(v or "", VersionLabel.unknown)
