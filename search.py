"""
search.py — Search academic databases for research papers.

Sources:
  1. Semantic Scholar (primary) — broad coverage, free, no key needed
  2. arXiv (secondary)          — great for space/satellite/physics papers
  3. CrossRef                   — DOI lookup, full bibliographic metadata
  4. arXiv direct               — lookup by arXiv ID
  5. Semantic Scholar author    — lookup papers by author name

Auto-detection: if the query looks like a DOI, arXiv ID, URL, or author name,
the appropriate lookup is used instead of keyword search.
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


# ── CrossRef DOI lookup ───────────────────────────────────────────────────────

CROSSREF_BASE = "https://api.crossref.org/works"


def lookup_by_doi(doi: str) -> list[dict]:
    """Look up a single paper by DOI via CrossRef."""
    doi = doi.strip().lstrip("https://doi.org/").lstrip("http://doi.org/").lstrip("doi:")
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(
                f"{CROSSREF_BASE}/{doi}",
                headers={"User-Agent": "OrbittSpaceResearchTool/1.0 (mailto:research@orbitt.space)"},
            )
        if resp.status_code != 200:
            return []
        item = resp.json().get("message", {})

        # Authors
        authors_list = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in item.get("author", [])[:5]
        ]
        authors = ", ".join(authors_list)
        if len(item.get("author", [])) > 5:
            authors += " et al."

        # Year
        year = None
        date_parts = item.get("published", {}).get("date-parts", [[]])[0]
        if date_parts:
            year = date_parts[0]

        # Abstract (CrossRef sometimes has it)
        abstract = item.get("abstract", "")
        # Strip JATS XML tags if present
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()

        title_list = item.get("title", ["Untitled"])
        title = title_list[0] if title_list else "Untitled"
        venue = (item.get("container-title") or [""])[0]

        return [make_result(
            source="CrossRef (DOI)",
            title=title,
            authors=authors,
            year=year,
            abstract=abstract or "No abstract available from CrossRef.",
            doi=doi,
            url=f"https://doi.org/{doi}",
            venue=venue,
            citation_count=item.get("is-referenced-by-count"),
            paper_id=doi,
        )]
    except Exception:
        return []


# ── arXiv direct ID lookup ─────────────────────────────────────────────────────

def lookup_by_arxiv_id(arxiv_id: str) -> list[dict]:
    """Look up a single paper directly by arXiv ID (e.g. 2301.12345)."""
    arxiv_id = arxiv_id.strip()
    # Strip URL prefix if pasted
    arxiv_id = re.sub(r"https?://arxiv\.org/(?:abs|pdf)/", "", arxiv_id)
    arxiv_id = arxiv_id.rstrip(".pdf")
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(
                ARXIV_BASE,
                params={"id_list": arxiv_id, "max_results": 1},
            )
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        results = search_arxiv.__wrapped__ if hasattr(search_arxiv, '__wrapped__') else None
        # Parse the single entry directly
        entries = root.findall("atom:entry", ARXIV_NS)
        if not entries:
            return []
        entry = entries[0]

        title_el   = entry.find("atom:title",   ARXIV_NS)
        summary_el = entry.find("atom:summary", ARXIV_NS)
        pub_el     = entry.find("atom:published", ARXIV_NS)
        id_el      = entry.find("atom:id",      ARXIV_NS)
        doi_el     = entry.find("arxiv:doi",    ARXIV_NS)

        title    = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        abstract = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
        arxiv_url = (id_el.text or "").strip() if id_el is not None else ""
        doi      = doi_el.text.strip() if doi_el is not None and doi_el.text else None

        year = None
        if pub_el is not None and pub_el.text:
            m = re.match(r"(\d{4})", pub_el.text)
            if m:
                year = int(m.group(1))

        authors_list = []
        for author in entry.findall("atom:author", ARXIV_NS):
            name_el = author.find("atom:name", ARXIV_NS)
            if name_el is not None and name_el.text:
                authors_list.append(name_el.text.strip())
        authors = ", ".join(authors_list[:5])
        if len(authors_list) > 5:
            authors += " et al."

        return [make_result(
            source="arXiv (direct)",
            title=title,
            authors=authors,
            year=year,
            abstract=abstract,
            doi=doi,
            url=arxiv_url,
            venue="arXiv",
            paper_id=arxiv_id,
        )]
    except Exception:
        return []


# ── Semantic Scholar author search ────────────────────────────────────────────

def search_by_author(author_name: str, limit: int = 15) -> list[dict]:
    """Search for papers by a specific author via Semantic Scholar."""
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            # First find the author
            resp = client.get(
                f"{SS_BASE}/author/search",
                params={"query": author_name, "limit": 3,
                        "fields": "authorId,name,paperCount"},
                headers={"User-Agent": "OrbittSpaceResearchTool/1.0"},
            )
        if resp.status_code != 200:
            return []
        authors_data = resp.json().get("data", [])
        if not authors_data:
            return []

        # Use the top author match
        author_id = authors_data[0]["authorId"]
        author_display = authors_data[0]["name"]

        with httpx.Client(timeout=TIMEOUT) as client:
            resp2 = client.get(
                f"{SS_BASE}/author/{author_id}/papers",
                params={"limit": limit, "fields": SS_FIELDS},
                headers={"User-Agent": "OrbittSpaceResearchTool/1.0"},
            )
        if resp2.status_code != 200:
            return []

        results = []
        for p in resp2.json().get("data", []):
            authors_list = [a.get("name", "") for a in (p.get("authors") or [])[:5]]
            if len(p.get("authors") or []) > 5:
                authors_list.append("et al.")
            authors = ", ".join(authors_list)

            ext = p.get("externalIds") or {}
            doi = ext.get("DOI")
            arxiv_id = ext.get("ArXiv")
            url = p.get("url") or ""
            if arxiv_id:
                url = f"https://arxiv.org/abs/{arxiv_id}"
            elif doi:
                url = f"https://doi.org/{doi}"

            results.append(make_result(
                source=f"Author: {author_display}",
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


# ── Query type detection ──────────────────────────────────────────────────────

def detect_query_type(query: str) -> str:
    """
    Detect what kind of input the user entered.
    Returns: 'doi' | 'arxiv_id' | 'arxiv_url' | 'doi_url' | 'author' | 'keyword'
    """
    q = query.strip()

    # DOI URL
    if re.match(r"https?://(dx\.)?doi\.org/", q):
        return "doi_url"

    # arXiv URL
    if re.match(r"https?://arxiv\.org/(abs|pdf)/", q):
        return "arxiv_url"

    # Raw DOI (starts with 10. and has a slash)
    if re.match(r"^10\.\d{4,}/\S+", q):
        return "doi"

    # arXiv ID patterns: 2301.12345 or cs.RO/0601001 or hep-th/9901001
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", q):
        return "arxiv_id"
    if re.match(r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$", q):
        return "arxiv_id"

    # Author search: "author:Name" prefix
    if q.lower().startswith("author:"):
        return "author"

    return "keyword"


# ── Combined search ───────────────────────────────────────────────────────────

def search_papers(query: str, limit: int = 15) -> list[dict]:
    """
    Smart search: auto-detects query type and routes to the right lookup.
      - DOI or DOI URL  → CrossRef
      - arXiv ID or URL → arXiv direct
      - author:Name     → Semantic Scholar author search
      - anything else   → keyword search across Semantic Scholar + arXiv
    """
    qtype = detect_query_type(query.strip())

    if qtype in ("doi", "doi_url"):
        return lookup_by_doi(query.strip())

    if qtype in ("arxiv_id", "arxiv_url"):
        return lookup_by_arxiv_id(query.strip())

    if qtype == "author":
        author_name = query.strip()[7:].strip()  # strip "author:" prefix
        return search_by_author(author_name, limit=limit)

    # Default: keyword search across both databases
    ss_results    = search_semantic_scholar(query, limit=limit)
    arxiv_results = search_arxiv(query, limit=8)

    ss_titles = {r["title"].lower()[:60] for r in ss_results}
    unique_arxiv = [
        r for r in arxiv_results
        if r["title"].lower()[:60] not in ss_titles
    ]

    combined = ss_results + unique_arxiv
    return combined[:limit + 5]


# ── Enrich metadata via Claude ────────────────────────────────────────────────

import json
import os
import anthropic

from models import (
    AltitudeRange, MethodologyType, MissionType,
    ResearchPaper, VLEORelevance, VLEORelevanceNotes, VLEOScore, ReadingStatus,
)

ENRICH_SYSTEM = """\
You are an expert VLEO (Very Low Earth Orbit) and ABEP research analyst.
Given a paper's title, authors, year, abstract, and venue, return a JSON object
with all structured fields filled in as accurately as possible from the available information.
Since you only have the abstract (not the full paper), be appropriately cautious
with fields like key_results and figures_graphs — mark them as partial.
Apply the 4-criteria VLEO scoring framework rigorously.

For the "tags" field, choose ALL that apply from this canonical list (use exact strings):
  ABEP, VLEO, Propulsion, Plasma, Power Electronics, Missions, Operations,
  Aerodynamics, Atmospheric Drag, Space Debris, Earth Observation, Navigation,
  Communications, Thermal Management, Materials, Orbit Mechanics,
  Collision Avoidance, Space Sustainability, Attitude Control, Hall Effect Thruster,
  Ion Thruster, Solar Cells, Magnetorquer, Drag Compensation, Atomic Oxygen,
  Neutral Gas Ingestion, Ionosphere, Thermosphere, Cubesat, Small Satellite

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
