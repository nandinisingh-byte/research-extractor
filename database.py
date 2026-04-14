"""
database.py — Local SQLite storage for extracted papers.

Every paper that passes through /extract-and-sync or /sync is saved here
so the public dashboard can display them without requiring Notion access.

Railway note: SQLite is stored at /data/papers.db
  → Add a Railway Volume mounted at /data for persistence across deploys.
  → Or swap DATABASE_URL to a Railway Postgres URL — SQLAlchemy handles both.
"""

import json
import os
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session

from models import ResearchPaper, VLEORelevanceNotes

# ── DB setup ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/papers.db")

# Railway Postgres URLs use postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)


class Base(DeclarativeBase):
    pass


class PaperRecord(Base):
    __tablename__ = "papers"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    added_at            = Column(DateTime, default=datetime.utcnow)

    # Bibliographic
    title               = Column(String(500), nullable=False)
    authors             = Column(String(1000))
    year                = Column(Integer)
    journal_conference  = Column(String(500))
    doi_url             = Column(String(500))

    # Classification
    tags                = Column(String(500))   # stored as comma-separated
    methodology_type    = Column(String(100))
    altitude_range      = Column(String(100))
    mission_type        = Column(String(200))
    organization_agency = Column(String(200))
    status              = Column(String(100))

    # Content
    aim                 = Column(Text)
    methodology         = Column(Text)
    key_results         = Column(Text)
    figures_graphs      = Column(Text)
    summary             = Column(Text)
    references          = Column(Text)

    # VLEO
    vleo_relevance      = Column(String(50))
    vleo_relevance_notes = Column(Text)   # stored as JSON

    # Notion link (if synced)
    notion_page_id      = Column(String(200))
    notion_url          = Column(String(500))


def init_db():
    """Create tables if they don't exist. Call once on startup."""
    os.makedirs("/data", exist_ok=True)
    Base.metadata.create_all(engine)


def save_paper(paper: ResearchPaper, notion_page_id: str = None, notion_url: str = None) -> int:
    """Persist a ResearchPaper to the database. Returns the new record ID."""
    notes_json = None
    if paper.vleo_relevance_notes:
        notes = paper.vleo_relevance_notes
        notes_json = json.dumps({
            "altitude_coverage":         notes.altitude_coverage.value,
            "atmospheric_drag":          notes.atmospheric_drag.value,
            "satellite_design_for_vleo": notes.satellite_design_for_vleo.value,
            "collision_debris_risk":     notes.collision_debris_risk.value,
            "verdict":                   notes.verdict,
        })

    record = PaperRecord(
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        journal_conference=paper.journal_conference,
        doi_url=paper.doi_url,
        tags=", ".join(paper.tags) if paper.tags else "",
        methodology_type=paper.methodology_type.value if paper.methodology_type else None,
        altitude_range=paper.altitude_range.value if paper.altitude_range else None,
        mission_type=paper.mission_type.value if paper.mission_type else None,
        organization_agency=paper.organization_agency,
        status=paper.status.value if paper.status else None,
        aim=paper.aim,
        methodology=paper.methodology,
        key_results=paper.key_results,
        figures_graphs=paper.figures_graphs,
        summary=paper.summary,
        references=paper.references,
        vleo_relevance=paper.vleo_relevance.value if paper.vleo_relevance else None,
        vleo_relevance_notes=notes_json,
        notion_page_id=notion_page_id,
        notion_url=notion_url,
    )

    with Session(engine) as session:
        session.add(record)
        session.commit()
        return record.id


def get_all_papers() -> list[dict]:
    """Return all papers as dicts (for the dashboard)."""
    with Session(engine) as session:
        records = session.query(PaperRecord).order_by(PaperRecord.added_at.desc()).all()
        result = []
        for r in records:
            notes = None
            if r.vleo_relevance_notes:
                try:
                    notes = json.loads(r.vleo_relevance_notes)
                except Exception:
                    pass

            result.append({
                "id":                   r.id,
                "added_at":             r.added_at.strftime("%Y-%m-%d") if r.added_at else "",
                "title":                r.title or "",
                "authors":              r.authors or "",
                "year":                 r.year,
                "journal_conference":   r.journal_conference or "",
                "doi_url":              r.doi_url or "",
                "tags":                 r.tags or "",
                "methodology_type":     r.methodology_type or "",
                "altitude_range":       r.altitude_range or "",
                "mission_type":         r.mission_type or "",
                "organization_agency":  r.organization_agency or "",
                "status":               r.status or "",
                "aim":                  r.aim or "",
                "methodology":          r.methodology or "",
                "key_results":          r.key_results or "",
                "figures_graphs":       r.figures_graphs or "",
                "summary":              r.summary or "",
                "references":           r.references or "",
                "vleo_relevance":       r.vleo_relevance or "",
                "vleo_relevance_notes": notes,
                "notion_url":           r.notion_url or "",
            })
        return result


def get_paper_count() -> int:
    with Session(engine) as session:
        return session.query(PaperRecord).count()
