// Back and Forward return to where you were (the owner, 4 Nov). A hash link makes a new history entry with no state;
// the first time the app sees one it stamps a key on it, and the scroll position is kept under that key when you
// leave. Arriving on a stamped entry is Back or Forward: its position comes back. Kept for the tab's session, so
// a reload keeps its place too.
type Hist = Pick<History, 'state' | 'replaceState'>
interface Store { getItem(k: string): string | null; setItem(k: string, v: string): void }
const KEY = 'octo:scroll'

export function scrollPlaces(h: Hist, store?: Store) {
  let ys: Record<string, number> = {}
  try { ys = JSON.parse(store?.getItem(KEY) ?? '{}') } catch { /* none kept */ }
  let n = 0
  const stamp = () => {
    const k = `${Date.now().toString(36)}.${n++}`
    h.replaceState({ ...(h.state ?? {}), octoScroll: k }, '')
    return k
  }
  let here: string = h.state?.octoScroll ?? stamp()
  const keep = (y: number) => {
    ys[here] = y
    try { store?.setItem(KEY, JSON.stringify(ys)) } catch { /* private window */ }
  }
  return {
    /** Where this entry was left last (on a reload), or null for the top. */
    start: (): number | null => ys[here] ?? null,
    keep,
    /** On a route change, before the new page draws: keeps the old entry's `y`; returns the new one's, or null for the top. */
    moved(y: number): number | null {
      keep(y)
      const k: string | undefined = h.state?.octoScroll
      here = k ?? stamp()
      return k ? ys[k] ?? null : null
    },
  }
}
