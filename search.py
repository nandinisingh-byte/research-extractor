"""
search.py — Search academic databases for research papers.

Sources:
  1. Semantic Scholar (primary) — broad coverage, free, no key needed
  2. arXiv (secondary)          — great for space/satellite/physics papers

Returns a unified list of SearchResult dicts.
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

TIMEOUT = 15


# ── Result model ──────────────────────────────────────────────────────────────

def make_result(
    source: str,
    title: str,
    authors: str,
    year: Optional[int],
    abstract: str,
    doi: Optional[str],
    url: Optional[str],
    venue: Optional[str] = None,
    citation_count: Optional[int] = None,
    paper_id: Optional[str] = None,
) -> dict:
    return {
        "source":         source,
        "title":          title,
        "authors":        authors,
        "year":           year,
        "abstract":       abstract,
        "doi":            doi,
        "url":            url,
        "venue":          venue or "",
        "citation_count": citation_count,
        "paper_id":       paper_id or "",
    }


# ── Semantic Scholar ──────────────────────────────────────────────────────────

SS_BASE = "https://api.semanticscholar.org/graph/v1"
SS_FIELDS = "title,authors,year,abstract,externalIds,venue,citationCount,openAccessPdf,url"


def search_semantic_scholar(query: str, limit: int = 10) -> list[dict]:
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(
                f"{SS_BASE}/paper/search",
                params={"query": query, "limit": limit, "fields": SS_FIELDS},
                headers={"User-Agent": "OrbittSpaceResearchTool/1.0"},
            )
        if resp.status_code != 200:
            return []

        data = resp.json()
        results = []
        for p in data.get("data", []):
            authors = ", ".join(
                a.get("name", "") for a in (p.get("authors") or [])[:5]
            )
            if len(p.get("authors") or []) > 5:
                authors += " et al."

            ext = p.get("externalIds") or {}
            doi = ext.get("DOI")
            arxiv_id = ext.get("ArXiv")

            url = p.get("url") or ""
            if arxiv_id:
                url = f"https://arxiv.org/abs/{arxiv_id}"
            elif doi:
                url = f"https://doi.org/{doi}"

            results.append(make_result(
                source="Semantic Scholar",
                title=p.get("title", "Untitled"),
                authors=authors,
                year=p.get("year"),
                abstract=p.get("abstract") or "",
                doi=doi,
                url=url,
                venue=p.get("venue"),
                citation_count=p.get("citationCount"),
                paper_id=p.get("paperId", ""),
            ))
        return results
    except Exception:
        return []


# ── arXiv ─────────────────────────────────────────────────────────────────────

ARXIV_BASE = "https://export.arxiv.org/api/query"
ARXIV_NS = {
    "atom":    "http://www.w3.org/2005/Atom",
    "arxiv":   "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


def search_arxiv(query: str, limit: int = 8) -> list[dict]:
    try:
        # Add space-specific booster to surface relevant papers
        search_query = f"all:{query}"
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(
                ARXIV_BASE,
                params={
                    "search_query": search_query,
                    "max_results":  limit,
                    "sortBy":       "relevance",
                    "sortOrder":    "descending",
                },
            )
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        results = []

        for entry in root.findall("atom:entry", ARXIV_NS):
            title_el   = entry.find("atom:title", ARXIV_NS)
            summary_el = entry.find("atom:summary", ARXIV_NS)
            id_el      = entry.find("atom:id", ARXIV_NS)
            pub_el     = entry.find("atom:published", ARXIV_NS)

            title   = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
            abstract = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
            arxiv_url = (id_el.text or "").strip() if id_el is not None else ""

            # Extract year from published date
            year = None
            if pub_el is not None and pub_el.text:
                m = re.match(r"(\d{4})", pub_el.text)
                if m:
                    year = int(m.group(1))

            # Authors
            authors_list = []
            for author in entry.findall("atom:author", ARXIV_NS):
                name_el = author.find("atom:name", ARXIV_NS)
                if name_el is not None and name_el.text:
                    authors_list.append(name_el.text.strip())
            authors = ", ".join(authors_list[:5])
            if len(authors_list) > 5:
                authors += " et al."

            # DOI
            doi_el = entry.find("arxiv:doi", ARXIV_NS)
            doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

            # arXiv ID from URL
            arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else ""

            results.append(make_result(
                source="arXiv",
                title=title,
                authors=authors,
                year=year,
                abstract=abstract,
                doi=doi,
                url=arxiv_url,
                venue="arXiv",
                paper_id=arxiv_id,
            ))

        return results
    except Exception:
        return []


# ── Combined search ───────────────────────────────────────────────────────────

def search_papers(query: str, limit: int = 15) -> list[dict]:
    """
    Search both Semantic Scholar and arXiv, merge results, deduplicate by title.
    """
    ss_results    = search_semantic_scholar(query, limit=limit)
    arxiv_results = search_arxiv(query, limit=8)

    # Deduplicate: skip arXiv results whose titles are already in SS results
    ss_titles = {r["title"].lower()[:60] for r in ss_results}
    unique_arxiv = [
        r for r in arxiv_results
        if r["title"].lower()[:60] not in ss_titles
    ]

    combined = ss_results + unique_arxiv
    return combined[:limit + 5]  # return up to limit+5 so UI can cap


# ── Enrich metadata via Claude ────────────────────────────────────────────────

import json
import os
import anthropic

from models import (
    AltitudeRange, MethodologyType, MissionType,
    ResearchPaper, VLEORelevance, VLEORelevanceNotes, VLEOScore, ReadingStatus,
)

ENRICH_SYSTEM = """\
You are an expert VLEO (Very Low Earth Orbit) research analyst.
Given a paper's title, authors, year, abstract, and venue, return a JSON object
with all structured fields filled in as accurately as possible from the available information.
Since you only have the abstract (not the full paper), be appropriately cautious
with fields like key_results and figures_graphs — mark them as partial.
Apply the 4-criteria VLEO scoring framework rigorously.
Return ONLY valid JSON, no markdown.
"""

ENRICH_PROMPT = """\
Paper metadata:
Title: {title}
Authors: {authors}
Year: {year}
Venue: {venue}
Abstract: {abstract}
DOI/URL: {url}

Return this JSON schema:
{{
  "title": "...",
  "authors": "...",
  "year": integer or null,
  "journal_conference": "...",
  "doi_url": "...",
  "status": "📥 To Read",
  "tags": ["tag1", "tag2"],
  "methodology_type": "Analytical|Simulation|Observational|Experimental|Review or null",
  "altitude_range": "VLEO (<450 km)|LEO (450–2000 km)|MEO|GEO|Multiple or null",
  "mission_type": "Earth Observation|Communications|Science|Space Sustainability|Navigation|Technology Demonstration|Military|Commercial|Other or null",
  "organization_agency": "...",
  "aim": "Core aim or research question",
  "methodology": "Study design and approach (from abstract)",
  "key_results": "Key findings mentioned in abstract",
  "figures_graphs": null,
  "summary": "3–4 sentence plain-language summary",
  "references": null,
  "vleo_relevance": "🔴 High|🟡 Medium|🟢 Low|⬜ Not Assessed",
  "vleo_relevance_notes": {{
    "altitude_coverage": "✅|⚠️|❌",
    "atmospheric_drag": "✅|⚠️|❌",
    "satellite_design_for_vleo": "✅|⚠️|❌",
    "collision_debris_risk": "✅|⚠️|❌",
    "verdict": "1–2 sentence VLEO relevance summary"
  }}
}}
"""


def enrich_from_metadata(result: dict) -> ResearchPaper:
    """Use Claude to turn search result metadata into a full ResearchPaper."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = ENRICH_PROMPT.format(
        title=result.get("title", ""),
        authors=result.get("authors", ""),
        year=result.get("year", ""),
        venue=result.get("venue", ""),
        abstract=result.get("abstract", ""),
        url=result.get("url") or result.get("doi") or "",
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",   # faster + cheaper for metadata enrichment
        max_tokens=2048,
        system=ENRICH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    def safe_enum(cls, val, default=None):
        if val is None: return default
        try: return cls(val)
        except ValueError: return default

    vleo_notes_raw = data.get("vleo_relevance_notes")
    vleo_notes = None
    if vleo_notes_raw:
        vleo_notes = VLEORelevanceNotes(
            altitude_coverage=safe_enum(VLEOScore, vleo_notes_raw.get("altitude_coverage"), VLEOScore.NOT),
            atmospheric_drag=safe_enum(VLEOScore, vleo_notes_raw.get("atmospheric_drag"), VLEOScore.NOT),
            satellite_design_for_vleo=safe_enum(VLEOScore, vleo_notes_raw.get("satellite_design_for_vleo"), VLEOScore.NOT),
            collision_debris_risk=safe_enum(VLEOScore, vleo_notes_raw.get("collision_debris_risk"), VLEOScore.NOT),
            verdict=vleo_notes_raw.get("verdict", ""),
        )

    return ResearchPaper(
        title=data.get("title", result.get("title", "Untitled")),
        authors=data.get("authors", result.get("authors", "")),
        year=data.get("year", result.get("year")),
        journal_conference=data.get("journal_conference", result.get("venue")),
        doi_url=data.get("doi_url", result.get("url")),
        status=safe_enum(ReadingStatus, data.get("status"), ReadingStatus.TO_READ),
        tags=data.get("tags", []),
        methodology_type=safe_enum(MethodologyType, data.get("methodology_type")),
        altitude_range=safe_enum(AltitudeRange, data.get("altitude_range")),
        mission_type=safe_enum(MissionType, data.get("mission_type")),
        organization_agency=data.get("organization_agency"),
        aim=data.get("aim", ""),
        methodology=data.get("methodology", ""),
        key_results=data.get("key_results", ""),
        figures_graphs=data.get("figures_graphs"),
        summary=data.get("summary", ""),
        references=data.get("references"),
        vleo_relevance=safe_enum(VLEORelevance, data.get("vleo_relevance"), VLEORelevance.NOT_ASSESSED),
        vleo_relevance_notes=vleo_notes,
    )
