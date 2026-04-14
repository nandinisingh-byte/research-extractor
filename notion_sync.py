"""
notion_sync.py — Push a ResearchPaper into the Notion Research Papers database.

Uses the Notion REST API directly (no heavy SDK needed).
Database ID: 5285593e07ec47418bd9f86bb3918f4e
"""

import os
from typing import Optional

import httpx

from models import ResearchPaper

NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE    = "https://api.notion.com/v1"


def _headers() -> dict:
    token = os.environ["NOTION_TOKEN"]
    return {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type":   "application/json",
    }


# ── Property builders ─────────────────────────────────────────────────────────

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text[:2000]}}]}


def _rich_text(text: Optional[str]) -> dict:
    if not text:
        return {"rich_text": []}
    # Notion rich_text blocks are capped at 2000 chars each; split if needed
    chunks = [text[i:i+2000] for i in range(0, min(len(text), 10000), 2000)]
    return {"rich_text": [{"text": {"content": c}} for c in chunks]}


def _select(value: Optional[str]) -> dict:
    if not value:
        return {"select": None}
    return {"select": {"name": value}}


def _multi_select(values: list[str]) -> dict:
    return {"multi_select": [{"name": v[:100]} for v in values if v]}


def _number(value: Optional[int]) -> dict:
    return {"number": value}


def _url(value: Optional[str]) -> dict:
    if not value:
        return {"url": None}
    return {"url": value}


# ── VLEO notes formatter ──────────────────────────────────────────────────────

def _format_vleo_notes(paper: ResearchPaper) -> str:
    notes = paper.vleo_relevance_notes
    if not notes:
        return ""
    lines = [
        f"1. Altitude coverage (<450 km): {notes.altitude_coverage}",
        f"2. Atmospheric drag & orbital decay: {notes.atmospheric_drag}",
        f"3. Satellite design for VLEO: {notes.satellite_design_for_vleo}",
        f"4. Collision & debris risk at VLEO: {notes.collision_debris_risk}",
        "",
        f"Verdict: {notes.verdict}",
    ]
    return "\n".join(lines)


# ── Page body builder ─────────────────────────────────────────────────────────

def _build_page_body(paper: ResearchPaper, database_id: str) -> dict:
    props: dict = {
        "Title":                _title(paper.title),
        "Authors":              _rich_text(paper.authors),
        "Status":               _select(paper.status.value if paper.status else None),
        "Tags":                 _multi_select(paper.tags),
        "Aim / Research Question": _rich_text(paper.aim),
        "Methodology":          _rich_text(paper.methodology),
        "Key Results":          _rich_text(paper.key_results),
        "Summary":              _rich_text(paper.summary),
        "VLEO Relevance":       _select(paper.vleo_relevance.value if paper.vleo_relevance else None),
        "VLEO Relevance Notes": _rich_text(_format_vleo_notes(paper)),
    }

    # Optional fields – only add if values present
    if paper.year:
        props["Year"] = _number(paper.year)
    if paper.journal_conference:
        props["Journal / Conference"] = _rich_text(paper.journal_conference)
    if paper.doi_url:
        props["DOI / URL"] = _url(paper.doi_url)
    if paper.methodology_type:
        props["Methodology Type"] = _select(paper.methodology_type.value)
    if paper.altitude_range:
        props["Altitude Range"] = _select(paper.altitude_range.value)
    if paper.mission_type:
        props["Mission Type"] = _select(paper.mission_type.value)
    if paper.organization_agency:
        props["Organization / Agency"] = _rich_text(paper.organization_agency)
    if paper.figures_graphs:
        props["Figures & Graphs"] = _rich_text(paper.figures_graphs)
    if paper.references:
        props["References"] = _rich_text(paper.references)

    # Page body — add a nicely formatted content block too
    children = _build_page_content(paper)

    return {
        "parent":     {"database_id": database_id},
        "properties": props,
        "children":   children,
    }


def _heading(text: str, level: int = 2) -> dict:
    kind = f"heading_{level}"
    return {
        "object": "block",
        "type":   kind,
        kind: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _paragraph(text: str) -> dict:
    chunks = [text[i:i+2000] for i in range(0, min(len(text), 6000), 2000)]
    return {
        "object": "block",
        "type":   "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]
        },
    }


def _build_page_content(paper: ResearchPaper) -> list[dict]:
    """Inline content blocks rendered inside the Notion page body."""
    blocks = []

    blocks.append(_heading("📌 Aim / Research Question", 2))
    blocks.append(_paragraph(paper.aim))

    blocks.append(_heading("🔬 Methodology", 2))
    blocks.append(_paragraph(paper.methodology))

    blocks.append(_heading("📊 Key Results", 2))
    blocks.append(_paragraph(paper.key_results))

    if paper.figures_graphs:
        blocks.append(_heading("📈 Figures & Graphs", 2))
        blocks.append(_paragraph(paper.figures_graphs))

    blocks.append(_heading("📝 Summary", 2))
    blocks.append(_paragraph(paper.summary))

    if paper.references:
        blocks.append(_heading("📚 References", 2))
        blocks.append(_paragraph(paper.references))

    # VLEO assessment callout
    vleo_text = _format_vleo_notes(paper)
    if vleo_text:
        blocks.append(_heading("🛰️ VLEO Relevance Assessment", 2))
        blocks.append(_paragraph(vleo_text))

    return blocks


# ── Public API ────────────────────────────────────────────────────────────────

def push_to_notion(paper: ResearchPaper) -> tuple[str, str]:
    """
    Create a new page in the Research Papers Notion database.
    Returns (page_id, page_url).
    """
    database_id = os.environ["NOTION_DATABASE_ID"]
    body = _build_page_body(paper, database_id)

    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{NOTION_API_BASE}/pages",
            headers=_headers(),
            json=body,
        )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Notion API error {response.status_code}: {response.text}"
        )

    result = response.json()
    page_id  = result["id"]
    page_url = result.get("url", f"https://www.notion.so/{page_id.replace('-', '')}")
    return page_id, page_url
