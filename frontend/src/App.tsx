import { useState } from 'react'
import { Chat } from './components/Chat'
import { Landing } from './components/Landing'
import { PaperView } from './components/PaperView'
import { usePaper } from './hooks'
import { clipSelection, type Selection } from './selection'

export default function App() {
  const { state, submit, reset } = usePaper()
  const [selection, setSelection] = useState<Selection | null>(null)

  if (state.phase !== 'ready') return <Landing state={state} onSubmit={submit} />

  const { paper, markdown } = state

  return (
    <div className="flex h-dvh flex-col bg-white text-slate-900">
      <header className="flex items-center gap-3 border-b border-slate-200 px-4 py-2">
        <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">{paper.title}</h1>
        <a
          href={`https://arxiv.org/abs/${paper.arxiv_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          {paper.arxiv_id}
        </a>
        <button
          className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
          onClick={() => {
            setSelection(null)
            reset()
          }}
        >
          New paper
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="h-full w-[60%] min-w-0">
          <PaperView markdown={markdown} onAsk={(t) => setSelection(clipSelection(t))} />
        </main>
        <aside className="h-full w-[40%] min-w-0 border-l border-slate-200">
          <Chat
            key={paper.cache_key}
            cacheKey={paper.cache_key}
            selection={selection}
            onClearSelection={() => setSelection(null)}
          />
        </aside>
      </div>
    </div>
  )
}