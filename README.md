# Research Paper Extractor — Railway Deployment

Upload any research paper PDF → auto-extract all fields → push to your Notion database.

---

## Project structure

```
research-extractor/
├── main.py           # FastAPI app (3 endpoints)
├── extractor.py      # PDF text extraction + Claude LLM prompt
├── notion_sync.py    # Notion REST API integration
├── models.py         # Pydantic models for all Notion fields
├── requirements.txt
├── railway.toml      # Railway build + deploy config
├── Procfile
└── .env.example      # Copy to .env for local dev
```

---

## Local setup

```bash
# 1. Clone / open folder in terminal
cd research-extractor

# 2. Create virtual env
python -m venv .venv && source .venv/bin/activate   # Mac/Linux
# OR: .venv\Scripts\activate  (Windows)

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up env vars
cp .env.example .env
# Edit .env with your keys (see below)

# 5. Run
python main.py
# → http://localhost:8000
# → http://localhost:8000/docs  (interactive Swagger UI)
```

---

## Environment variables

| Variable             | Required | Description |
|----------------------|----------|-------------|
| `ANTHROPIC_API_KEY`  | ✅        | Claude API key from console.anthropic.com |
| `NOTION_TOKEN`       | ✅        | Notion integration token (secret_...) |
| `NOTION_DATABASE_ID` | ✅        | Your Research Papers DB ID |
| `API_SECRET_KEY`     | Optional | Bearer token to protect the API |

### Getting your Notion token
1. Go to https://www.notion.so/my-integrations
2. Create a new integration → copy the **Internal Integration Secret**
3. Open your Research Papers database in Notion → **...** menu → **Connections** → add your integration

---

## Deploy to Railway

### Option A — GitHub (recommended)
1. Push this folder to a GitHub repo
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
3. Select the repo → Railway auto-detects Python
4. Go to **Variables** tab and add all 4 env vars
5. Deploy — Railway gives you a public URL like `https://your-app.up.railway.app`

### Option B — Railway CLI
```bash
npm install -g @railway/cli
railway login
railway init          # link to new project
railway up            # deploy
railway variables set ANTHROPIC_API_KEY=sk-ant-...
railway variables set NOTION_TOKEN=secret_...
railway variables set NOTION_DATABASE_ID=5285593e07ec47418bd9f86bb3918f4e
```

---

## API endpoints

### `GET /health`
Health check (used by Railway). Returns `{"status": "ok"}`.

### `POST /extract`
Upload a PDF, get structured JSON back. Does **not** write to Notion.
```bash
curl -X POST https://your-app.up.railway.app/extract \
  -H "Authorization: Bearer YOUR_SECRET_KEY" \
  -F "file=@paper.pdf"
```

### `POST /extract-and-sync`  ← main endpoint
Upload a PDF → extract fields → create Notion page in one step.
```bash
curl -X POST https://your-app.up.railway.app/extract-and-sync \
  -H "Authorization: Bearer YOUR_SECRET_KEY" \
  -F "file=@paper.pdf"
```
Returns:
```json
{
  "success": true,
  "notion_page_id": "...",
  "notion_url": "https://www.notion.so/...",
  "paper": { ... all extracted fields ... }
}
```

### `POST /sync`
Send already-extracted JSON → create Notion page (useful for manual edits before syncing).

---

## Fields extracted

| Field | Notes |
|-------|-------|
| Title, Authors, Year | Bibliographic |
| Journal / Conference | Venue |
| DOI / URL | Link |
| Tags | Multi-select |
| Aim / Research Question | Core objective |
| Methodology + Type | Study design |
| Key Results | Numerical findings |
| Figures & Graphs | Chart descriptions |
| Summary | Plain-language overview |
| References | Key works cited |
| Altitude Range | VLEO / LEO / MEO / GEO |
| Mission Type | Earth Obs / Science / etc. |
| Organization / Agency | ESA / NASA / etc. |
| VLEO Relevance | 🔴 High / 🟡 Medium / 🟢 Low |
| VLEO Relevance Notes | 4-criteria scored assessment |

---

## Interactive docs
After deploying, visit `https://your-app.up.railway.app/docs` for a full Swagger UI where you can test all endpoints directly in the browser.
