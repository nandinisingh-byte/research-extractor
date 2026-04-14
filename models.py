"""
Pydantic models matching the Research Papers Notion database schema.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enumerations matching Notion select/multi-select options ──────────────────

class ReadingStatus(str, Enum):
    TO_READ     = "📥 To Read"
    IN_PROGRESS = "📖 In Progress"
    COMPLETED   = "✅ Completed"
    ARCHIVED    = "🗄️ Archived"


class MethodologyType(str, Enum):
    ANALYTICAL    = "Analytical"
    SIMULATION    = "Simulation"
    OBSERVATIONAL = "Observational"
    EXPERIMENTAL  = "Experimental"
    REVIEW        = "Review"


class AltitudeRange(str, Enum):
    VLEO = "VLEO (<450 km)"
    LEO  = "LEO (450–2000 km)"
    MEO  = "MEO"
    GEO  = "GEO"
    MULTIPLE = "Multiple"


class MissionType(str, Enum):
    EARTH_OBSERVATION   = "Earth Observation"
    COMMUNICATIONS      = "Communications"
    SCIENCE             = "Science"
    SPACE_SUSTAINABILITY = "Space Sustainability"
    NAVIGATION          = "Navigation"
    TECHNOLOGY_DEMO     = "Technology Demonstration"
    MILITARY            = "Military"
    COMMERCIAL          = "Commercial"
    OTHER               = "Other"


class VLEORelevance(str, Enum):
    HIGH          = "🔴 High"
    MEDIUM        = "🟡 Medium"
    LOW           = "🟢 Low"
    NOT_ASSESSED  = "⬜ Not Assessed"


# ── VLEO scoring sub-model ────────────────────────────────────────────────────

class VLEOScore(str, Enum):
    FULLY    = "✅"
    PARTIAL  = "⚠️"
    NOT      = "❌"


class VLEORelevanceNotes(BaseModel):
    altitude_coverage:        VLEOScore = Field(..., description="Does the paper study objects/phenomena at <450 km?")
    atmospheric_drag:         VLEOScore = Field(..., description="Does it discuss drag, density, or orbital lifetime at low altitudes?")
    satellite_design_for_vleo: VLEOScore = Field(..., description="Does it cover aerodynamic design, propulsion, or materials for VLEO?")
    collision_debris_risk:    VLEOScore = Field(..., description="Does it assess conjunction rates, debris density, or collision probability at VLEO?")
    verdict:                  str       = Field(..., description="1–2 sentence summary of VLEO relevance for practitioners.")


# ── Main extraction model ─────────────────────────────────────────────────────

class ResearchPaper(BaseModel):
    # ── Core bibliographic fields
    title:               str            = Field(..., description="Full paper title")
    authors:             str            = Field(..., description="All authors, comma-separated")
    year:                Optional[int]  = Field(None, description="Publication year (4-digit integer)")
    journal_conference:  Optional[str]  = Field(None, description="Journal or conference venue name")
    doi_url:             Optional[str]  = Field(None, description="DOI or URL link to the paper")

    # ── Classification
    status:              ReadingStatus  = Field(ReadingStatus.TO_READ, description="Reading status")
    tags:                list[str]      = Field(default_factory=list, description="Topic tags (e.g. VLEO, drag, debris)")
    methodology_type:    Optional[MethodologyType] = Field(None)
    altitude_range:      Optional[AltitudeRange]   = Field(None)
    mission_type:        Optional[MissionType]      = Field(None)
    organization_agency: Optional[str]              = Field(None, description="ESA / NASA / SpaceX / University / etc.")

    # ── Content fields
    aim:                 str            = Field(..., description="Core aim or research question of the paper")
    methodology:         str            = Field(..., description="Study design, datasets, and approach used")
    key_results:         str            = Field(..., description="Critical numerical findings and main outcomes")
    figures_graphs:      Optional[str]  = Field(None, description="Description of key figures/graphs and what they show")
    summary:             str            = Field(..., description="Plain-language concise overview of the paper")
    references:          Optional[str]  = Field(None, description="Key works cited by this paper")

    # ── VLEO-specific scoring
    vleo_relevance:      VLEORelevance      = Field(VLEORelevance.NOT_ASSESSED)
    vleo_relevance_notes: Optional[VLEORelevanceNotes] = Field(None)


class ExtractionResponse(BaseModel):
    """API response returned to the caller."""
    success:       bool
    notion_page_id: Optional[str] = None
    notion_url:    Optional[str]  = None
    paper:         Optional[ResearchPaper] = None
    error:         Optional[str]  = None
