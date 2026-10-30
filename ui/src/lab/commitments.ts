// Commitments both ways (B14): the owner's marks and "Copy what we agreed", kept pure so they're tested.
import { day, dueDay } from './format'
import type { Commitment, CommitmentState } from './types'

/** The marks a commitment can take: Done / Dropped on the owner's own, They did / They didn't on others'. */
export function marks(mine: boolean): { state: CommitmentState; label: string }[] {
  return mine ? [{ state: 'done', label: 'Done' }, { state: 'dropped', label: 'Dropped' }]
    : [{ state: 'did', label: 'They did' }, { state: 'didnt', label: "They didn't" }]
}

export const MARKED: Record<CommitmentState, string> = { done: '✓ Done', dropped: 'Dropped', did: '✓ They did', didnt: "They didn't" }

/** "Copy what we agreed": both sides' commitments with their dates, as plain text for the clipboard (Octo sends
 *  nothing). The day each was said is added when they come from more than one conversation. */
export function agreedText(items: Commitment[], heading = 'What we agreed'): string {
  const days = new Set(items.map((c) => (c.at ? day(c.at) : '')))
  const said = (c: Commitment) => (days.size > 1 && c.at ? ` (said ${day(c.at)})` : '')
  return [heading, ...items.map((c) => `- ${c.by}: ${c.what}${c.due ? `, by ${dueDay(c.due)}` : ''}${said(c)}`)].join('\n')
}
