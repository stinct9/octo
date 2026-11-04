// Geometry for the fillers trend (Progress): scales, clean ticks, the nearest point. Pure, so it's
// tested without a page. Marks follow the dataviz rules: one accent line, a 10% band, grey dots.

/** A clean top for the y axis: the next multiple of a 1-2-5 step at or above v, never below 2. */
export function niceMax(v: number): number {
  const top = Math.max(2, v)
  const step = niceStep(top / 4)
  return Math.ceil(top / step) * step
}

export function niceStep(raw: number): number {
  const p = 10 ** Math.floor(Math.log10(raw))
  const f = raw / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p
}

/** Ticks from 0 to max (max being a niceMax), about four of them. */
export function yTicks(max: number): number[] {
  const step = niceStep(max / 4)
  const out = []
  for (let v = 0; v <= max + 1e-9; v += step) out.push(Math.round(v * 100) / 100)
  return out
}

export function linear(d0: number, d1: number, r0: number, r1: number): (v: number) => number {
  const span = d1 - d0 || 1
  return (v) => r0 + ((v - d0) / span) * (r1 - r0)
}

/** Local midnights from the first day to the last, thinned to at most `max` labels. */
export function dayTicks(from: number, to: number, max: number): number[] {
  const start = new Date(from * 1000)
  start.setHours(0, 0, 0, 0)
  const days: number[] = []
  for (const d = new Date(start); d.getTime() / 1000 <= to; d.setDate(d.getDate() + 1)) days.push(d.getTime() / 1000)
  if (days.length <= max) return days
  const every = Math.ceil(days.length / max)
  return days.filter((_, i) => i % every === 0)
}

/** Index of the x nearest to x (xs ascending), or -1 when empty. */
export function nearest(xs: number[], x: number): number {
  let best = -1
  let gap = Infinity
  xs.forEach((v, i) => { const g = Math.abs(v - x); if (g < gap) { gap = g; best = i } })
  return best
}

/** Local Mondays from the first day to the last, every 1, 2, 4… weeks to fit `max` labels; days under two weeks. */
export function weekTicks(from: number, to: number, max: number): number[] {
  const d = new Date(from * 1000)
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7))  // the first Monday on or after the start
  const mondays: number[] = []
  for (; d.getTime() / 1000 <= to; d.setDate(d.getDate() + 7)) mondays.push(d.getTime() / 1000)
  if (mondays.length < 2) return dayTicks(from, to, max)
  let every = 1
  while (Math.ceil(mondays.length / every) > Math.max(1, max)) every *= 2
  return mondays.filter((_, i) => i % every === 0)
}

/** The band as one closed path: along the top, back along the bottom. */
export function bandPath(xs: number[], lo: number[], hi: number[]): string {
  if (!xs.length) return ''
  const top = xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${hi[i].toFixed(1)}`)
  const bottom = xs.map((x, i) => `L${x.toFixed(1)},${lo[i].toFixed(1)}`).reverse()
  return `${top.join('')}${bottom.join('')}Z`
}

export function linePath(xs: number[], ys: number[]): string {
  return xs.map((x, i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join('')
}
