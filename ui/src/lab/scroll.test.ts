import { describe, expect, it } from 'vitest'
import { scrollPlaces } from './scroll'

// A history: link() is a new entry (no state), back()/forward() move between entries with their state.
function fakeHistory() {
  const entries: unknown[] = [null]
  let at = 0
  return {
    get state() { return entries[at] },
    replaceState(s: unknown) { entries[at] = s },
    link() { entries.splice(at + 1, entries.length, null); at++ },
    back() { at-- },
    forward() { at++ },
  }
}
function fakeStore() { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } }

describe('where each history entry was scrolled to', () => {
  it('a link opens at the top; Back and Forward return to where you were', () => {
    const h = fakeHistory()
    const p = scrollPlaces(h)
    h.link(); expect(p.moved(1200)).toBe(null)   // list at 1200 → a conversation
    h.back(); expect(p.moved(300)).toBe(1200)    // conversation at 300 → back to the list
    h.forward(); expect(p.moved(1250)).toBe(300) // forward to the conversation
    h.back(); expect(p.moved(0)).toBe(1250)
    h.link(); expect(p.moved(40)).toBe(null)     // the same list → a fresh link: top
  })
  it('a reload keeps its place, from the tab\'s session store', () => {
    const h = fakeHistory(); const store = fakeStore()
    const p = scrollPlaces(h, store)
    expect(p.start()).toBe(null)
    p.keep(800)
    expect(scrollPlaces(h, store).start()).toBe(800)
  })
  it('keeps the rest of the entry\'s state', () => {
    const h = fakeHistory(); h.replaceState({ other: 1 })
    scrollPlaces(h)
    expect(h.state).toMatchObject({ other: 1, octoScroll: expect.any(String) })
  })
})
