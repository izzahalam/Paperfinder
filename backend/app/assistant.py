"""Optional AI search assistant (Claude).

Scope is deliberately narrow and bounded (SRS NFR-11, R-25, §14):

* It turns a researcher's free-text request into *search fields* (and, after a
  search, suggests refinements). It never produces bibliographic metadata,
  abstracts, access labels or files.
* One request per call, structured JSON output, no tools, no autonomous loops.
* Its suggestion is shown to the user for review; nothing is searched until the
  user submits it. Every result still goes through the deterministic pipeline.
* Any DOI it returns must literally appear in the user's text (anti-fabrication).
* Privacy: only the typed request (and, for refinement, the previous query and
  result titles) is sent to Anthropic; the UI discloses this.
"""

from __future__ import annotations

import json
import logging
import os
import re

from .models import AssistantSuggestion, SearchJob, SearchMode
from .normalize import DOI_SEARCH_RE, normalize_doi, parse_identifier

log = logging.getLogger("paperfinder.assistant")

SYSTEM_PROMPT = """You help researchers turn a request written in plain language into search fields for academic \
metadata services (Crossref, OpenAlex, Semantic Scholar, arXiv). The request may be in any language, including \
Urdu or Roman Urdu; always write the search fields in English (paper titles in their original language).

Choose a mode:
- "doi_url" when the user gives a DOI, an arXiv ID or a paper URL. Put it, exactly as written, in `query`.
- "title" when the user names one specific paper by its title. Put the title in `query`.
- "advanced" otherwise. Fill `title` (only a known or partial paper title), `authors` (surnames or full names),
  `keywords` (2-6 precise topical terms, no filler words), `year_from`/`year_to`, and `venue` only when stated or
  clearly implied.

Rules:
- Never invent identifiers, titles, authors or years that the user did not provide or clearly imply. If you
  recognise a famous paper the user describes, you may put its well-known title in `title`, and say so in
  `explanation` so the user can check it.
- Leave fields empty (null or []) rather than guessing.
- `explanation`: one or two short sentences, in the user's language, describing how you interpreted the request.
- `refinements`: up to 4 short, concrete suggestions to improve the search (e.g. narrower keywords, adding a year
  range or an author). If information about a previous search is provided, base suggestions on it."""

SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["doi_url", "title", "advanced"]},
        "query": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "authors": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": ["string", "null"]},
        "year_from": {"type": ["integer", "null"]},
        "year_to": {"type": ["integer", "null"]},
        "venue": {"type": ["string", "null"]},
        "explanation": {"type": "string"},
        "refinements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mode", "query", "title", "authors", "keywords", "year_from", "year_to", "venue",
                 "explanation", "refinements"],
    "additionalProperties": False,
}


class AssistantUnavailable(RuntimeError):
    pass


class AssistantError(RuntimeError):
    pass


def assistant_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_user_message(text: str, previous: SearchJob | None) -> str:
    parts = [f"<request>\n{text.strip()}\n</request>"]
    if previous is not None:
        req = previous.request
        q = req.query or json.dumps({k: v for k, v in req.model_dump(mode="json").items()
                                      if v not in (None, [], "") and k != "providers"})
        titles = [f"- [{p.match.label.value}] {p.title} ({p.year or 'n.d.'})" for p in previous.results[:8]]
        parts.append(
            "<previous_search>\n"
            f"mode: {req.mode.value}\nquery: {q}\nresult_count: {len(previous.results)}\n"
            + ("top_results:\n" + "\n".join(titles) if titles else "top_results: none")
            + "\n</previous_search>"
        )
    return "\n\n".join(parts)


def sanitize(raw: dict, user_text: str) -> AssistantSuggestion:
    """Validate model output and strip anything not grounded in the user's text."""
    s = AssistantSuggestion.model_validate(raw)
    user_dois = {normalize_doi(m) for m in DOI_SEARCH_RE.findall(user_text)}
    notes: list[str] = []

    if s.mode == SearchMode.doi_url:
        ident = parse_identifier(s.query or "")
        cand = normalize_doi(s.query or "")
        grounded = (s.query or "").strip() and (s.query.strip().lower() in user_text.lower() or (cand and cand in user_dois))
        if ident.kind == "invalid" or not grounded:
            notes.append("The suggested identifier was not in your request, so it was removed.")
            s.mode, s.query = SearchMode.advanced, None
    if s.mode == SearchMode.title and not (s.query or "").strip():
        s.mode = SearchMode.advanced
    for f in ("title", "keywords", "venue"):
        v = getattr(s, f)
        if v is not None:
            v = re.sub(r"\s+", " ", v).strip()[:300] or None
            setattr(s, f, v)
    s.authors = [a.strip()[:100] for a in s.authors if a and a.strip()][:10]
    for f in ("year_from", "year_to"):
        y = getattr(s, f)
        if y is not None and not (1000 <= y <= 2100):
            setattr(s, f, None)
    if s.year_from and s.year_to and s.year_from > s.year_to:
        s.year_from, s.year_to = s.year_to, s.year_from
    if s.mode == SearchMode.advanced and not (s.title or s.keywords or s.venue or s.authors):
        s.keywords = re.sub(r"\s+", " ", user_text).strip()[:200]
    s.refinements = [r.strip()[:200] for r in s.refinements if r.strip()][:4]
    s.explanation = (s.explanation.strip()[:500] + (" " + " ".join(notes) if notes else "")).strip()
    return s


async def interpret(text: str, previous: SearchJob | None, model: str) -> AssistantSuggestion:
    if not assistant_configured():
        raise AssistantUnavailable(
            "The AI assistant is not configured. Add ANTHROPIC_API_KEY to the .env file to enable it.")
    import anthropic

    client = anthropic.AsyncAnthropic(max_retries=2, timeout=60.0)
    try:
        response = await client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(text, previous)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AssistantError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.") from None
    except anthropic.RateLimitError:
        raise AssistantError("The AI assistant is rate-limited right now. Try again in a moment.") from None
    except anthropic.BadRequestError as e:
        log.warning("assistant bad request: %s", e)
        raise AssistantError("The AI assistant could not process this request.") from None
    except anthropic.APIStatusError as e:
        raise AssistantError(f"The AI service returned an error (HTTP {e.status_code}). Try again later.") from None
    except anthropic.APIConnectionError:
        raise AssistantError("Could not reach the AI service. Check your internet connection.") from None
    finally:
        await client.close()

    if response.stop_reason == "refusal":
        raise AssistantError("The AI assistant declined this request. Please use the regular search modes.")
    if response.stop_reason == "max_tokens":
        raise AssistantError("The AI assistant's answer was cut off. Try a shorter request.")
    text_out = next((b.text for b in response.content if b.type == "text"), None)
    if not text_out:
        raise AssistantError("The AI assistant returned no suggestion.")
    try:
        raw = json.loads(text_out)
        return sanitize(raw, text)
    except (ValueError, TypeError) as e:
        log.warning("assistant returned invalid output: %r", e)
        raise AssistantError("The AI assistant returned an invalid suggestion.") from None
