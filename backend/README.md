# Backend

FastAPI backend for arXiv paper ingestion and Gemini-powered Q&A.

## Setup

Requirements:

- Python 3.15+
- [uv](https://docs.astral.sh/uv/)
- A [Gemini API key](https://aistudio.google.com/apikey)

From this directory:

```powershell
uv sync
Copy-Item .env.example .env
```

Set `GEMINI_API_KEY` in `.env`, then start the API:

```powershell
uv run uvicorn src.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

The API docs are available at http://127.0.0.1:8000/docs.

Run tests:

```powershell
$env:PYTHONPATH = (Get-Location).Parent.Path
uv run pytest
```