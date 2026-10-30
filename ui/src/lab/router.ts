import { reactive } from 'vue'

// Hash routes, so `bench serve`'s static hosting needs no server-side fallback:
//   #/            conversations
//   #/c/<id>      one conversation; #/c/<id>/<line> opens it at that line and plays it (search, B12)
//   #/people/<id> one person, before you talk; #/people/<id>/prepare  a plan for the next conversation (B36)
//   #/coach/<id>  an earlier coach note
//   #/practice  #/practice/<id>   practice drills (B18), and one take
//   #/ask         ask your conversations (B24)
//   #/calendar  #/calendar/2026-10  #/calendar/2026-10-28   from above (B37): this month, a month, a day
//   #/rehearse  #/rehearse/<id>  #/rehearse/with/<person>   rehearsals (B26), one, and a new one with someone
//   #/people  #/progress  #/pipeline  #/bench  #/phone  #/settings  #/setup  #/mac  #/fillers  #/fillers/check  #/coach  #/sort
export type Route =
  | { name: 'conversations' }
  | { name: 'conversation'; id: string; line?: number }
  | { name: 'people' }
  | { name: 'person'; id: string }
  | { name: 'prepare'; id: string }
  | { name: 'progress' }
  | { name: 'pipeline' }
  | { name: 'bench' }
  | { name: 'phone' }
  | { name: 'settings' }
  | { name: 'setup' }
  | { name: 'mac' }
  | { name: 'fillers' }
  | { name: 'fillercheck' }
  | { name: 'coach'; note?: string }
  | { name: 'sort' }
  | { name: 'questioncheck' }
  | { name: 'commitmentcheck' }
  | { name: 'practice'; take?: string }
  | { name: 'ask' }
  | { name: 'calendar'; month?: string; day?: string }
  | { name: 'rehearse'; id?: string; person?: string }

export function parse(hash: string): Route {
  const path = hash.replace(/^#/, '') || '/'
  const c = path.match(/^\/c\/([A-Za-z0-9_]+)(?:\/(\d+))?$/)
  if (c) return c[2] ? { name: 'conversation', id: c[1], line: Number(c[2]) } : { name: 'conversation', id: c[1] }
  const who = path.match(/^\/people\/([A-Za-z0-9_]+)$/)
  if (who) return { name: 'person', id: who[1] }
  const prep = path.match(/^\/people\/([A-Za-z0-9_]+)\/prepare$/)
  if (prep) return { name: 'prepare', id: prep[1] }
  const note = path.match(/^\/coach\/([A-Za-z0-9_]+)$/)
  if (note) return { name: 'coach', note: note[1] }
  if (path === '/people') return { name: 'people' }
  if (path === '/progress') return { name: 'progress' }
  if (path === '/pipeline') return { name: 'pipeline' }
  if (path === '/bench') return { name: 'bench' }
  if (path === '/phone') return { name: 'phone' }
  if (path === '/settings') return { name: 'settings' }
  if (path === '/setup') return { name: 'setup' }
  if (path === '/mac') return { name: 'mac' }
  if (path === '/fillers') return { name: 'fillers' }
  if (path === '/fillers/check') return { name: 'fillercheck' }
  if (path === '/coach') return { name: 'coach' }
  if (path === '/sort') return { name: 'sort' }
  if (path === '/questions/check') return { name: 'questioncheck' }
  if (path === '/commitments/check') return { name: 'commitmentcheck' }
  if (path === '/ask') return { name: 'ask' }
  const cal = path.match(/^\/calendar(?:\/(\d{4}-\d{2})(-\d{2})?)?$/)
  if (cal) return cal[2] ? { name: 'calendar', day: cal[1] + cal[2] } : cal[1] ? { name: 'calendar', month: cal[1] } : { name: 'calendar' }
  const pr = path.match(/^\/practice(?:\/([A-Za-z0-9_]+))?$/)
  if (pr) return pr[1] ? { name: 'practice', take: pr[1] } : { name: 'practice' }
  const rh = path.match(/^\/rehearse(?:\/(with\/)?([A-Za-z0-9_]+))?$/)
  if (rh) return rh[2] ? (rh[1] ? { name: 'rehearse', person: rh[2] } : { name: 'rehearse', id: rh[2] }) : { name: 'rehearse' }
  return { name: 'conversations' }
}

const inBrowser = typeof window !== 'undefined'
export const route = reactive<{ current: Route }>({ current: parse(inBrowser ? location.hash : '') })
if (inBrowser) window.addEventListener('hashchange', () => { route.current = parse(location.hash) })

export const href = {
  conversations: '#/',
  conversation: (id: string) => `#/c/${id}`,
  line: (id: string, line: number | null) => (line == null ? `#/c/${id}` : `#/c/${id}/${line}`),
  people: '#/people',
  person: (id: string) => `#/people/${id}`,
  prepare: (id: string) => `#/people/${id}/prepare`,
  progress: '#/progress',
  pipeline: '#/pipeline',
  bench: '#/bench',
  phone: '#/phone',
  settings: '#/settings',
  setup: '#/setup',
  mac: '#/mac',
  fillers: '#/fillers',
  fillerCheck: '#/fillers/check',
  coach: '#/coach',
  coachNote: (id: string) => `#/coach/${id}`,
  sort: '#/sort',
  questionCheck: '#/questions/check',
  commitmentCheck: '#/commitments/check',
  practice: '#/practice',
  take: (id: string) => `#/practice/${id}`,
  ask: '#/ask',
  calendar: '#/calendar',
  month: (ym: string) => `#/calendar/${ym}`,
  day: (iso: string) => `#/calendar/${iso}`,
  rehearse: '#/rehearse',
  rehearsal: (id: string) => `#/rehearse/${id}`,
  rehearseWith: (person: string) => `#/rehearse/with/${person}`,
}
