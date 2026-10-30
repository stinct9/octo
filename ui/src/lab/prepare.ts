// Prepare for a conversation (B36, octo/prepare.py): how the page says what a plan reads, and finds the name in a line.
import type { PreparePreview } from './types'

const n = (k: number, one: string) => `${k} ${one}${k === 1 ? '' : 's'}`

/** "Sends 212 lines: 2 conversations with Leela, and 6 naming them" (past: "Read …"), or why nothing would go. */
export function sendsText(p: PreparePreview, past = false): string {
  const withThem = p.with.filter((c) => c.lines).length
  const naming = p.mentions.filter((c) => c.ticked && c.lines).length
  if (!withThem && !naming) return `Nothing to send: no conversation with ${p.person.name} or naming them${p.mentions.length ? ' is ticked' : ' yet'}`
  const parts = [withThem ? `${n(withThem, 'conversation')} with ${p.person.name}` : '', naming ? `${n(naming, 'conversation')} naming them` : '']
  const left = p.left ? `; ${n(p.left, 'more')} didn't fit` : ''
  return `${past ? 'Read' : 'Sends'} ${n(p.lines, 'line')}: ${parts.filter(Boolean).join(', and ')}${left}`
}

/** The owner's other spellings, typed "Tam, Tami": trimmed, no empties, no repeats. */
export function spellings(text: string): string[] {
  return [...new Set(text.split(',').map((s) => s.trim()).filter(Boolean))]
}

/** A line split around the names it was found by, for <mark>: [{text, hit}]. */
export function marked(text: string, names: string[]): { text: string; hit: boolean }[] {
  const alts = names.filter((x) => x.length > 2).map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (!alts.length) return [{ text, hit: false }]
  const whole = new RegExp(`^(${alts.join('|')})$`, 'i')
  const parts = text.split(new RegExp(`\\b(${alts.join('|')})\\b`, 'i')).filter(Boolean)
  return parts.map((part) => ({ text: part, hit: parts.length > 1 && whole.test(part) }))
}
