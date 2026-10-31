# arXiv Paper Q&A

Paste an arXiv link, read the paper on the left, and chat with it on the right. Highlight any passage to ask about exactly that text.

## Features

- **Paste any arXiv link.** Accepts `/abs/`, `/pdf/`, `/html/`, versioned IDs (`v2`), bare IDs, and old-style IDs like `hep-th/9901001`.
- **Clean paper view.** Uses arXiv's HTML version when available (math kept as LaTeX, rendered with KaTeX) and falls back to PDF extraction.
- **Select to ask.** Highlight text in the paper and click "Ask about this". The passage is attached to your question.
- **Grounded, streamed answers.** The model answers from the paper only, cites sections, and says so when the paper doesn't cover something.
- **Follow-up questions.** Chat history is sent with each question.
- **Cached ingestion.** Each paper is parsed once. Pasting the same link again is instant, and concurrent requests for the same paper start one job.

## How it works

```
arXiv URL
  -> parse ID, fetch metadata (resolves latest version)
  -> fetch HTML (fallback: PDF) -> parse to markdown -> count tokens -> store (SQLite)

Question (+ optional highlighted passage)
  -> full paper as a cached system block + chat history -> Gemini -> SSE stream
```

v1 sends the **whole paper** to the model instead of using RAG. A typical paper is 10-30k tokens, so this is simpler and usually more accurate than chunked retrieval. Prompt caching makes follow-up questions cheap. The answering code sits behind an `AnswerStrategy` interface, so a RAG strategy can be added later (multi-paper chat, very long papers) without touching ingestion or the API.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, SSE streaming |
| LLM | Gemini via the Google GenAI API |
| Parsing | BeautifulSoup + markdownify (HTML), PyMuPDF4LLM (PDF) |
| Storage | SQLite (single file) |
| Frontend | Vite, React, TypeScript, Tailwind CSS |
| Rendering | react-markdown, remark-math, rehype-katex |

## Project structure

```
arxiv-chat/
├── backend/
│   ├── src/
│   │   ├── main.py            # FastAPI app, routes, rate limits
│   │   ├── config.py          # settings from env vars
│   │   ├── schemas.py         # request/response models
│   │   ├── llm.py             # Gemini client: streaming and token counting
│   │   ├── store.py           # SQLite: papers, status, parsed markdown
│   │   ├── ingestion/
│   │   │   ├── arxiv.py       # URL parsing, metadata, fetch (rate limited, retries)
│   │   │   ├── parser.py      # HTML/PDF -> markdown
│   │   │   └── pipeline.py    # fetch -> parse -> size check -> store
│   │   └── qa/
│   │       ├── base.py        # AnswerStrategy protocol (the RAG seam)
│   │       ├── full_context.py
│   │       └── prompts.py     # versioned system prompt
│   ├── tests/
│   └── streamlit_app.py       # Streamlit UI for end-to-end testing
└── frontend/
    └── src/
        ├── api.ts             # fetch wrappers + SSE parser
        ├── hooks.ts           # usePaper (submit + poll), useChat (stream)
        ├── selection.ts       # highlighted text -> clean text (math-aware)
        └── components/        # Landing, PaperView, Chat, Markdown
```

## Getting started

**Requirements:** Python 3.15+, Node 20+, a [Gemini API key](https://aistudio.google.com/apikey).

### Backend

```bash
cd backend
# install dependencies from pyproject.toml (uv sync, or pip install -e ".[dev]")
copy .env.example .env      # then edit: GEMINI_API_KEY, ARXIV_USER_AGENT
uv run uvicorn src.main:create_app --factory --reload
```

API docs at http://localhost:8000/docs.

### Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173
```

The dev server proxies `/api` to `localhost:8000`, so no CORS setup is needed in development.

### Tests

```bash
cd backend
pytest -q
```

Tests use saved fixtures and fakes. They never call arXiv or the Gemini API.

## Configuration

Set in `backend/.env`:

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | required | |
| `LLM_MODEL` | `gemini-2.5-flash` | Any Gemini model your key can use |
| `ARXIV_USER_AGENT` | placeholder | arXiv asks API clients to identify themselves. Put a real contact. |
| `DB_PATH` | `data/papers.db` | SQLite file, created on first run |
| `MAX_PAPER_TOKENS` | `150000` | Papers above this fail at ingestion |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | JSON list. Set to your frontend origin when deployed. |

## API

| Route | Purpose |
|---|---|
| `POST /papers` `{url}` | Start ingestion. `202` if processing, `200` if already cached |
| `GET /papers/{key}` | Status: `queued`, `fetching`, `parsing`, `ready`, `failed` |
| `GET /papers/{key}/content` | Parsed markdown of a ready paper |
| `POST /papers/{key}/chat` `{question, selection?, history}` | SSE stream of answer tokens |
| `GET /health` | Liveness check |

Chat events: `token {"text"}` (many), then `done {}` or `error {"message"}`. The client sends chat history with every request, so the server stores no conversations.

## Design notes

- **Parse quality sets the ceiling.** HTML first, PDF as fallback. Math survives only on the HTML path.
- **Paper text is untrusted.** It is wrapped in tags with escaping, and the prompt tells the model to ignore instructions found inside the paper.
- **Errors are user-safe.** Upstream details go to logs, never to API responses.
- **One shared arXiv client** keeps total traffic under arXiv's rate limit (about one request per 3 seconds).

## License

Add a license (for example MIT) before publishing.