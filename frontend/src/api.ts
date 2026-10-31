const BASE = '/api'

export type Status = 'queued' | 'fetching' | 'parsing' | 'ready' | 'failed'

export interface PaperStatus {
  cache_key: string
  arxiv_id: string
  title: string
  authors: string[]
  status: Status
  error: string | null
  source_kind: string | null
  token_count: number | null
}

export interface HistoryTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatBody {
  question: string
  selection?: string
  history: HistoryTurn[]
}

export type ChatEvent =
  | { type: 'token'; text: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export class ApiError extends Error {
  status: number // 0 = network failure
  retryAfter?: number

  constructor(status: number, message: string, retryAfter?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status}).`
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') message = body.detail
    else if (res.status === 422) message = 'Please check your input and try again.'
  } catch {
    /* non-JSON error body */
  }
  const ra = Number(res.headers.get('retry-after'))
  const retryAfter = Number.isFinite(ra) && ra > 0 ? ra : undefined
  if (res.status === 429 && retryAfter) message += ` Try again in ${retryAfter}s.`
  return new ApiError(res.status, message, retryAfter)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the server.')
  }
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as T
}

const enc = encodeURIComponent

export const submitPaper = (url: string) =>
  request<PaperStatus>('/papers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })

export const getPaper = (key: string) => request<PaperStatus>(`/papers/${enc(key)}`)

export const getContent = (key: string) =>
  request<{ cache_key: string; markdown: string }>(`/papers/${enc(key)}/content`)

function parseBlock(block: string): ChatEvent | null {
  let name = ''
  let data = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event: ')) name = line.slice(7)
    else if (line.startsWith('data: ')) data += line.slice(6)
  }
  if (!name) return null
  let payload: { text?: string; message?: string } = {}
  try {
    payload = data ? JSON.parse(data) : {}
  } catch {
    return null
  }
  if (name === 'token') return { type: 'token', text: payload.text ?? '' }
  if (name === 'done') return { type: 'done' }
  if (name === 'error') return { type: 'error', message: payload.message ?? 'The answer failed.' }
  return null
}

/** POST + SSE (EventSource cannot POST). Throws ApiError for non-2xx responses. */
export async function* streamChat(
  key: string,
  body: ChatBody,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  let res: Response
  try {
    res = await fetch(`${BASE}/papers/${enc(key)}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if (signal.aborted) throw e
    throw new ApiError(0, 'Cannot reach the server.')
  }
  if (!res.ok) throw await toApiError(res)
  if (!res.body) throw new ApiError(0, 'Streaming is not supported by this browser.')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const block = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const ev = parseBlock(block)
        if (ev) yield ev
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}