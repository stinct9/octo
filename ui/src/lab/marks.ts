// B16: the moments the owner marked as they happened (octo/marks.py), kept pure so it's tested.
import type { Mark } from './types'

/** "You marked this moment" / "You marked 3 moments". */
export const markedText = (n: number): string => (n === 1 ? 'You marked this moment' : `You marked ${n} moments`)

/** Which transcript lines carry a mark. */
export const markedLines = (marks: Mark[]): Set<number> => new Set(marks.flatMap((m) => (m.line == null ? [] : [m.line])))
