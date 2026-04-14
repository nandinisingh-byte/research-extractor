"""
extractor.py — PDF text extraction + Claude-powered structured field extraction.

Flow:
  1. Read raw text from the uploaded PDF via pdfplumber
  2. Send text + structured JSON prompt to Claude claude-sonnet-4-6
  3. Parse the JSON response into a ResearchPaper model
"""

import json
import os
import re
import tempfile
from pathlib import Path

import anthropic
import pdfplumber

from models import (
    AltitudeRange,
    MethodologyType,
    MissionType,
    ResearchPaper,
    VLEORelevance,
    VLEORelevanceNotes,
    VLEOScore,
    ReadingStatus,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

MAX_CHARS = 60_000  # ~15k tokens – well within Claude's context


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Write bytes to a temp file and extract all text via pdfplumber."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        pages = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def truncate(text: str, max_chars: int = MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... text truncated for length ...]"


# ── Extraction prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert research paper analyst specialising in satellite engineering, \
space sustainability, and Very Low Earth Orbit (VLEO) technology. \
Your job is to read academic paper text and return a single JSON object with \
every field populated as accurately and completely as possible.

VLEO is defined as altitudes below 450 km. Apply the 4-criteria scoring framework \
rigorously:
  1. altitude_coverage      — Does the paper study objects/phenomena below 450 km?
  2. atmospheric_drag       — Does it discuss drag forces, density, or orbital lifetime at low altitudes?
  3. satellite_design_for_vleo — Does it cover aerodynamic design, propulsion, or materials for VLEO?
  4. collision_debris_risk  — Does it assess conjunction rates, debris density, or collision probability at VLEO?

Score each criterion as exactly one of: "✅", "⚠️", or "❌".
Map to VLEO relevance:
  • 3–4 ✅  → "🔴 High"
  • 2 ✅ or mix of ✅ and ⚠️ → "🟡 Medium"
  • 0–1 ✅ with mostly ❌   → "🟢 Low"

Return ONLY valid JSON — no markdown fences, no commentary.
"""

USER_PROMPT_TEMPLATE = """\
Extract all fields from the research paper text below and return a JSON object \
that matches this exact schema:

{{
  "title": "string",
  "authors": "comma-separated string",
  "year": integer or null,
  "journal_conference": "string or null",
  "doi_url": "string or null",
  "status": "📥 To Read",
  "tags": ["tag1", "tag2", ...],
  "methodology_type": "Analytical|Simulation|Observational|Experimental|Review or null",
  "altitude_range": "VLEO (<450 km)|LEO (450–2000 km)|MEO|GEO|Multiple or null",
  "mission_type": "Earth Observation|Communications|Science|Space Sustainability|Navigation|Technology Demonstration|Military|Commercial|Other or null",
  "organization_agency": "string or null",
  "aim": "string – core aim or research question",
  "methodology": "string – study design, datasets, approach",
  "key_results": "string – critical numerical findings and main outcomes",
  "figures_graphs": "string – description of key figures/graphs and what they show, or null",
  "summary": "string – plain-language overview (3–5 sentences)",
  "references": "string – key works cited, or null",
  "vleo_relevance": "🔴 High|🟡 Medium|🟢 Low|⬜ Not Assessed",
  "vleo_relevance_notes": {{
    "altitude_coverage": "✅|⚠️|❌",
    "atmospheric_drag": "✅|⚠️|❌",
    "satellite_design_for_vleo": "✅|⚠️|❌",
    "collision_debris_risk": "✅|⚠️|❌",
    "verdict": "1–2 sentence summary of VLEO relevance for practitioners"
  }}
}}

─────────────────────────────────────────
PAPER TEXT:
{paper_text}
─────────────────────────────────────────

Return ONLY the JSON object.
"""


# ── Main extraction function ──────────────────────────────────────────────────

def extract_paper(pdf_bytes: bytes) -> ResearchPaper:
    """
    Full pipeline: PDF bytes → raw text → Claude extraction → ResearchPaper model.
    """
    # 1. Extract raw text
    raw_text = extract_text_from_pdf(pdf_bytes)
    if not raw_text.strip():
        raise ValueError("Could not extract any text from the PDF. It may be scanned/image-based.")

    truncated = truncate(raw_text)

    # 2. Call Claude
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(paper_text=truncated),
            }
        ],
    )

    raw_json = message.content[0].text.strip()

    # Strip accidental markdown fences if present
    raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
    raw_json = re.sub(r"\s*```$", "", raw_json)

    # 3. Parse JSON → model
    data = json.loads(raw_json)
    return _dict_to_model(data)


def _dict_to_model(data: dict) -> ResearchPaper:
    """Convert raw dict from Claude into a validated ResearchPaper."""

    # Safely coerce enums
    def safe_enum(cls, val, default=None):
        if val is None:
            return default
        try:
            return cls(val)
        except ValueError:
            return default

    # Parse VLEO notes sub-object
    vleo_notes_raw = data.get("vleo_relevance_notes")
    vleo_notes = None
    if vleo_notes_raw and isinstance(vleo_notes_raw, dict):
        vleo_notes = VLEORelevanceNotes(
            altitude_coverage=safe_enum(VLEOScore, vleo_notes_raw.get("altitude_coverage"), VLEOScore.NOT),
            atmospheric_drag=safe_enum(VLEOScore, vleo_notes_raw.get("atmospheric_drag"), VLEOScore.NOT),
            satellite_design_for_vleo=safe_enum(VLEOScore, vleo_notes_raw.get("satellite_design_for_vleo"), VLEOScore.NOT),
            collision_debris_risk=safe_enum(VLEOScore, vleo_notes_raw.get("collision_debris_risk"), VLEOScore.NOT),
            verdict=vleo_notes_raw.get("verdict", ""),
        )

    return ResearchPaper(
        title=data.get("title", "Untitled"),
        authors=data.get("authors", ""),
        year=data.get("year"),
        journal_conference=data.get("journal_conference"),
        doi_url=data.get("doi_url"),
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
