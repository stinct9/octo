import type { RunEvent, RunSummary, StepInfo } from './types'

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  steps: () => call<StepInfo[]>('GET', '/api/steps'),
  runs: (limit = 10) => call<RunSummary[]>('GET', `/api/runs?limit=${limit}`),
  runEvents: (runId: string) => call<RunEvent[]>('GET', `/api/runs/${runId}/events`),
  start: (steps: string[]) => call<{ run_id: string }>('POST', '/api/runs', { steps }),
  ready: () => call<{ ok: boolean }>('POST', '/api/runs/current/ready'),
  cancel: () => call<{ ok: boolean }>('POST', '/api/runs/current/cancel'),
  recordingUrl: (runId: string) => `/api/runs/${runId}/recording`,
}

/**
 * Live events. EventSource reconnects by itself and sends Last-Event-ID, so the server
 * replays only what was missed; the reducer ignores anything it has already applied.
 */
export function subscribe(handlers: {
  onEvent: (e: RunEvent) => void
  onDevice: (port: string | null) => void
  onConnection: (open: boolean) => void
}): () => void {
  const es = new EventSource('/api/events')
  es.addEventListener('run', (m) => handlers.onEvent(JSON.parse((m as MessageEvent).data)))
  es.addEventListener('device', (m) => handlers.onDevice(JSON.parse((m as MessageEvent).data).port))
  es.onopen = () => handlers.onConnection(true)
  es.onerror = () => handlers.onConnection(false)
  return () => es.close()
}
