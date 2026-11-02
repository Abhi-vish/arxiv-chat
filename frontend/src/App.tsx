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
      <header className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white/95 px-3 py-2.5 shadow-sm backdrop-blur supports-[padding:max(0px)]:pt-[max(0.625rem,env(safe-area-inset-top))] sm:gap-3 sm:px-4 sm:py-2">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-xs font-bold text-white shadow-sm sm:h-7 sm:w-7 sm:rounded-lg">
            ar
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold leading-5 text-slate-900">{paper.title}</h1>
            <p className="text-[11px] leading-4 text-slate-500 sm:hidden">arXiv paper</p>
          </div>
        </div>
        <a
          href={`https://arxiv.org/abs/${paper.arxiv_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="max-w-[7rem] truncate rounded-md px-1.5 py-1 text-[11px] font-medium text-slate-500 transition-colors hover:bg-slate-50 hover:text-indigo-700 sm:max-w-none sm:text-xs"
        >
          {paper.arxiv_id}
        </a>
        <button
          className="shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 active:bg-slate-100 sm:rounded-md sm:px-2 sm:py-1"
          onClick={() => {
            setSelection(null)
            reset()
          }}
        >
          New paper
        </button>
      </header>

      <div ref={splitRef} className="flex min-h-0 flex-1">
        <main
          className="hidden h-full min-w-0 md:block"
          style={{ width: `${paperWidth}%` }}
        >
          <PaperView markdown={markdown} onAsk={(t) => setSelection(clipSelection(t))} />
        </main>
        <div
          className="group relative z-10 hidden h-full w-1 shrink-0 cursor-col-resize border-l border-slate-200 bg-white hover:bg-indigo-100 md:block"
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