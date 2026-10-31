import { useEffect, useRef, useState } from 'react'
import { useChat, type ChatMessage } from '../hooks'
import { MAX_SELECTION_CHARS, type Selection } from '../selection'
import { Markdown } from './Markdown'

interface Props {
  cacheKey: string
  selection: Selection | null
  onClearSelection: () => void
}

export function Chat({ cacheKey, selection, onClearSelection }: Props) {
  const { messages, streaming, send, stop, clear } = useChat(cacheKey)
  const [input, setInput] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true) // autoscroll only while the user is near the bottom
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = listRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  useEffect(() => {
    if (selection) inputRef.current?.focus()
  }, [selection])

  const onScroll = () => {
    const el = listRef.current
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const canSend = !streaming && (input.trim().length > 0 || !!selection)

  const submit = () => {
    if (!canSend) return
    void send(input.trim() || 'Explain this passage.', selection?.text)
    setInput('')
    onClearSelection()
    stickRef.current = true
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
        <h2 className="text-sm font-semibold text-slate-700">Chat</h2>
        {messages.length > 0 && (
          <button className="text-xs text-slate-500 hover:text-slate-800" onClick={clear}>
            Clear
          </button>
        )}
      </div>

      <div ref={listRef} onScroll={onScroll} className="relative min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center px-8 text-center">
            <div className="max-w-sm">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-50 text-2xl text-indigo-600">
                ?
              </div>
              <h3 className="text-base font-semibold text-slate-700">Ask about this paper</h3>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                Ask a question or highlight text in the paper to get a focused explanation.
              </p>
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <Message key={m.id} m={m} live={streaming && i === messages.length - 1} />
          ))
        )}
      </div>

      <div className="border-t border-slate-200 p-3">
        {selection && (
          <div className="mb-2 rounded-md border-l-4 border-indigo-500 bg-indigo-50 p-2 text-xs text-slate-700">
            <div className="flex gap-2">
              <p className="line-clamp-3 flex-1 whitespace-pre-wrap">{selection.text}</p>
              <button
                className="self-start text-slate-400 hover:text-slate-700"
                aria-label="Remove selected text"
                onClick={onClearSelection}
              >
                ✕
              </button>
            </div>
            {selection.truncated && (
              <p className="mt-1 text-amber-700">
                Trimmed to the first {MAX_SELECTION_CHARS.toLocaleString()} characters.
              </p>
            )}
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            rows={2}
            maxLength={2000}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder={selection ? 'Ask about the selected text…' : 'Ask about this paper…'}
            className="flex-1 resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
          />
          {streaming ? (
            <button
              onClick={stop}
              className="rounded-md bg-slate-200 px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-300"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!canSend}
              className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Message({ m, live }: { m: ChatMessage; live: boolean }) {
  if (m.role === 'user') {
    return (
      <div className="ml-6 rounded-lg bg-slate-100 p-3 text-sm text-slate-900">
        {m.selection && (
          <blockquote className="mb-2 line-clamp-4 whitespace-pre-wrap border-l-2 border-indigo-400 pl-2 text-xs text-slate-600">
            {m.selection}
          </blockquote>
        )}
        <p className="whitespace-pre-wrap">{m.content}</p>
      </div>
    )
  }
  return (
    <div className="text-sm text-slate-900">
      {m.content ? (
        <div className="prose prose-sm prose-slate max-w-none">
          <Markdown>{m.content}</Markdown>
        </div>
      ) : (
        live && <p className="text-slate-400">Thinking…</p>
      )}
      {m.error && <p className="mt-2 text-red-600">{m.error}</p>}
      {m.incomplete && !m.error && <p className="mt-2 text-xs text-slate-400">Stopped.</p>}
    </div>
  )
}