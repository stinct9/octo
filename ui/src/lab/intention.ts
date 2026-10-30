// B15: what the review said of an intention, in words and a tone, kept pure so it's tested.
import type { Happened } from './types'

export const HAPPENED: Record<Happened, { text: string; tone: 'done' | 'warn' | 'muted' }> = {
  yes: { text: 'It happened', tone: 'done' },
  partly: { text: 'Partly', tone: 'warn' },
  no: { text: "It didn't happen", tone: 'warn' },
  unclear: { text: "Can't tell from the transcript", tone: 'muted' },
}

/** The verdict's words; without one, why there's none yet. */
export function happenedText(h: Happened | null | undefined, reviewed: boolean): string {
  return h ? HAPPENED[h].text : reviewed ? "The review couldn't say" : 'The review will say whether it happened'
}
