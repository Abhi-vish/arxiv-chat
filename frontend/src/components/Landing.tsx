import { useState } from 'react'
import type { PaperState } from '../hooks'

const LABELS: Record<string, string> = {
  submitting: 'Checking the link…',
  queued: 'Queued…',
  fetching: 'Downloading from arXiv…',
  parsing: 'Extracting text…',
  ready: 'Loading paper…',
}

export function Landing({
  state,
  onSubmit,
}: {
  state: PaperState
  onSubmit: (url: string) => void
}) {
  const [url, setUrl] = useState('')
  const loading = state.phase === 'loading'

  return (
    <div className="flex min-h-dvh items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-xl">
        <h1 className="text-2xl font-semibold text-slate-900">Ask questions about an arXiv paper</h1>
        <p className="mt-1 text-sm text-slate-500">Paste a link. Read it on the left, chat on the right.</p>

        <form
          className="mt-6 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (url.trim() && !loading) onSubmit(url.trim())
          }}
        >
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={loading}
            placeholder="https://arxiv.org/abs/1706.03762"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:bg-slate-100"
          />
          <button
            type="submit"
            disabled={loading || !url.trim()}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            Load
          </button>
        </form>

        {state.phase === 'loading' && (
          <p className="mt-4 flex items-center gap-2 text-sm text-slate-600">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
            {LABELS[state.status] ?? 'Working…'}
          </p>
        )}
        {state.phase === 'error' && <p className="mt-4 text-sm text-red-600">{state.message}</p>}
      </div>
    </div>
  )
}