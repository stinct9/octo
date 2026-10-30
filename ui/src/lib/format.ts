import type { Metric } from '../types'

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'number') {
    // Typographic minus for negatives: dBm and dBFS read better with it.
    // One decimal normally; two for small fractions like log-probabilities (−0.26, not −0.3).
    const s = Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 2 : 1)
    return s.replace('-', '−')
  }
  return String(v).replace(/^-(?=\d)/, '−')
}

/** "last 430", or "" when unchanged / unknown. */
export function previousNote(m: Metric): string {
  if (m.previous === null || m.previous === undefined || m.previous === m.value) return ''
  return `last ${formatValue(m.previous)}`
}

/** Signed change vs last time for numeric metrics, e.g. "▲ 6.7". */
export function delta(m: Metric): string {
  if (typeof m.value !== 'number' || typeof m.previous !== 'number') return ''
  const d = m.value - m.previous
  if (Math.abs(d) < 0.05) return '= last'
  return `${d > 0 ? '▲' : '▼'} ${formatValue(Math.abs(Math.round(d * 10) / 10))} vs last`
}

export function duration(s: number | undefined): string {
  if (s === undefined) return ''
  return s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
}

export function clock(unixSeconds: number | null | undefined): string {
  if (!unixSeconds) return ''
  return new Date(unixSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function words(s: string): string[] {
  return s.toLowerCase().match(/[\p{L}\p{N}']+/gu) ?? []
}

/** Word error rate: word-level edit distance / reference length. */
export function wer(reference: string, hypothesis: string): { errors: number; words: number; rate: number } | null {
  const ref = words(reference)
  const hyp = words(hypothesis)
  if (ref.length === 0) return null
  let prev = Array.from({ length: hyp.length + 1 }, (_, j) => j)
  for (let i = 1; i <= ref.length; i++) {
    const cur = [i]
    for (let j = 1; j <= hyp.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ref[i - 1] === hyp[j - 1] ? 0 : 1))
    }
    prev = cur
  }
  const errors = prev[hyp.length]
  return { errors, words: ref.length, rate: errors / ref.length }
}
