// Ask your conversations (B24, octo/ask.py): how the page says what was sent.
import type { AskSent } from './types'

const n = (k: number, one: string) => `${k} ${one}${k === 1 ? '' : 's'}`

/** "Sent 38 lines from 2 conversations (Ravi, this month)", or why nothing was. */
export function sentText(s: AskSent): string {
  const by = [s.people.join(', '), s.dates ?? ''].filter(Boolean).join(', ')
  if (!s.conversations.length) return `Nothing sent: no conversation matched${by ? ` (${by})` : ''}`
  const left = s.left ? `; ${n(s.left, 'more conversation')} matched but didn't fit` : ''
  return `Sent ${n(s.lines, 'line')} from ${n(s.conversations.length, 'conversation')}${by ? ` (${by})` : ''}${left}`
}
