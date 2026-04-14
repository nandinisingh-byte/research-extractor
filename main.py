"""
main.py — FastAPI application for research paper extraction.

Endpoints:
  GET  /health              — Railway health check
  GET  /dashboard           — Public web dashboard (no login needed)
  GET  /api/papers          — JSON list of all papers (feeds the dashboard)
  POST /extract             — Upload a PDF, get extracted fields back as JSON
  POST /extract-and-sync    — Upload a PDF, extract fields, push to Notion + save to DB
  POST /extract-only-save   — Upload a PDF, extract fields, save to DB only (no Notion)
  POST /sync                — Send pre-extracted JSON, push to Notion

Authentication:
  If API_SECRET_KEY env var is set, all upload/sync endpoints require
  the header:  Authorization: Bearer <API_SECRET_KEY>
  The dashboard and /api/papers are always public.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

load_dotenv()

from database import get_all_papers, get_paper_count, init_db, save_paper
from extractor import extract_paper
from models import ExtractionResponse, ResearchPaper
from notion_sync import push_to_notion

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Research Paper Extractor",
    description=(
        "Upload a research paper PDF and automatically extract structured fields "
        "(aim, methodology, key results, VLEO scoring, etc.), push to Notion, "
        "and view everything on a public web dashboard."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ── Auth ──────────────────────────────────────────────────────────────────────

bearer_scheme = HTTPBearer(auto_error=False)
API_SECRET_KEY: Optional[str] = os.environ.get("API_SECRET_KEY")


def verify_token(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)):
    if not API_SECRET_KEY:
        return  # No key set → open access
    if credentials is None or credentials.credentials != API_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API secret key.",
        )


# ── Public routes (no auth) ───────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health_check():
    """Railway uses this to confirm the service is up."""
    return {"status": "ok", "service": "research-paper-extractor", "papers": get_paper_count()}


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"], include_in_schema=False)
def dashboard():
    """
    Public web dashboard — lists all extracted papers with search and filters.
    No login required. Share this URL with anyone.
    """
    html_path = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/papers", tags=["Dashboard"])
def api_papers():
    """
    Returns all stored papers as JSON.
    This is what the dashboard reads — also public so anyone can query it.
    """
    return JSONResponse(content=get_all_papers())


# ── Protected routes (require API key if set) ─────────────────────────────────

@app.post(
    "/extract",
    response_model=ExtractionResponse,
    tags=["Extraction"],
    summary="Extract fields from a PDF (preview only — does NOT save or sync)",
)
async def extract_only(
    file: UploadFile = File(..., description="Research paper PDF"),
    _: None = Depends(verify_token),
):
    """
    Upload a PDF and receive extracted fields as JSON.
    Nothing is saved to the database or Notion — useful for previewing.
    """
    _validate_pdf(file)
    pdf_bytes = await file.read()
    _validate_size(pdf_bytes)

    try:
        paper = extract_paper(pdf_bytes)
    except ValueError as e:
        return ExtractionResponse(success=False, error=str(e))
    except Exception as e:
        return ExtractionResponse(success=False, error=f"Extraction failed: {e}")

    return ExtractionResponse(success=True, paper=paper)


@app.post(
    "/extract-and-sync",
    response_model=ExtractionResponse,
    tags=["Extraction"],
    summary="Extract PDF → save to dashboard + push to Notion",
)
async def extract_and_sync(
    file: UploadFile = File(..., description="Research paper PDF"),
    _: None = Depends(verify_token),
):
    """
    Full pipeline:
      1. Extract all fields from the PDF using Claude AI
      2. Push to your Notion Research Papers database
      3. Save to the local database so it appears on the public dashboard

    Returns the Notion page URL and dashboard URL.
    """
    _validate_pdf(file)
    pdf_bytes = await file.read()
    _validate_size(pdf_bytes)

    # Step 1: Extract
    try:
        paper = extract_paper(pdf_bytes)
    except ValueError as e:
        return ExtractionResponse(success=False, error=str(e))
    except Exception as e:
        return ExtractionResponse(success=False, error=f"Extraction failed: {e}")

    # Step 2: Notion sync
    page_id, page_url = None, None
    notion_error = None
    try:
        page_id, page_url = push_to_notion(paper)
    except Exception as e:
        notion_error = str(e)

    # Step 3: Always save to local DB (even if Notion failed)
    try:
        save_paper(paper, notion_page_id=page_id, notion_url=page_url)
    except Exception as e:
        pass  # DB save failing shouldn't block the response

    if notion_error and not page_id:
        return ExtractionResponse(
            success=False,
            paper=paper,
            error=f"Extraction succeeded but Notion sync failed: {notion_error}. Paper saved to dashboard.",
        )

    return ExtractionResponse(
        success=True,
        notion_page_id=page_id,
        notion_url=page_url,
        paper=paper,
    )


@app.post(
    "/extract-only-save",
    response_model=ExtractionResponse,
    tags=["Extraction"],
    summary="Extract PDF → save to dashboard only (no Notion needed)",
)
async def extract_only_save(
    file: UploadFile = File(..., description="Research paper PDF"),
    _: None = Depends(verify_token),
):
    """
    For users without Notion access:
    Extract the PDF and save it to the dashboard database.
    The paper will immediately appear on the public /dashboard page.
    """
    _validate_pdf(file)
    pdf_bytes = await file.read()
    _validate_size(pdf_bytes)

    try:
        paper = extract_paper(pdf_bytes)
    except ValueError as e:
        return ExtractionResponse(success=False, error=str(e))
    except Exception as e:
        return ExtractionResponse(success=False, error=f"Extraction failed: {e}")

    try:
        save_paper(paper)
    except Exception as e:
        return ExtractionResponse(
            success=False, paper=paper,
            error=f"Extraction succeeded but dashboard save failed: {e}",
        )

    return ExtractionResponse(success=True, paper=paper)


@app.post(
    "/sync",
    response_model=ExtractionResponse,
    tags=["Notion"],
    summary="Push a pre-extracted ResearchPaper JSON to Notion + dashboard",
)
async def sync_to_notion(
    paper: ResearchPaper,
    _: None = Depends(verify_token),
):
    """
    If you already have extracted data (e.g. from /extract with manual edits),
    send it here to create a Notion page and save to the dashboard.
    """
    page_id, page_url = None, None
    try:
        page_id, page_url = push_to_notion(paper)
    except Exception as e:
        return ExtractionResponse(success=False, paper=paper, error=str(e))

    try:
        save_paper(paper, notion_page_id=page_id, notion_url=page_url)
    except Exception:
        pass

    return ExtractionResponse(
        success=True,
        notion_page_id=page_id,
        notion_url=page_url,
        paper=paper,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_pdf(file: UploadFile):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")


def _validate_size(pdf_bytes: bytes):
    if len(pdf_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF must be under 50 MB.")


# ── Dev runner ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
