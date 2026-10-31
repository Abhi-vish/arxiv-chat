import { useEffect, useRef, useState } from 'react'
import { Markdown } from './Markdown'
import { selectionToText } from '../selection'

interface Props {
  markdown: string
  onAsk: (text: string) => void
}

interface Floating {
  top: number
  left: number
  text: string
}

export function PaperView({ markdown, onAsk }: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const [float, setFloat] = useState<Floating | null>(null)

  // Hide the button as soon as the selection is cleared.
  useEffect(() => {
    const onChange = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) setFloat(null)
    }
    document.addEventListener('selectionchange', onChange)
    return () => document.removeEventListener('selectionchange', onChange)
  }, [])

  // Show it after the user finishes selecting (mouse or touch).
  const onPointerUp = () => {
    setTimeout(() => {
      const sel = window.getSelection()
      const root = rootRef.current
      if (!sel || !root || sel.isCollapsed || sel.rangeCount === 0) return setFloat(null)
      const range = sel.getRangeAt(0)
      if (!root.contains(range.commonAncestorContainer)) return setFloat(null)
      const text = selectionToText(range)
      if (!text) return setFloat(null)

      const rect = range.getBoundingClientRect()
      const left = Math.min(Math.max(rect.left + rect.width / 2, 80), window.innerWidth - 80)
      const top = rect.top > 56 ? rect.top - 44 : rect.bottom + 8
      setFloat({ top, left, text })
    }, 0)
  }

  return (
    <div
      ref={rootRef}
      className="h-full overflow-y-auto"
      onPointerUp={onPointerUp}
      onScroll={() => setFloat(null)}
    >
      <article className="prose prose-slate mx-auto max-w-3xl px-8 py-6">
        <Markdown>{markdown}</Markdown>
      </article>

      {float && (
        <button
          style={{ top: float.top, left: float.left }}
          className="fixed z-10 -translate-x-1/2 rounded-full bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white shadow-lg hover:bg-indigo-700"
          onMouseDown={(e) => e.preventDefault()} // keep the selection alive
          onClick={() => {
            onAsk(float.text)
            window.getSelection()?.removeAllRanges()
            setFloat(null)
          }}
        >
          Ask about this
        </button>
      )}
    </div>
  )
}