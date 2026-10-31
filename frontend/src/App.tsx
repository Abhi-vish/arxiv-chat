import { useRef, useState } from 'react'
import { Chat } from './components/Chat'
import { Landing } from './components/Landing'
import { PaperView } from './components/PaperView'
import { usePaper } from './hooks'
import { clipSelection, type Selection } from './selection'

export default function App() {
  const { state, submit, reset } = usePaper()
  const [selection, setSelection] = useState<Selection | null>(null)
  const [paperWidth, setPaperWidth] = useState(60)
  const splitRef = useRef<HTMLDivElement>(null)

  const resizePanels = (clientX: number) => {
    const bounds = splitRef.current?.getBoundingClientRect()
    if (!bounds) return
    const nextWidth = ((clientX - bounds.left) / bounds.width) * 100
    setPaperWidth(Math.min(80, Math.max(35, nextWidth)))
  }

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

      <div ref={splitRef} className="flex min-h-0 flex-1">
        <main className="h-full min-w-0" style={{ width: `${paperWidth}%` }}>
          <PaperView markdown={markdown} onAsk={(t) => setSelection(clipSelection(t))} />
        </main>
        <div
          className="group relative z-10 h-full w-1 shrink-0 cursor-col-resize border-l border-slate-200 bg-white hover:bg-indigo-100"
          role="separator"
          aria-label="Resize paper and chat panels"
          aria-orientation="vertical"
          aria-valuemin={35}
          aria-valuemax={80}
          aria-valuenow={paperWidth}
          tabIndex={0}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId)
            resizePanels(event.clientX)
          }}
          onPointerMove={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              resizePanels(event.clientX)
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
              event.preventDefault()
              const delta = event.key === 'ArrowLeft' ? -2 : 2
              setPaperWidth((width) => Math.min(80, Math.max(35, width + delta)))
            }
          }}
        >
          <div className="absolute inset-y-1/2 -left-1 h-10 w-2 -translate-y-1/2 rounded-full bg-slate-300 opacity-0 transition-opacity group-hover:opacity-100 group-focus:opacity-100" />
        </div>
        <aside className="h-full min-w-0 flex-1">
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