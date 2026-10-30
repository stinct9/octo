import { ref } from 'vue'
import type { Answer, Commitment, CommitmentChecks, CommitmentState, CommitmentVerdict, CoachMark, CoachNote, CoachPart, CoachSummary, Device, FillerCheckResult, FillerCheckStart, FillersSummary, FixResult, FocusAnswer, FocusSummary, ClaudeUsage, Measure, Incoming, ItemFeedback, OpenTask, Person, PersonPage, PipelineImpact, PipelineStep, Progress, SentTask, SessionFocus, SessionDetail, SessionSummary, Speaker, Status, Todo, AsideKind, AsideRow, QuestionChecks, QuestionsSummary, QuestionToCheck, QuestionVerdict, SearchResults, SearchWho, AskEntry, AskSummary, Quiet, WeekCard, AgendaEvent, Intention, Reflection, DrillsSummary, DrillDetail, Rehearsal, RehearsalsSummary, Talk, PrepareState, PreparePreview, MonthView, DayView } from './types'

/** Fired once a request comes back "this browser isn't let in" (the Mac's key, octo/mackey.py); App.vue shows how. */
export const LET_IN = 'octo:let-in'
/** How to let a browser in: the Terminal command, and whether the Octo app is made (octo/launcher.py). */
export interface LetIn { command: string; app: boolean; window?: boolean }

async function failed(res: Response): Promise<never> {
  let detail = res.statusText
  try {
    const body = await res.json()
    detail = body.detail ?? detail
    if (res.status === 401 && body.let_in && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent<LetIn>(LET_IN, { detail: { command: body.command, app: !!body.app, window: !!body.window } }))
  } catch { /* not JSON */ }
  throw new Error(detail)
}

/** The search route (B12), with only the filters that are set. */
export function searchPath(q: string, person = '', who: SearchWho = ''): string {
  const p = new URLSearchParams({ q })
  if (person) p.set('person', person)
  if (who) p.set('who', who)
  return `/api/octo/search?${p}`
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) return failed(res)
  return res.json() as Promise<T>
}

/** How many things the owner did are still being saved or worked out: App.vue shows a bar across the top meanwhile. */
export const working = ref(0)

async function post<T>(path: string, body: unknown): Promise<T> {
  working.value++
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!res.ok) return await failed(res)
    return await (res.json() as Promise<T>)
  } finally {
    working.value--
  }
}

/** Upload one audio file as the raw body (XHR, for progress). Resolves with the new recording's id. */
function upload(file: File, source: string, onProgress: (fraction: number) => void): Promise<{ id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const q = new URLSearchParams({ name: file.name, source, modified: String(file.lastModified) })
    xhr.open('POST', `/api/octo/recordings?${q}`)
    xhr.setRequestHeader('X-Octo-Upload', '1')
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total) }
    xhr.onload = () => {
      let body: { id?: string; detail?: string } = {}
      try { body = JSON.parse(xhr.responseText) } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300 && body.id) resolve({ id: body.id })
      else reject(new Error(body.detail ?? xhr.statusText ?? 'upload failed'))
    }
    xhr.onerror = () => reject(new Error('the server could not be reached'))
    xhr.send(file)
  })
}

export type SpeakerAction = { action: 'confirm' } | { action: 'reject' } | { action: 'name'; name: string; invitee?: string }

/** A change to one item's answers. verdict "" clears "Not right?" (optional since 6 Oct). */
export type FeedbackChange = Partial<Omit<ItemFeedback, 'verdict'>> & { verdict?: ItemFeedback['verdict'] | '' }

export const lab = {
  pipeline: () => get<PipelineStep[]>('/api/octo/pipeline'),
  claudeUsage: () => get<Record<'last_5h' | 'today', ClaudeUsage> & { claude_code?: boolean }>('/api/octo/pipeline/claude'),
  pipelineImpact: (stage: string) => get<PipelineImpact>(`/api/octo/pipeline/${encodeURIComponent(stage)}/impact`),
  choose: (stage: string, choice: string) =>
    post<{ selected: string; impact: PipelineImpact; steps: PipelineStep[] }>(`/api/octo/pipeline/${encodeURIComponent(stage)}`, { choice }),
  sessions: () => get<SessionSummary[]>('/api/octo/sessions'),
  /** The last `days` days, and how many conversations are older. */
  recentSessions: async (days: number) => {
    const r = await fetch(`/api/octo/sessions?days=${days}`)
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
    return { sessions: (await r.json()) as SessionSummary[], older: Number(r.headers.get('X-Older') ?? 0),
      aside: Number(r.headers.get('X-Aside') ?? 0) }
  },
  /** Moves when the list could have: the list polls it while busy and fetches itself only then. */
  sessionsChanged: () => get<{ token: string }>('/api/octo/sessions/changed'),
  session: (id: string) => get<SessionDetail>(`/api/octo/sessions/${encodeURIComponent(id)}`),
  audioUrl: (id: string) => `/api/octo/sessions/${encodeURIComponent(id)}/audio`,
  people: () => get<Person[]>('/api/octo/people'),
  person: (id: string) => get<PersonPage>(`/api/octo/people/${encodeURIComponent(id)}`),
  /** B15: "" clears it. */
  intention: (person: string, text: string) =>
    post<{ intention: Intention | null }>(`/api/octo/people/${encodeURIComponent(person)}/intention`, { text }),
  reflection: (id: string, text: string) =>
    post<{ reflection: Reflection | null }>(`/api/octo/sessions/${encodeURIComponent(id)}/reflection`, { text }),
  speaker: (id: string, letter: string, a: SpeakerAction) =>
    post<{ speakers: Speaker[]; todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/speakers/${letter}`, a),
  feedback: (id: string, run: string, item: string, change: FeedbackChange) =>
    post<{ feedback: Record<string, ItemFeedback>; todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/feedback`,
      { run, item, ...change }),
  /** The one question, "What here was new to you?": the items picked, or nothing new (since 6 Oct). */
  answer: (id: string, run: string, picked: string[], nothingNew: boolean) =>
    post<{ feedback: Record<string, ItemFeedback>; answer: Answer; todo: Todo }>(
      `/api/octo/sessions/${encodeURIComponent(id)}/answer`, { run, new: picked, nothing_new: nothingNew }),
  ticktick: (id: string, run: string, item: string) =>
    post<{ ticktick: Record<string, SentTask> }>(`/api/octo/sessions/${encodeURIComponent(id)}/ticktick`, { run, item }),
  /** Open commitments both ways with a person (B14). */
  commitments: (person: string) =>
    get<{ items: Commitment[]; ticktick_connected: boolean }>(`/api/octo/commitments?person=${encodeURIComponent(person)}`),
  markCommitment: (id: string, state: CommitmentState | null) => post<Commitment>(`/api/octo/commitments/${encodeURIComponent(id)}`, { state }),
  commitmentTicktick: (id: string) => post<Commitment>(`/api/octo/commitments/${encodeURIComponent(id)}/ticktick`, {}),
  /** Every commitment, oldest first, and the owner's check so far (21 Oct). */
  commitmentCheck: () => get<CommitmentChecks>('/api/octo/commitments/check'),
  judgeCommitment: (id: string, verdict: CommitmentVerdict | null) =>
    post<CommitmentChecks>(`/api/octo/commitments/${encodeURIComponent(id)}/check`, { verdict }),
  ticktickDone: (id: string) => get<{ done: Record<string, boolean | null> }>(`/api/octo/sessions/${encodeURIComponent(id)}/ticktick/done`),
  /** Your week (B17): home's card (null when dismissed or nothing yet), or that week's. */
  week: (week?: string) => get<WeekCard | null>(week ? `/api/octo/week?week=${encodeURIComponent(week)}` : '/api/octo/week'),
  /** Home's agenda (B34): today's and tomorrow's calendar events. */
  agenda: () => get<AgendaEvent[]>('/api/octo/agenda'),
  dismissWeek: (week: string) => post<{ ok: boolean }>('/api/octo/week/dismiss', { week }),
  quiet: (person: string) => get<Quiet>(`/api/octo/people/${encodeURIComponent(person)}/quiet`),
  muteQuiet: (person: string, muted: boolean) => post<Quiet>(`/api/octo/people/${encodeURIComponent(person)}/quiet`, { muted }),
  coach: () => get<CoachSummary>('/api/octo/coach'),
  askCoach: () => post<CoachSummary>('/api/octo/coach', {}),
  coachNote: (id: string) => get<CoachNote>(`/api/octo/coach/${encodeURIComponent(id)}`),
  /** verdict / novelty "" clears it. */
  markCoach: (id: string, part: CoachPart, mark: { verdict?: CoachMark['verdict'] | ''; novelty?: CoachMark['novelty'] | '' }) =>
    post<{ feedback: CoachNote['feedback'] }>(`/api/octo/coach/${encodeURIComponent(id)}/feedback`, { part, ...mark }),
  coachFocus: (id: string, text: string, measure: Measure | null) =>
    post<{ focus: FocusSummary; note: CoachNote }>(`/api/octo/coach/${encodeURIComponent(id)}/focus`, { text, measure: measure ?? '' }),
  coachSeen: (id: string) => post<{ ok: boolean }>(`/api/octo/coach/${encodeURIComponent(id)}/seen`, {}),
  focus: () => get<FocusSummary>('/api/octo/focus'),
  startFocus: (text: string, from?: { session_id: string; run: string; item: string }, measure: Measure | null = null) =>
    post<FocusSummary>('/api/octo/focus', { text, ...from, measure }),
  fillers: () => get<FillersSummary>('/api/octo/fillers'),
  fillerWord: (phrase: string, on: boolean, source: 'typed' | 'suggested' = 'typed') =>
    post<FillersSummary>('/api/octo/fillers/words', { phrase, on, source }),
  hedgeWord: (phrase: string, on: boolean) => post<{ phrases: string[] }>('/api/octo/delivery/hedges', { phrase, on }),
  fillerCheck: (sessionId?: string) =>
    get<FillerCheckStart>(`/api/octo/fillers/check${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),
  saveFillerCheck: (sessionId: string, start: number, end: number, byEar: number) =>
    post<FillerCheckResult>('/api/octo/fillers/check', { session_id: sessionId, start_s: start, end_s: end, by_ear: byEar }),
  stopFocus: () => post<FocusSummary>('/api/octo/focus/stop', {}),
  focusCheck: (id: string, focusId: string, answer: FocusAnswer | null) =>
    post<{ focus: SessionFocus | null; feedback: Record<string, ItemFeedback> }>(
      `/api/octo/sessions/${encodeURIComponent(id)}/focus`, { focus_id: focusId, answer }),
  progress: () => get<Progress>('/api/octo/progress'),
  actions: () => get<OpenTask[]>('/api/octo/actions'),
  actionsTicktick: () => get<{ done: Record<string, boolean | null> }>('/api/octo/actions/ticktick'),
  merge: (id: string, letter: string, into: string) =>
    post<FixResult>(`/api/octo/sessions/${encodeURIComponent(id)}/speakers/${letter}/merge`, { into }),
  moveLine: (id: string, line: number, to: string) =>
    post<FixResult>(`/api/octo/sessions/${encodeURIComponent(id)}/lines/${line}`, { to }),
  undoFix: (id: string) => post<FixResult>(`/api/octo/sessions/${encodeURIComponent(id)}/corrections/undo`, {}),
  leaveOut: (id: string, letter: string, leftOut: boolean) =>
    post<FixResult>(`/api/octo/sessions/${encodeURIComponent(id)}/exclude`, { letter, left_out: leftOut }),
  deleteRange: (id: string, start: number, end: number) =>
    post<{ todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/delete-range`, { start, end }),
  deleteSession: (id: string, work = false) =>
    post<{ deleted: string; parts: string[] }>(`/api/octo/sessions/${encodeURIComponent(id)}/delete`, { confirm: 'delete', work }),
  incoming: () => get<Incoming[]>('/api/octo/incoming'),
  status: () => get<Status>('/api/octo/status'),
  device: () => get<Device>('/api/octo/device'),
  split: (id: string, at: number | number[]) =>
    post<{ split_at: number | number[]; new_session: string; new_sessions: string[] }>(`/api/octo/sessions/${id}/split`, { at }),
  joinBack: (id: string) => post<{ session: string }>(`/api/octo/sessions/${id}/join-back`, {}),
  group: (id: string, side: 'before' | 'after') => post<{ session: string }>(`/api/octo/sessions/${id}/group`, { side }),
  ungroup: (id: string, first: string, second: string) =>
    post<{ session: string }>(`/api/octo/sessions/${id}/ungroup`, { first, second }),
  setRecording: (on: boolean) => post<Device>('/api/octo/device/recording', { on }),
  retry: (id: string) => post<{ todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/retry`, {}),
  clearProvisional: (id: string) => post<{ provisional: null }>(`/api/octo/sessions/${encodeURIComponent(id)}/provisional`, { clear: true }),
  redoReview: (id: string) => post<{ todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/redo-review`, {}),
  keep: (id: string) => post<{ restored: string }>(`/api/octo/sessions/${encodeURIComponent(id)}/keep`, {}),
  /** Not a conversation (B5): set aside as `kind`, or null to put it back. */
  aside: (id: string, kind: AsideKind | null) => post<{ aside: AsideKind | null; todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/aside`, { kind }),
  asideList: () => get<AsideRow[]>('/api/octo/aside'),
  search: (q: string, person: string, who: SearchWho) => get<SearchResults>(searchPath(q, person, who)),
  /** Ask your conversations (B24): answers on the page (memory only, for a while) and kept ones. */
  asks: () => get<AskSummary>('/api/octo/ask'),
  ask: (question: string) => post<AskEntry>('/api/octo/ask', { question }),
  keepAsk: (id: string) => post<AskEntry>(`/api/octo/ask/${encodeURIComponent(id)}/keep`, {}),
  deleteAsk: (id: string) => post<{ ok: boolean }>(`/api/octo/ask/${encodeURIComponent(id)}/delete`, {}),
  /** From above (B37): a month, then a day. */
  month: (ym?: string) => get<MonthView>(`/api/octo/calendar/month${ym ? `?month=${ym}` : ''}`),
  day: (iso: string) => get<DayView>(`/api/octo/calendar/day?date=${iso}`),
  /** Prepare for a conversation (B36): the plan for the next one with them, what it would read, make one, delete it. */
  /** Someone not in People yet: added by name (no voiceprint); returns who to prepare for. */
  preparePerson: (name: string, invitee?: string) => post<{ person_id: string }>('/api/octo/people/prepare', { name, invitee }),
  prepare: (person: string) => get<PrepareState>(`/api/octo/people/${encodeURIComponent(person)}/prepare`),
  preparePick: (person: string, names: string[], skip: string[]) =>
    get<PreparePreview>(`/api/octo/people/${encodeURIComponent(person)}/prepare/pick?${new URLSearchParams([
      ...names.map((n): [string, string] => ['names', n]), ...skip.map((s): [string, string] => ['skip', s])])}`),
  /** `mentions`: the mention conversations left ticked; the rest, and any that turned up since, aren't sent. */
  startPrepare: (person: string, body: { about: string; names: string[]; mentions: string[] }) =>
    post<PrepareState>(`/api/octo/people/${encodeURIComponent(person)}/prepare`, body),
  deletePrepare: (person: string) => post<PrepareState>(`/api/octo/people/${encodeURIComponent(person)}/prepare/delete`, {}),
  /** Others' questions and who spoke next, by 1:1 and group (B4). */
  questions: () => get<QuestionsSummary>('/api/octo/questions'),
  questionCheck: () => get<{ items: QuestionToCheck[]; summary: QuestionChecks }>('/api/octo/questions/check'),
  saveQuestionCheck: (sessionId: string, start: number, verdict: QuestionVerdict | null) =>
    post<QuestionChecks>('/api/octo/questions/check', { session_id: sessionId, start, verdict }),
  /** Take back the last answer for it ("Sort them"), leaving none. */
  unanswerAside: (id: string) => post<{ aside: null; todo: Todo }>(`/api/octo/sessions/${encodeURIComponent(id)}/aside`, { kind: null, undo: true }),
  upload,
  drills: () => get<DrillsSummary>('/api/octo/drills'),
  drill: (id: string) => get<DrillDetail>(`/api/octo/drills/${encodeURIComponent(id)}`),
  drillPrompt: (phrase: string, on: boolean) => post<DrillsSummary>('/api/octo/drills/prompts', { phrase, on }),
  deleteDrill: (id: string) => post<{ deleted: string }>(`/api/octo/drills/${encodeURIComponent(id)}/delete`, { confirm: 'delete' }),
  /** A take as the raw body; resolves with its id once the lab has stored it. */
  addDrill: async (take: Blob, name: string, prompt: string) => {
    const res = await fetch(`/api/octo/drills?${new URLSearchParams({ name, prompt })}`, {
      method: 'POST', headers: { 'X-Octo-Upload': '1', 'Content-Type': 'application/octet-stream' }, body: take })
    if (!res.ok) return failed(res)
    return res.json() as Promise<{ id: string }>
  },
  talk: (id: string) => get<Talk>(`/api/octo/sessions/${encodeURIComponent(id)}/talk`),
  sayTalk: (id: string, text: string) => post<Talk>(`/api/octo/sessions/${encodeURIComponent(id)}/talk`, { text }),
  clearTalk: (id: string) => post<Talk>(`/api/octo/sessions/${encodeURIComponent(id)}/talk/clear`, {}),
  rehearsals: () => get<RehearsalsSummary>('/api/octo/rehearsals'),
  rehearsal: (id: string) => get<Rehearsal>(`/api/octo/rehearsals/${encodeURIComponent(id)}`),
  /** B26: someone from People (person_id) or someone described (who). */
  startRehearsal: (body: { person_id?: string; who?: string; hard: string; want: string }) => post<Rehearsal>('/api/octo/rehearsals', body),
  /** The owner's line, then the other person's (one call). "" asks again after a failed reply. */
  sayRehearsal: (id: string, text: string) => post<Rehearsal>(`/api/octo/rehearsals/${encodeURIComponent(id)}/say`, { text }),
  stopRehearsal: (id: string) => post<Rehearsal>(`/api/octo/rehearsals/${encodeURIComponent(id)}/stop`, {}),
  deleteRehearsal: (id: string) => post<{ deleted: string }>(`/api/octo/rehearsals/${encodeURIComponent(id)}/delete`, { confirm: 'delete' }),
  rename: (name: string, to: string, merge = false) => post<{ done: 'renamed' | 'merged'; name: string }>('/api/octo/people/rename', { name, to, merge }),
  forget: (name: string, confirm: string) => post<{ forgotten: string }>('/api/octo/people/forget', { name, confirm }),
  consent: (id: string, how: string, note: string) => post<{ consent: { at: number; how: string; note: string } }>(`/api/octo/people/${encodeURIComponent(id)}/consent`, { how, note }),
  withdrawConsent: (id: string) => post<{ voiceprints_deleted: number }>(`/api/octo/people/${encodeURIComponent(id)}/consent/withdraw`, {}),
}
