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

All endpoints are open — no API key required.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

load_dotenv()

from database import delete_paper, get_all_papers, get_paper_count, get_paper_by_id, init_db, save_paper
from exports import generate_all_docx, generate_all_pdf, generate_docx, generate_pdf
from extractor import extract_paper
from models import ExtractionResponse, ResearchPaper
from notion_sync import push_to_notion
from search import enrich_from_metadata, search_papers

# ── App setup ──────────────────────────────────────────────────────────────────

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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health_check():
    """Railway uses this to confirm the service is up."""
    return {"status": "ok", "service": "research-paper-extractor", "papers": get_paper_count()}


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"], include_in_schema=False)
def dashboard():
    """Public web dashboard — lists all extracted papers. Share with anyone."""
    html_path = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/papers", tags=["Dashboard"])
def api_papers():
    """Returns all stored papers as JSON. Used by the dashboard."""
    return JSONResponse(content=get_all_papers())


@app.delete("/api/papers/{paper_id}", tags=["Dashboard"], summary="Delete a paper from the database")
def delete_paper_endpoint(paper_id: int):
    """Permanently delete a paper by ID."""
    removed = delete_paper(paper_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Paper not found")
    return {"success": True, "deleted_id": paper_id}


@app.post(
    "/extract",
    response_model=ExtractionResponse,
    tags=["Extraction"],
    summary="Extract fields from a PDF (preview only — does NOT save)",
)
async def extract_only(
    file: UploadFile = File(..., description="Research paper PDF"),
):
    """Upload a PDF and receive extracted fields as JSON. Nothing is saved."""
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
):
    """Extract all fields, push to Notion, and save to the dashboard."""
    _validate_pdf(file)
    pdf_bytes = await file.read()
    _validate_size(pdf_bytes)

    try:
        paper = extract_paper(pdf_bytes)
    except ValueError as e:
        return ExtractionResponse(success=False, error=str(e))
    except Exception as e:
        return ExtractionResponse(success=False, error=f"Extraction failed: {e}")

    page_id, page_url = None, None
    notion_error = None
    try:
        page_id, page_url = push_to_notion(paper)
    except Exception as e:
        notion_error = str(e)

    try:
        save_paper(paper, notion_page_id=page_id, notion_url=page_url)
    except Exception:
        pass

    if notion_error and not page_id:
        return ExtractionResponse(
            success=False, paper=paper,
            error=f"Extraction succeeded but Notion sync failed: {notion_error}. Paper saved to dashboard.",
        )

    return ExtractionResponse(success=True, notion_page_id=page_id, notion_url=page_url, paper=paper)


@app.post(
    "/extract-only-save",
    response_model=ExtractionResponse,
    tags=["Extraction"],
    summary="Extract PDF → save to dashboard only (no Notion needed)",
)
async def extract_only_save(
    file: UploadFile = File(..., description="Research paper PDF"),
):
    """Extract the PDF and save to the dashboard. No Notion required."""
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
async def sync_to_notion(paper: ResearchPaper):
    """Send already-extracted data to Notion and save to the dashboard."""
    page_id, page_url = None, None
    try:
        page_id, page_url = push_to_notion(paper)
    except Exception as e:
        return ExtractionResponse(success=False, paper=paper, error=str(e))

    try:
        save_paper(paper, notion_page_id=page_id, notion_url=page_url)
    except Exception:
        pass

    return ExtractionResponse(success=True, notion_page_id=page_id, notion_url=page_url, paper=paper)


# ── Search & discover endpoints ────────────────────────────────────────────────

@app.get("/api/search", tags=["Search"])
def search_endpoint(q: str, limit: int = 15):
    """
    Search Semantic Scholar + arXiv for papers matching the query.
    Returns a list of results with title, authors, year, abstract, URL.
    No papers are saved — use /api/add-from-search to add one.
    """
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters.")
    results = search_papers(q.strip(), limit=limit)
    return JSONResponse(content={"results": results, "count": len(results)})


@app.post("/api/add-from-search", tags=["Search"])
async def add_from_search(body: dict):
    """
    Takes a search result dict, uses Claude to enrich the metadata into
    full structured fields, then saves to the dashboard database.
    Optionally also pushes to Notion if NOTION_TOKEN is set.
    """
    try:
        paper = enrich_from_metadata(body)
    except Exception as e:
        return ExtractionResponse(success=False, error=f"Enrichment failed: {e}")

    page_id, page_url = None, None
    notion_token = os.environ.get("NOTION_TOKEN")
    if notion_token:
        try:
            page_id, page_url = push_to_notion(paper)
        except Exception:
            pass  # Notion failure shouldn't block dashboard save

    try:
        save_paper(paper, notion_page_id=page_id, notion_url=page_url)
    except Exception as e:
        return ExtractionResponse(success=False, paper=paper,
                                  error=f"Enrichment succeeded but save failed: {e}")

    return ExtractionResponse(success=True, notion_page_id=page_id,
                              notion_url=page_url, paper=paper)


# ── Export endpoints ───────────────────────────────────────────────────────────

@app.get("/api/papers/{paper_id}/export/pdf", tags=["Export"],
         summary="Download a single paper as a PDF report")
def export_paper_pdf(paper_id: int):
    paper = get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    filename = paper["title"][:60].replace(" ", "_").replace("/", "-") + ".pdf"
    return Response(
        content=generate_pdf(paper),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/papers/{paper_id}/export/docx", tags=["Export"],
         summary="Download a single paper as a Word document")
def export_paper_docx(paper_id: int):
    paper = get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    filename = paper["title"][:60].replace(" ", "_").replace("/", "-") + ".docx"
    return Response(
        content=generate_docx(paper),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/papers/export/pdf", tags=["Export"],
         summary="Download all papers as a combined PDF report")
def export_all_pdf():
    papers = get_all_papers()
    if not papers:
        raise HTTPException(status_code=404, detail="No papers in database")
    return Response(
        content=generate_all_pdf(papers),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="research_papers_export.pdf"'},
    )


@app.get("/api/papers/export/docx", tags=["Export"],
         summary="Download all papers as a combined Word document")
def export_all_docx():
    papers = get_all_papers()
    if not papers:
        raise HTTPException(status_code=404, detail="No papers in database")
    return Response(
        content=generate_all_docx(papers),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="research_papers_export.docx"'},
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _validate_pdf(file: UploadFile):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")


def _validate_size(pdf_bytes: bytes):
    if len(pdf_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF must be under 50 MB.")


# ── Dev runner ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
