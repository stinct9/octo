// The owner's choices on the pages (folds, the calendar's view), kept in the lab so the Octo app's window, a browser
// and the phone's Lab tab agree (4 Nov). This device's copy only paints the page before the lab answers; the lab's wins.
import { computed, reactive, type WritableComputedRef } from 'vue'

type Value = boolean | string
const LOCAL = 'octo:prefs'
const prefs = reactive<Record<string, Value>>(local())

function local(): Record<string, Value> {
  try {
    const got: Record<string, Value> = JSON.parse(localStorage.getItem(LOCAL) ?? '{}')
    for (let i = 0; i < localStorage.length; i++) { // kept per device before 4 Nov: carried over once
      const k = localStorage.key(i)!, v = localStorage.getItem(k)
      if (k in got) continue
      if (/^octo:(fold:|transcript$|mark-fillers$|questions$|review-open$)/.test(k)) got[k] = v === '1'
      if (k === 'octo.calendar.show' && v) got[k] = v
    }
    return got
  } catch { return {} }
}

function keep() { try { localStorage.setItem(LOCAL, JSON.stringify(prefs)) } catch { /* private window */ } }

function save(key: string, value: Value) {
  fetch(`/api/octo/prefs/${encodeURIComponent(key)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }) }).catch(() => { /* offline: this device keeps it until the next change */ })
}

/** The lab's copy, on the page's first load and whenever it comes back into view. A choice only this device has (made
 * before 4 Nov, or while the lab couldn't be reached) goes to the lab. */
export async function loadPrefs() {
  try {
    const res = await fetch('/api/octo/prefs')
    if (!res.ok) return
    const there: Record<string, Value> = await res.json()
    for (const k of Object.keys(prefs)) if (!(k in there)) save(k, prefs[k])
    Object.assign(prefs, there)
    keep()
  } catch { /* the lab isn't answering: this device's copy stands */ }
}

/** One choice, as a ref: reading gives the owner's (or `fallback`), setting saves it here and in the lab. */
export function pref(key: string, fallback: boolean): WritableComputedRef<boolean>
export function pref<T extends string>(key: string, fallback: T): WritableComputedRef<T>
export function pref<T extends Value>(key: string, fallback: T): WritableComputedRef<T> {
  return computed({
    get: () => (typeof prefs[key] === typeof fallback ? prefs[key] : fallback) as T,
    set: (v: T) => { prefs[key] = v; keep(); save(key, v) },
  })
}
