# Frontend

React and TypeScript frontend for reading arXiv papers and asking Gemini-powered questions.

## Setup

Requirements:

- Node.js 20+
- The backend running at `http://127.0.0.1:8000`

From this directory:

```powershell
npm install
npm run dev
```

Open http://localhost:5173.

The Vite development server proxies `/api` requests to the local FastAPI backend.

## Build

```powershell
npm run build
```

## Features

- Load and read parsed arXiv papers.
- Resize the paper and chat panels.
- Highlight passages and ask focused questions.
- Stream Gemini answers in the chat panel.
- Render Markdown, GitHub Flavored Markdown, and LaTeX.
