import type { Incoming, PersonPage, Progress, SessionDetail, SessionSummary } from './types'

/** What the last visit to a page showed, kept while the app is open: going back shows it at once, and the page then
 *  asks again (28 Oct: going back and forth between conversations took a long time, with "Loading…" every time). */
export const kept = {
  list: null as { sessions: SessionSummary[]; older: number; aside: number; incoming: Incoming[] } | null,
  conversations: new Map<string, SessionDetail>(),
  progress: null as Progress | null,  // 4 Nov: Progress and a person's page too, ~3 s each on a busy Mac
  people: new Map<string, PersonPage>(),
}

const CONVERSATIONS = 12

/** Keeps a conversation as last seen, the most recent CONVERSATIONS of them. */
export function keepConversation(c: SessionDetail): void {
  kept.conversations.delete(c.id)
  kept.conversations.set(c.id, c)
  // Back on the list, its row is where it now is (3 Nov: a conversation tried again showed as needing you, then left).
  const row = kept.list?.sessions.find((r) => r.id === c.id)
  if (row) row.todo = c.todo
  for (const id of kept.conversations.keys()) {
    if (kept.conversations.size <= CONVERSATIONS) break
    kept.conversations.delete(id)
  }
}

/** A conversation that's gone (deleted, or no longer active): out of what the list shows on the way back. */
export function forgetConversation(id: string): void {
  kept.conversations.delete(id)
  if (kept.list) kept.list.sessions = kept.list.sessions.filter((r) => r.id !== id)
}
