// Splitting one conversation at several points (octo/split.py `split_at`), kept pure so it's tested.
import type { Line } from './types'

export const MIN_GAP_S = 30 // the route's rule: each cut at least 30 s from either end and from each other

export interface Part { start: number; end: number; speakers: string[] }

/** Whether a cut can go at `at`, given the conversation's length and the cuts already chosen. */
export const canCut = (at: number, duration: number, cuts: number[]): boolean =>
  at >= MIN_GAP_S && at <= duration - MIN_GAP_S && cuts.every((c) => Math.abs(c - at) >= MIN_GAP_S)

/** The parts the cuts make, each with its speakers' letters, most talk first (a line counts where it starts). */
export function parts(cuts: number[], duration: number, lines: Pick<Line, 'speaker' | 'start' | 'end'>[]): Part[] {
  const edges = [0, ...[...cuts].sort((a, b) => a - b), duration]
  return edges.slice(1).map((end, i) => {
    const start = edges[i]
    const talk = new Map<string, number>()
    for (const ln of lines) {
      if (ln.speaker && ln.start >= start && ln.start < end) talk.set(ln.speaker, (talk.get(ln.speaker) ?? 0) + ln.end - ln.start)
    }
    return { start, end, speakers: [...talk].sort((a, b) => b[1] - a[1]).map(([s]) => s) }
  })
}
