import type { Incoming, SessionDetail, SessionSummary } from './types'

/** What the last visit to a page showed, kept while the app is open: going back shows it at once, and the page then
 *  asks again (Jay, 28 Oct: going back and forth between conversations took a long time, with "Loading…" every time). */
export const kept = {
  list: null as { sessions: SessionSummary[]; older: number; aside: number; incoming: Incoming[] } | null,
  conversations: new Map<string, SessionDetail>(),
}

const CONVERSATIONS = 12

/** Keeps a conversation as last seen, the most recent CONVERSATIONS of them. */
export function keepConversation(c: SessionDetail): void {
  kept.conversations.delete(c.id)
  kept.conversations.set(c.id, c)
  for (const id of kept.conversations.keys()) {
    if (kept.conversations.size <= CONVERSATIONS) break
    kept.conversations.delete(id)
  }
}
