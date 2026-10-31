import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  getContent,
  getPaper,
  streamChat,
  submitPaper,
  type HistoryTurn,
  type PaperStatus,
  type Status,
} from './api'

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

// ------------------------------------------------------------------ paper

export type PaperState =
  | { phase: 'idle' }
  | { phase: 'loading'; status: Status | 'submitting' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; paper: PaperStatus; markdown: string }

const POLL_MS = 1000
const POLL_TIMEOUT_MS = 5 * 60_000
const MAX_POLL_FAILURES = 5

export function usePaper() {
  const [state, setState] = useState<PaperState>({ phase: 'idle' })
  const run = useRef(0) // bump to cancel any in-flight submit loop

  useEffect(
    () => () => {
      run.current++
    },
    [],
  )

  const submit = useCallback(async (url: string) => {
    const id = ++run.current
    const alive = () => id === run.current
    setState({ phase: 'loading', status: 'submitting' })
    try {
      let paper = await submitPaper(url)
      if (!alive()) return

      const deadline = Date.now() + POLL_TIMEOUT_MS
      let failures = 0
      while (paper.status !== 'ready' && paper.status !== 'failed') {
        setState({ phase: 'loading', status: paper.status })
        if (Date.now() > deadline) throw new Error('Timed out while processing the paper.')
        await sleep(POLL_MS)
        if (!alive()) return
        try {
          paper = await getPaper(paper.cache_key)
          failures = 0
        } catch (e) {
          const transient = e instanceof ApiError && (e.status === 0 || e.status >= 500)
          if (transient && ++failures < MAX_POLL_FAILURES) continue
          throw e
        }
      }
      if (paper.status === 'failed') throw new Error(paper.error ?? 'Processing failed.')

      setState({ phase: 'loading', status: 'ready' })
      const { markdown } = await getContent(paper.cache_key)
      if (!alive()) return
      setState({ phase: 'ready', paper, markdown })
    } catch (e) {
      if (!alive()) return
      setState({ phase: 'error', message: e instanceof Error ? e.message : 'Something went wrong.' })
    }
  }, [])

  const reset = useCallback(() => {
    run.current++
    setState({ phase: 'idle' })
  }, [])

  return { state, submit, reset }
}

// ------------------------------------------------------------------- chat

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  selection?: string // highlighted passage attached to a user message
  incomplete?: boolean // stopped, failed, or cut off
  error?: string
}

let counter = 0
const uid = () => String(++counter)

const MAX_HISTORY_MESSAGES = 40 // server allows 50; stays pair-aligned
const MAX_TURN_CHARS = 20_000 // server limit per turn

/** Messages are always [user, assistant] pairs. Pairs whose answer failed are dropped. */
function toHistory(ms: ChatMessage[]): HistoryTurn[] {
  const out: HistoryTurn[] = []
  for (let i = 0; i + 1 < ms.length; i += 2) {
    const u = ms[i]
    const a = ms[i + 1]
    if (a.incomplete || !a.content) continue
    const quoted = u.selection ? `> ${u.selection.replace(/\n/g, '\n> ')}\n\n${u.content}` : u.content
    out.push({ role: 'user', content: quoted.slice(0, MAX_TURN_CHARS) })
    out.push({ role: 'assistant', content: a.content.slice(0, MAX_TURN_CHARS) })
  }
  return out.slice(-MAX_HISTORY_MESSAGES)
}

export function useChat(cacheKey: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(
    async (question: string, selection?: string) => {
      if (abortRef.current) return // one answer at a time

      const history = toHistory(messagesRef.current)
      const userMsg: ChatMessage = { id: uid(), role: 'user', content: question, selection }
      const botMsg: ChatMessage = { id: uid(), role: 'assistant', content: '' }
      setMessages((m) => [...m, userMsg, botMsg])
      setStreaming(true)

      const ctrl = new AbortController()
      abortRef.current = ctrl
      const patch = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((ms) => ms.map((m) => (m.id === botMsg.id ? fn(m) : m)))

      let finished = false
      try {
        for await (const ev of streamChat(cacheKey, { question, selection, history }, ctrl.signal)) {
          if (ev.type === 'token') patch((m) => ({ ...m, content: m.content + ev.text }))
          else if (ev.type === 'done') finished = true
          else patch((m) => ({ ...m, incomplete: true, error: ev.message }))
        }
        if (!finished) {
          patch((m) => (m.error ? m : { ...m, incomplete: true, error: 'The connection was lost.' }))
        }
      } catch (e) {
        if (ctrl.signal.aborted) patch((m) => ({ ...m, incomplete: true }))
        else {
          const message = e instanceof Error ? e.message : 'Something went wrong.'
          patch((m) => ({ ...m, incomplete: true, error: message }))
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
      }
    },
    [cacheKey],
  )

  const stop = useCallback(() => abortRef.current?.abort(), [])

  const clear = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
  }, [])

  return { messages, streaming, send, stop, clear }
}