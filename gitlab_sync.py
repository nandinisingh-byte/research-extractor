"""
gitlab_sync.py — Auto-commit each paper to a GitLab repository.

For every paper added (via PDF upload or Discover search), this module
pushes three files to GitLab in a single commit:

  papers/<date>_<slug>/
    data.json      — all extracted fields as structured JSON
    summary.md     — human-readable markdown summary
    paper.pdf      — original PDF (only when uploaded via PDF endpoint)

Environment variables required (set in Railway):
  GITLAB_TOKEN       — Personal Access Token with 'api' scope
  GITLAB_PROJECT_ID  — numeric project ID (shown under project name in GitLab)

Optional:
  GITLAB_BRANCH      — branch to commit to (default: main)
  GITLAB_BASE_PATH   — folder prefix inside the repo (default: papers)
"""

import base64
import json
import os
import re
from datetime import datetime

import httpx

GITLAB_API = "https://gitlab.com/api/v4"
TIMEOUT = 20


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str, max_len: int = 50) -> str:
    """Convert a title to a safe folder name."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len]


def _headers() -> dict:
    token = os.environ.get("GITLAB_TOKEN", "")
    return {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}


def _project_id() -> str:
    return os.environ.get("GITLAB_PROJECT_ID", "")


def _branch() -> str:
    return os.environ.get("GITLAB_BRANCH", "main")


def _base_path() -> str:
    return os.environ.get("GITLAB_BASE_PATH", "papers").strip("/")


# ── Content builders ──────────────────────────────────────────────────────────

def _build_json(paper_dict: dict) -> str:
    """Serialise the paper dict to pretty JSON."""
    return json.dumps(paper_dict, indent=2, ensure_ascii=False, default=str)


def _build_markdown(paper_dict: dict) -> str:
    """Build a readable Markdown summary for the paper."""
    p = paper_dict
    vleo = p.get("vleo_relevance", "⬜ Not Assessed")
    notes = p.get("vleo_relevance_notes") or {}

    lines = [
        f"# {p.get('title', 'Untitled')}",
        "",
        f"**Authors:** {p.get('authors', '—')}  ",
        f"**Year:** {p.get('year', '—')}  ",
        f"**Journal / Conference:** {p.get('journal_conference', '—')}  ",
        f"**DOI / URL:** {p.get('doi_url', '—')}  ",
        f"**Status:** {p.get('status', '—')}  ",
        f"**Tags:** {p.get('tags', '—')}  ",
        f"**Methodology:** {p.get('methodology_type', '—')}  ",
        f"**Altitude Range:** {p.get('altitude_range', '—')}  ",
        f"**Mission Type:** {p.get('mission_type', '—')}  ",
        f"**Organisation:** {p.get('organization_agency', '—')}  ",
        "",
        "---",
        "",
        "## Aim",
        p.get("aim", "—"),
        "",
        "## Methodology",
        p.get("methodology", "—"),
        "",
        "## Key Results",
        p.get("key_results", "—"),
        "",
        "## Summary",
        p.get("summary", "—"),
    ]

    if p.get("figures_graphs"):
        lines += ["", "## Figures & Graphs", p["figures_graphs"]]

    if p.get("references"):
        lines += ["", "## References", p["references"]]

    lines += [
        "",
        "---",
        "",
        "## VLEO Relevance",
        f"**Overall:** {vleo}",
        "",
        f"| Criterion | Score |",
        f"|-----------|-------|",
        f"| Altitude coverage | {notes.get('altitude_coverage', '—')} |",
        f"| Atmospheric drag | {notes.get('atmospheric_drag', '—')} |",
        f"| Satellite design for VLEO | {notes.get('satellite_design_for_vleo', '—')} |",
        f"| Collision / debris risk | {notes.get('collision_debris_risk', '—')} |",
    ]

    if notes.get("verdict"):
        lines += ["", f"> {notes['verdict']}"]

    if p.get("notion_url"):
        lines += ["", "---", f"[Open in Notion]({p['notion_url']})"]

    lines += [
        "",
        "---",
        f"*Added: {p.get('added_at', datetime.utcnow().strftime('%Y-%m-%d'))} · "
        f"Orbitt Space Research Intelligence Platform*",
    ]

    return "\n".join(lines)


# ── Main sync function ────────────────────────────────────────────────────────

def push_paper_to_gitlab(
    paper_dict: dict,
    pdf_bytes: bytes | None = None,
) -> tuple[bool, str]:
    """
    Commit the paper to GitLab. Returns (success, url_or_error_message).

    paper_dict  — the paper as a plain dict (from get_paper_by_id or similar)
    pdf_bytes   — raw PDF bytes if available (from an upload endpoint)
    """
    token = os.environ.get("GITLAB_TOKEN", "")
    project_id = _project_id()

    if not token or not project_id:
        return False, "GITLAB_TOKEN or GITLAB_PROJECT_ID not set"

    title = paper_dict.get("title", "untitled")
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    slug = f"{date_str}_{_slugify(title)}"
    folder = f"{_base_path()}/{slug}"

    # Build file actions for the batch commit
    actions = [
        {
            "action": "create",
            "file_path": f"{folder}/data.json",
            "content": _build_json(paper_dict),
            "encoding": "text",
        },
        {
            "action": "create",
            "file_path": f"{folder}/summary.md",
            "content": _build_markdown(paper_dict),
            "encoding": "text",
        },
    ]

    if pdf_bytes:
        actions.append({
            "action": "create",
            "file_path": f"{folder}/paper.pdf",
            "content": base64.b64encode(pdf_bytes).decode("utf-8"),
            "encoding": "base64",
        })

    commit_message = f"Add paper: {title[:80]}"

    payload = {
        "branch": _branch(),
        "commit_message": commit_message,
        "actions": actions,
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(
                f"{GITLAB_API}/projects/{project_id}/repository/commits",
                headers=_headers(),
                json=payload,
            )

        if resp.status_code in (200, 201):
            commit_data = resp.json()
            web_url = commit_data.get("web_url", "")
            # Construct tree URL for the paper folder
            tree_url = (
                f"https://gitlab.com/api/v4/projects/{project_id}/"
                f"repository/tree?path={folder}"
            )
            # Better: build the human-readable GitLab URL
            # We'll return the commit URL — good enough for linking
            return True, web_url
        else:
            err = resp.json().get("message", resp.text)
            return False, f"GitLab API error {resp.status_code}: {err}"

    except Exception as e:
        return False, f"GitLab sync failed: {e}"
