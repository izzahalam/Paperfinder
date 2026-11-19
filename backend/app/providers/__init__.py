from .arxiv import ArxivProvider
from .base import Ctx, NotSupported, Provider, Query
from .crossref import CrossrefProvider
from .openalex import OpenAlexProvider
from .semanticscholar import SemanticScholarProvider
from .unpaywall import UnpaywallProvider

# Order = metadata priority used when merging (most authoritative first).
REGISTRY: dict[str, Provider] = {
    p.name: p
    for p in (
        CrossrefProvider(),
        OpenAlexProvider(),
        SemanticScholarProvider(),
        UnpaywallProvider(),
        ArxivProvider(),
    )
}

__all__ = ["REGISTRY", "Ctx", "NotSupported", "Provider", "Query"]
