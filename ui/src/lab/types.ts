// Shapes returned by /api/octo (octo/api.py).

export type Todo = (
  | { kind: 'processing'; stage: string; step: number; of: number; detail?: string | null; queued?: boolean
      /** When it'll be ready for the owner (B3): its review ("review"), or the point it needs them ("you"). */
      eta?: number | null; late?: boolean; then?: 'review' | 'you' | null }
  | { kind: 'failed'; stage: string; error: string | null; hint?: string | null; auto_retry?: boolean }
  | { kind: 'confirm'; hindi?: boolean } // hindi: the draft is merged by Claude once you're confirmed in it
  | { kind: 'not_you' } // nothing left to answer and the owner isn't in it: numbers only, no review
  | { kind: 'short'; reason: string }
  | { kind: 'paused'; reason: string }
  | { kind: 'review' } // the review is in: one question waits, "What here was new to you?" (since 6 Oct)
  | { kind: 'reviewed' }
  | { kind: 'aside'; what: AsideKind } // The owner set it aside: not a conversation (B5, 13 Oct)
  | { kind: 'part'; head: string } // B35: a later part of one discussion; its review is the whole's, on the first part
) & {
  /** B35: on one discussion's first part, the later part (its number and id) that the whole waits on. */
  part?: number; part_id?: string
}

/** What a recording that isn't a conversation was (B5): the TV, music or the owner singing, something else. */
export type AsideKind = 'tv' | 'music' | 'other'
export interface AsideRow { id: string; aside: AsideKind; duration_s: number; source: string; started_at: number | null }

/** One search result (B12, octo/search.py): a line, or review text opening at its quote's line (null: none). */
export interface SearchHit {
  kind: 'line' | 'review'
  session_id: string
  at: number | null
  with: string[]
  line: number | null
  start?: number
  /** Who said it, or the review's part: "The one thing", "Insight", "Step". */
  who: string
  self: boolean
  before: string
  match: string
  after: string
}
export interface SearchResults { results: SearchHit[]; total: number; scanned: number; ms: number }
export type SearchWho = 'you' | 'them' | ''

/** Ask your conversations (B24, octo/ask.py): a moment a claim rests on, checked against the lines that were sent. */
export interface AskQuote { session_id: string; line: number; start: number; text: string; at: number | null; who: string; self: boolean; uncertain: boolean }
export interface AskAnswer { claims: { text: string; quotes: AskQuote[] }[]; dropped: number; not_found: string; limits: string }
/** What went to the model: picked on the Mac by the people, the dates and the words in the question. */
export interface AskSent {
  lines: number; people: string[]; dates: string | null; left: number
  conversations: { session_id: string; at: number | null; with: string[]; lines: number; whole: boolean }[]
}
export interface AskEntry {
  id: string; question: string; at: number; status: 'running' | 'done' | 'failed'; kept_at?: number | null
  answer: AskAnswer | null; sent: AskSent | null; error: string | null
}
/** Prepare for a conversation (B36, octo/prepare.py): what a plan reads, picked on the Mac before anything is sent. */
export interface PrepareConv {
  session_id: string; at: number | null; with: string[]; lines: number; whole: boolean
  /** Every line that would go (the page's fold; never stored). */
  going?: { line: number; who: string; text: string }[]
}
export interface PrepareMention extends PrepareConv {
  /** Unticked: not sent (another person with the same name, say). */
  ticked: boolean
  hits: number
  /** The first few lines naming them, as the page shows them before Prepare. */
  shown: { line: number; text: string; who: string }[]
}
export interface PreparePreview {
  person: { id: string; name: string }
  names: string[]
  /** Others in People with the same first name. */
  shared_first: string[]
  /** A first name too short to search for on its own (the whole name still is). */
  short_first: string | null
  lines: number
  left: number
  max_lines: number
  with: PrepareConv[]
  mentions: PrepareMention[]
}
export interface PrepareItem { headline: string; observation: string; quotes: AskQuote[]; second_hand: boolean }
export interface PreparePlanBody {
  aim: string
  why: string
  thinking: PrepareItem[]
  open: PrepareItem[]
  structure: { minutes: number; part: string; detail: string }[]
  questions: { purpose: string; items: string[] }[]
  careful: { text: string; quotes: AskQuote[] }[]
  limits: string
  dropped: number
  /** Readings with no moment the lab could check, left out. */
  unbacked: number
  /** Readings and questions resting on something said in confidence, left out. */
  withheld: number
}
export interface PreparePlan {
  id: string; status: 'running' | 'done' | 'failed'; at: number; finished_at: number | null
  about: string | null; names: string[]; sent: PreparePreview | null; plan: PreparePlanBody | null
  /** The calendar event it was made for. */
  event: string | null
  /** Its conversation has begun (the one recorded, or the meeting): it shows beside that conversation's review. */
  used: { at: number; session_id: string | null } | null
  error: string | null; waiting_until: number | null
}
export interface PrepareClose { kind: 'step' | 'commitment'; text: string; due: string | null; session_id: string; mine?: boolean }
export interface PrepareState {
  person: { id: string; name: string }
  plan: PreparePlan | null
  /** The last plan made, while a new one runs or after it failed. */
  previous: PreparePlan | null
  close_first: PrepareClose[]
  event: { title: string; start: number; end: number } | null
  /** Their open intention (B15): Save as my intention replaces it. */
  intention: string | null
  why_not: string | null
  max_about: number
  max_names: number
}
/** From above (B37, octo/month.py): a month of conversations per local day, then one day. */
/** The owner's fillers pooled over conversations on the fillers' line: per 100 of their words. */
export interface FillerRate { words: number; fillers: number; rate: number }
export interface MonthDay { date: string; count: number; minutes: number; reviews: number; to_answer: number; people: string[]; fillers: FillerRate | null }
export interface MonthView {
  month: string; today: string; first: string | null; prev: string; next: string
  days: MonthDay[]
  totals: { conversations: number; minutes: number; reviews: number; answered: number; people: number; fillers: FillerRate | null }
}
export type DayItem =
  | (SessionSummary & { kind: 'conversation'; at: number; one_thing: string | null; fillers: FillerRate | null })
  | { kind: 'event'; at: number; id: string; title: string; start: number; end: number;
      people: { name: string; person_id: string | null }[]; conversation: string | null }
export interface DayView {
  date: string; today: string; items: DayItem[]; totals: { conversations: number; minutes: number; fillers: FillerRate | null }
  prev: string | null; next: string | null
  /** The calendar keeps two days either side: older days show conversations only. */
  events_kept: boolean
}
export interface AskSummary { live: AskEntry[]; kept: AskEntry[]; why_not: string | null; max_lines: number; live_minutes: number }

/** auto: the owner's own voice, matched surely enough to settle without asking (C4); no voiceprint until confirmed. */
export type SpeakerState = 'confirmed' | 'auto' | 'suggested' | 'ambiguous' | 'unknown'

export interface SpeakerChip {
  letter: string
  name: string // display name: "Nisha", "Nisha?", "Rishi or Anil?", "Speaker C"
  state: SpeakerState
  self: boolean
}

export interface SessionSummary {
  id: string
  source: string
  started_at: number | null
  duration_s: number
  speech_s: number
  speakers: SpeakerChip[]
  preview: string | null
  todo: Todo
  /** A guess that it isn't a conversation (B5): offered in "Sort them", never acted on by itself. */
  maybe_aside?: boolean
  /** B35: one discussion in this many parts, shown once (its length theirs together). */
  part_count?: number
}

export interface Speaker {
  letter: string
  name: string | null
  /** Their person page (octo/person.py), once named. */
  person_id?: string | null
  state: SpeakerState
  self: boolean
  alternatives: string[]
  talk_s: number
  share: number
}

export interface Line {
  id: number
  speaker: string | null
  start: number
  end: number
  text: string
  uncertain: boolean
}

export interface Turn { speaker: string; start: number; end: number }

export interface SpeakerNumbers {
  talk_s: number
  share: number
  turns: number
  longest_turn_s: number
  words: number
  questions: number
  interrupted_others: number
  was_interrupted: number
  fillers?: number
  fillers_per_100_words?: number
}

export interface Numbers { speakers: Record<string, SpeakerNumbers>; speaker_changes: number }

export interface Quote { line: number; start: number; speaker: string | null; text: string; uncertain: boolean }

/** How he did against the principle (reviews since 6 Oct); depends: it turns on what the transcript can't show. */
export type Held = 'held' | 'partly' | 'missed' | 'depends'

export interface Finding {
  principle: number
  title: string
  observation: string
  quotes: Quote[]
  unverified_quotes: number
  flags: string[]
  try_next_time: string | null
  held?: Held | null
}

/** A "What to do" task. Older reviews (before 2 Oct evening) have `action` instead of title/detail/due. */
export interface Task { title?: string; detail?: string; why: string; due?: string | null; action?: string }

export interface SentTask { task_id: string; project_id?: string | null; url: string; added_at: number }

/** The owner's mark on a commitment (B14): Done / Dropped on their own, They did / They didn't on others'; null is open. */
export type CommitmentState = 'done' | 'dropped' | 'did' | 'didnt'
/** The owner's check of the step's work on one (21 Oct): did it get this commitment right? */
export type CommitmentVerdict = 'right' | 'partly' | 'wrong'
export interface CommitmentChecks { items: Commitment[]; counts: Record<CommitmentVerdict, number>; checked: number; first: number }
/** One promise, both ways (octo/commitments.py `view`). `mine`: the owner promised it. */
export interface Commitment {
  id: string
  session_id: string
  at: number | null
  mine: boolean
  by: string
  to: string
  by_person: string | null
  to_person: string | null
  what: string
  quote: string
  line: number
  start: number
  end: number
  due: string | null
  state: CommitmentState | null
  checked?: CommitmentVerdict | null
  ticktick: { task_id: string; project_id?: string; url: string } | null
}
/** "Octo hasn't heard from…" (B17, octo/week.py `heard`): `text` once it's been twice their usual gap. */
export interface Quiet { person_id: string; name: string; usual_days: number | null; since_days: number | null; muted: boolean; text: string | null }
/** Your week (B17, octo/week.py `card`): the seven days from `week`, a Monday. The rest only on home's card (`current`). */
/** Home's agenda (B34): a calendar event today or tomorrow, who's in it, what was recorded during it. */
export interface AgendaEvent {
  id: string
  title: string
  start: number
  end: number
  /** key: the invitee's (to prepare for someone Octo doesn't know, B36); prepared: a plan for them is ready. */
  people: { name: string; person_id: string | null; key?: string; prepared?: boolean }[]
  conversation: string | null
}

export interface WeekCard {
  week: string
  start: number
  end: number
  current: boolean
  counts: { heard: number; reviewed: number; acted: number }
  focus: { id: string; text: string; measure: Measure | null; week: Measured | null; usual: Measured | null } | null
  commitments?: { mine: number; theirs: number; due: Commitment[] }
  quiet?: Quiet[]
  coach?: { note_id: string; at: number; theme: string | null } | null
}
/** The conversation page's block: its commitments, earlier open ones that came up here, how the step stands. */
export interface SessionCommitments {
  items: Commitment[]
  came_up: (Commitment & { note: string; here: { line: number; quote: string; start: number; end: number } })[]
  status: 'waiting' | 'queued' | 'running' | 'failed' | 'done' | 'old'
  error: string | null
}

export type FocusAnswer = 'did' | 'missed' | 'na'
/** A number a focus or a coach note is tracked with (octo/measures.py, 6 Oct; the delivery ones since 15 Oct, B10). */
export type Measure = 'fillers' | 'questions' | 'talk_share' | 'talked_over' | Exclude<DeliveryMeasure, 'fillers'>
/** How the owner showed up (octo/delivery.py, B10): the six delivery measures, listening (B13) and voice (B27). */
export type DeliveryMeasure = 'fillers' | 'pauses' | 'restarts' | 'hedges' | 'pace' | 'longest' | 'patience' | 'turns' | 'their_longest'
  | 'pitch_range' | 'loudness' | 'pushed_pitch' | 'pushed_loudness'
/** Where this conversation sits against his own spread: low / about / high, or why there's no comparison. */
export type DeliveryState = 'low' | 'about' | 'high' | 'short' | 'no_usual' | 'off' | 'unmeasured' | 'no_audio'
export interface DeliveryRow {
  measure: DeliveryMeasure
  value: number | null
  /** Pooled over every conversation on the line before this one. */
  usual: number | null
  /** The middle half of those conversations' own values: "about usual". */
  lo: number | null
  hi: number | null
  state: DeliveryState
  conversations: number
  /** A group, where his groups run higher than his 1:1s on this measure. */
  groups_higher: boolean
}
export interface Span { start: number; end: number }
export interface SessionDelivery {
  rows: DeliveryRow[]
  /** Listening (B13): patience, turns per minute, their longest stretch; empty when nobody else spoke. */
  listening: DeliveryRow[]
  /** Voice (B27, octo/tone.py): pitch range, loudness, and both when pushed back, from the stored audio. */
  voice: DeliveryRow[]
  /** Seconds of his voice measured (null: not measured yet, or no audio), and of it in pushed-back turns. */
  voice_s: number | null
  pushed_s: number | null
  /** His turns right after someone else's question or disagreement. */
  pushed_turns: number
  /** Times he took the turn after someone finished, and started ≥ 0.2 s before they did (talking over). */
  taken: number
  over: number | null
  changes: number | null
  span_s: number | null
  words: number
  on_line: boolean
  reason: string | null
  size: 'solo' | 'one' | 'group'
  min_words: number
  fillers: SessionFillers
  hedges: { phrases: string[]; by_phrase: Record<string, number>; count: number }
  speak_s: number | null
  timed_words: number | null
  moments: { pauses: Span[]; restarts: Span[]; longest: (Span & { words: number }) | null
    /** His longest waits before taking the turn; each other person's longest stretch, longest first. */
    waits: (Span & { gap: number; spk: string })[]
    their_longest: (Span & { s: number; words: number; letter: string; name: string | null })[] }
}
/** One measure pooled over conversations; lo / hi: a rough band for ordinary variation, where it's a count. */
export interface Measured { value: number; lo: number | null; hi: number | null; conversations: number; words?: number; minutes?: number; count?: number }
/** A measure since a moment (a focus started, a coach note), against about an hour of talk just before. */
export interface SinceBefore { measure: Measure; since: Measured | null; before: Measured | null }
/** What the owner is practising (octo/focus.py). */
export interface Focus {
  id: string
  text: string
  session_id: string | null
  item: string | null
  started_at: number
  ended_at: number | null
  tally: Record<FocusAnswer, number>
  /** A number the focus is tracked with (since 6 Oct), or none. */
  measure?: Measure | null
  /** For a focus with a number: since it started, and before. */
  numbers?: SinceBefore | null
  /** The coach note it came from, if any. */
  coach_note?: string | null
}
/** The focus on a conversation's page: the owner's answer there, and that conversation's own number. */
export type SessionFocus = Focus & { answer: FocusAnswer | null; here?: number | null }
export interface FocusSummary { active: Focus | null; past: Focus[] }

export interface Evidence { quotes: Quote[]; unverified_quotes: number; flags: string[] }

/** The single thing most worth the owner's attention, with its moment and the step that acts on it (since 6 Oct). */
export interface OneThing extends Evidence { headline: string; why: string; next: number | null }

export interface Insights {
  kind?: 'review'             // a review of the whole conversation (octo/review_any.py)
  model: string
  rubric: string
  skipped: string | null
  summary?: string
  one_thing?: OneThing | null
  reading?: ({ person: string; headline: string; observation: string } & Evidence)[]
  between?: ({ headline: string; observation: string } & Evidence)[]
  conduct: Finding[]
  no_moment: number[]
  dropped: { principle?: number; section?: string; headline?: string; reason: string }[]
  beneath: { question: string; quotes: Quote[] }[]
  next?: Task[]
  limits?: string
  /** B15: each intention this conversation took, and whether it happened (null: the review couldn't show it). */
  intention?: ({ person: string; text: string; happened: Happened | null; observation: string } & Evidence)[]
  seconds: number
}

/** B15: an intention before a conversation, and a reflection after it (octo/intention.py). The owner's own words. */
export type Happened = 'yes' | 'partly' | 'no' | 'unclear'
export interface Intention { id: string; text: string; created_at: number }
export interface TakenIntention extends Intention { session_id: string; taken_at: number; happened: Happened | null; reviewed: boolean }
export interface Reflection { text: string; updated_at: number }

export interface ItemFeedback {
  verdict: 'right' | 'partly' | 'wrong' | null
  novelty: 'new' | 'knew' | null
  useful: 'yes' | 'no' | null
  acted: boolean
  note: string | null
  /** A "What to do" step the owner let go: off home's list, not acted on. */
  let_go?: boolean
}

/** The one question per conversation, "What here was new to you?" (since 6 Oct). */
export interface Answer { nothing_new: boolean; new: string[]; answered_at: number }

/** B23: an event's invitee offered as a speaker; person: whom Octo takes them for, sure once the owner said so. */
export interface Invitee {
  key: string
  name: string
  email: string | null
  calendar_name: string | null
  person_id: string | null
  person: string | null
  sure: boolean
}
export interface SessionCalendar { events: { title: string; start: number; end: number }[]; invitees: Invitee[] }

/** B35 (octo/parts.py `page`): a conversation right before or after this one, to read with it as one discussion. */
export interface PartOffer {
  side: 'before' | 'after'
  other: string
  gap_s: number
  minutes: number
  who: string[]
  /** The owner said before that these two aren't one discussion. */
  ungrouped: boolean
}
export interface Part {
  id: string
  n: number
  at: number
  duration_s: number
  /** Seconds since the part before ended; null for the first. */
  gap_s: number | null
  by: 'owner' | 'lab' | null
  /** Why the lab grouped it: the hour cap's cut, a pause, another recording ("joined": an audio join from 29 Oct). */
  kind: 'hour' | 'pause' | 'recordings' | 'joined' | null
  /** The part before it (for Not one discussion). */
  first: string | null
  answered: boolean
}
/** One discussion in parts (B35), or the neighbours a conversation alone could be read with. */
export interface Parts {
  offers?: PartOffer[]
  head?: string
  list?: Part[]
  /** The review shown on the first part reads every part. */
  whole?: boolean
  /** A part was answered before they were grouped: no review of the whole until Redo the review. */
  held?: boolean
  /** On the first part's page: the later parts' lines, numbered and timed as the whole review has them (from the first
   *  part's start); each plays from its own part's audio, at `start` less that part's offset (`at` less the first's). */
  lines?: (Line & { part: string })[]
  /** Who each of those lines' letters is ("C2": speaker C of part 2). */
  people?: Record<string, { name: string; self: boolean }>
}

export interface SessionDetail extends Omit<SessionSummary, 'speakers'> {
  speakers: Speaker[]
  calendar?: SessionCalendar | null
  language: string | null
  lines: Line[]
  turns: Turn[]
  numbers: Numbers | null
  insights: Insights | null
  insight_run: string | null
  /** A rated review kept although what it was made from changed since: the page offers a redo. */
  review_outdated: boolean
  feedback: Record<string, ItemFeedback>
  /** The owner's answer to the one question on the review shown, or null while it waits. */
  answer: Answer | null
  ticktick: Record<string, SentTask>
  ticktick_connected: boolean
  /** The focus active when this conversation happened, and the answer given here. */
  focus: SessionFocus | null
  /** B15: how it went, in the owner's words (the coach reads it, the review doesn't), and the intentions it took. */
  reflection: Reflection | null
  intentions: { id: string; person_id: string; name: string; text: string; created_at: number; taken_at: number }[]
  /** B36: plans made for this conversation (Prepare), beside its review; the review doesn't read them. */
  planned?: { person_id: string; name: string; at: number; aim: string; about: string | null; questions: { purpose: string; items: string[] }[] }[]
  reviewed_at: number | null
  in_gate: boolean
  corrections: number
  left_out: string[]
  audio_deleted: boolean
  split_suggestions: SplitSuggestion[]
  /** Split from the conversation before or after it (octo/split.py `here`): by the owner or by the lab, for Join back. */
  split: { by: 'owner' | 'lab'; side: 'before' | 'after'; other: string | null; at: number } | null
  /** B35: the parts of one discussion it's one of, or the conversations either side to read it with. */
  parts: Parts | null
  /** Letters confirmed as the same name whose voices don't clearly agree: one person, or two? */
  same_person_asks: { name: string; letters: string[] }[]
  /** Stretches the silence filter dropped (and diarize didn't give back): text and start, to hear. */
  dropped: { start: number; text: string }[]
  /** Hindi merged by Claude: per line id, words neither local draft has nearby (underlined). */
  unsupported: Record<string, string[]>
  /** null: this lab keeps its recordings (B29) */
  audio_days: number | null
  /** Why the transcript isn't used (a paused language), e.g. "Mostly Hindi". */
  paused: string | null
  /** Hindi or mixed: transcribed as two local drafts merged by Claude (octo/hindi.py). */
  merged: boolean
  /** The owner's four languages got under half of Whisper's vote: what it heard may be none of them. */
  language_unsure: boolean
  /** Why its review counts only provisionally (J9), until the owner clears it; null otherwise. */
  provisional: string | null
  /** Set aside as not a conversation (B5), or null. */
  aside: AsideKind | null
  /** What was promised both ways (B14); null when set aside or without words. */
  commitments: SessionCommitments | null
  /** The owner's fillers here with their own list (octo/fillers.py), or null without their words. */
  fillers: SessionFillers | null
  /** The recorder's own "you know" cues here (B20, rec-24), on the lines they followed; null when none. */
  recorder_cues: RecorderCue[] | null
  /** The moments the owner marked as they happened (B16): a double tap on the recorder, or the phone's Mark button. */
  marks: Mark[]
  /** How he showed up (B10): six delivery numbers against his usual; null without his words. */
  delivery: SessionDelivery | null
  /** Others' questions and who spoke next (B4): a count, not a judgement; null when the owner isn't confirmed. */
  questions: SessionQuestions | null
}

/** Where the people change: seconds into the conversation, speaker letters on each side; `strong`: nobody shared and
 * the owner barely talking on one side, put first on the page. */
export interface SplitSuggestion { at: number; pause_s: number; overlap: number; before: string[]; after: string[]; strong: boolean }

export interface FixResult { speakers: Speaker[]; todo: Todo; lines: Line[]; turns: Turn[]; corrections: number; left_out: string[] }

export interface Count { value: number; of?: number; provisional?: number }

export interface Progress {
  /** The owner's own lab (reviews before 7 Oct): no beta card there; a buyer's lab shows it. */
  first_lab?: boolean
  counts: {
    /** When the lab started counting reviews; the weekly table puts earlier weeks in one row. */
    since: string
    /** recent_new: "new" over the last 10 reviewed, beside the total (whether novelty wears off). */
    checks: { reviewed: Count; new_share: Count & { of: number }; recent_new: Count & { of: number }; acted: Count }
    wrong: { value: number; of: number }
    /** How the owner answered on current reviews: items new to them; "knew it" (item by item, before 6 Oct);
     *  conversations where nothing was new (the one question, since). */
    novelty: { new: number; knew: number; nothing_new: number }
    last_conversation_at: number | null
  }
  coverage: { n: number; title: string; conversations: number }[]
  /** Each principle in the owner's words, and what each review shown now said about it, newest first. */
  by_principle: { n: number; text: string; seen: { session_id: string; at: number | null; with: string[]; title: string | null;
    observation: string | null; item: string; verdict: 'right' | 'partly' | 'wrong' | null; held?: Held | null }[] }[]
  /** Where every conversation is, and what's waiting on the owner. */
  states?: Record<'reviewed' | 'to_review' | 'waiting_on_you' | 'failed' | 'processing' | 'cant_count' | 'not_counted', number>
  weekly: { week: string; conversations: number; share: number | null; questions: number; talked_over: number }[]
  insight_conversations: number
  /** Practice (B18): its own line, never in the counts above. */
  drills?: DrillLine
}

/** An open "What to do" step from a current review, for home's "From your reviews". */
export interface OpenTask {
  session_id: string
  run: string
  item: string
  at: number | null
  title: string
  due: string | null
  rated: boolean
  counts: boolean
  ticktick: string | null
}

// ---- fillers over time (octo/fillers.py) --------------------------------------------------------

/** Fillers pooled over some of the owner's words, with a rough 95% band for ordinary variation. */
export type QuestionOutcome = 'you' | 'other' | 'nobody'
export interface QuestionMoment { line: number; start: number; end: number; by: string; outcome: QuestionOutcome; reply: number | null; after_s: number | null }
export interface SessionQuestions { asked: number; you: number; other: number; nobody: number; size: 'one' | 'group'; moments: QuestionMoment[]; reply_s: number }
export interface QuestionsBySize { asked: number; you: number; other: number; nobody: number; conversations: number }
export type QuestionsSummary = Record<'one' | 'group', QuestionsBySize>
export type QuestionVerdict = 'answered' | 'unanswered' | 'not_question'
export interface QuestionToCheck extends QuestionMoment { session_id: string; at: number; text: string; by_name: string | null
  reply_end: number | null; verdict: QuestionVerdict | null }
export interface QuestionChecks { by_app: Partial<Record<QuestionOutcome, Partial<Record<QuestionVerdict, number>>>>; checked: number }

export interface Pooled { words: number; fillers: number; conversations: number; rate: number; lo: number; hi: number }

export interface FillerPoint { session_id: string; at: number | null; words: number; fillers: number; rate: number | null; with: string[] }

export interface FillersSummary {
  phrases: string[]
  /** now: the latest ~2,000 words after the first fortnight (null until 500 of them); change: now against the fortnight. */
  trend: { points: FillerPoint[]; line: ({ at: number | null } & Pooled)[]; baseline: Pooled | null; now: Pooled | null;
    change: 'lower' | 'higher' | 'within' | null; baseline_until: number | null; words_after: number }
  groups: { people: ({ name: string } & Pooled)[]; sizes: ({ size: string; label: string } & Pooled)[]; min_words: number }
  suggestions: { lean_on: { phrase: string; count: number; rate: number; others: number }[];
    turn_starts: { word: string; turns: number; share: number; others: number }[] }
  off_line: { session_id: string; at: number | null; reason: string }[]
  /** The owner's most common fillers on the line, most first. */
  common: { phrase: string; count: number }[]
  checks: CheckSummary
  window_words: number
  min_words: number
}

export interface CheckSummary { checks: number; by_ear: number; by_app: number; words: number; share: number | null }

/** One conversation's fillers with the owner's list (conversation page). */
export interface SessionFillers {
  words: number
  fillers: number
  rate: number | null
  by_phrase: Record<string, number>
  on_line: boolean
  reason: string | null
  /** Per line id: [start, end) character spans of the fillers in the owner's lines. */
  spans: Record<string, [number, number][]>
  heaviest: { start: number; end: number; words: number; fillers: number; rate: number; lines: number[] }[]
  phrases: string[]
}

export interface FillerCheckStart {
  candidates: { session_id: string; at: number | null; words: number; with: string[] }[]
  session_id: string | null
  window: { start: number; end: number; jay_s: number } | null
  summary: CheckSummary
}

export interface FillerCheckResult {
  id: string
  by_ear: number
  by_app: number
  words: number
  lines: (Line & { spans: [number, number][] })[]
  summary: CheckSummary
}

/** What went to Claude in a window: reviews, Hindi merges and the coach (octo/sessions.py claude_usage). */
export interface ClaudeUsage { calls: number; minutes: number; tokens: number; unrecorded_calls: number }

export interface Person {
  id: string
  name: string
  self: boolean
  /** When and how they agreed to be recognised by voice (product plan 4.6), or null. */
  consent?: { at: number; how: string; note: string } | null
  /** This lab keeps their voiceprint only with that agreement recorded. */
  needs_consent?: boolean
  voiceprints: number
  seconds: number
  enrolled: number
  conversations: number
  last_heard: number | null
}

export interface Incoming {
  id: string
  name: string
  source: string
  duration_s: number
  created_at: number
  started_at: number | null
  state: 'processing' | 'failed' | 'nothing' | 'dropped'
  error: string | null
  dropped: { id: string; duration_s: number }[]
}

export interface Status {
  processing: boolean
  current: { stage: string; target: string; progress: string | null; started_at: number | null } | null
  queued: number
  code_changed?: boolean
  /** The lab can't save to its folder: macOS is holding it (stuck) or refused (denied). */
  cant_save?: 'stuck' | 'denied' | null
  claude_signed_out?: boolean  // reviews wait for Claude Code's sign-in on the Mac (4 Nov)
  /** When the queue will be empty (B3), while there's work. */
  queue_empty_at?: number | null
  /** Steps to go, with the ones that will be queued as earlier ones finish. */
  queue_steps?: number
  /** Free space on the Mac's disk, GB (under 20: the header warns; under 5: the recorder isn't pulled). */
  free_gb?: number
  /** What reads the transcript here (octo/settings.py privacy): the review's model, on this Mac or not, and Hindi's merge. */
  privacy?: { review: string | null; local: boolean; hindi: string | null }
}

/** The recorder, as last seen on the home Wi-Fi (octo/device.py). */
export interface Device {
  configured: boolean
  seen_at: number | null
  checked_at: number | null
  error: string | null
  syncing: boolean
  state: 'recording' | 'off' | 'starting' | 'no_card' | 'card_full' | 'card_error' | 'mic_error' | null
  want_on: boolean | null
  card_hours_left: number | null
  battery_mv: number | null
  rssi: number | null
  sd_failures: number | null
  dropped_samples: number | null
  uptime_h: number | null
  /** Minutes of audio pulled but not yet joined into a recording. */
  waiting_on_mac: number
}

/** One step of the pipeline, as the Pipeline page shows it (octo/settings.py). */
export interface PipelineChoice { id: string; label: string; why: string; default: boolean; unavailable: string | null; provider?: string | null }
export interface PipelineStep {
  stage: string; n: number; name: string; does: string; why: string; model: string
  fixed: string | null; tried: string[]; version: number; params: Record<string, unknown>
  selected: string | null; choices: PipelineChoice[]
  /** The review in a new lab whose owner hasn't chosen a model yet (product plan 4.1). */
  blocked?: string | null
  counts: { done: number; queued: number; running: number; failed: number; paused: number }
}
export interface PipelineImpact {
  conversations: number; audio_s: number; later: string[]
  estimate_s: number | null; estimate_partial: boolean; keeps: string
}

// ---- the coach (octo/coach.py, 6 Oct) ----------------------------------------------------------

/** A review item a coach note cites: its conversation, what the review said, and its moment. */
export interface CoachEvidence {
  id: string
  session_id: string
  insight_run: string
  item: string
  at: number
  kind: 'one_thing' | 'reading' | 'between' | 'principle' | 'try' | 'step'
  label: string
  headline: string
  held: Held | null
  quote: { text: string; who: string; line: number | null; start: number; end: number | null; mine: boolean } | null
  /** Read when the note is shown: who else was there, and whether the audio is still kept. */
  with: string[]
  audio: boolean
}
export interface CoachTheme { theme: string; why: string; conversations: number; evidence: CoachEvidence[] }
export interface CoachWorkOn extends CoachTheme {
  try: { when: string; instead: string } | null
  know: { measure: Measure | 'focus'; how: string }
  /** The try line as a focus, for the owner to edit. */
  focus_text: string
}
export interface CoachNoteBody {
  work_on: CoachWorkOn | null
  keep: CoachTheme | null
  also: CoachTheme[]
  changed: string
  limits: string
  dropped: { section: string; theme: string; reason: string }[]
  unknown_evidence: string[]
}
export type CoachPart = 'work_on' | 'keep' | 'also0' | 'also1'
export interface CoachMark { verdict: 'right' | 'partly' | 'wrong' | null; novelty: 'new' | 'knew' | null }
export interface CoachNote {
  id: string
  created_at: number
  finished_at: number | null
  status: 'running' | 'done' | 'failed'
  error: string | null
  model: string
  read: { conversations: number; answered: number; items: number; left_out: number; from: number | null; to: number | null } | null
  note: CoachNoteBody | null
  feedback: Partial<Record<CoachPart, CoachMark>>
  seen: boolean
  /** The focus made from it, if any. */
  focus: { id: string; text: string; started_at: number; active: boolean; tally: Record<FocusAnswer, number> } | null
  /** How he'll know: that number since this note, against before it. */
  numbers: SinceBefore | null
}
export interface CoachSummary {
  latest: CoachNote | null
  running: { id: string; created_at: number } | null
  failed: { id: string; error: string; at: number } | null
  earlier: { id: string; created_at: number; theme: string | null }[]
  /** Reviews made since the latest note. */
  new_since: number
  /** Conversations with a review the coach can read, and how many of those it reads (the newest). */
  conversations: number
  reads: number
  can_ask: boolean
  why_not: string | null
  min_conversations: number
}

// ---- a page per person, before you talk (octo/person.py, 6 Oct) --------------------------------

export interface WithAndEveryone { with: Measured | null; everyone: Measured | null }
export interface PersonPage {
  id: string
  name: string
  self: boolean
  /** The newest conversations with them (up to 12); `count` is all of them. */
  conversations: { session_id: string; at: number | null; minutes: number; others: string[]; summary: string;
    one_thing: string | null; todo: Todo }[]
  count: number
  first_at: string | null
  last_at: number | null
  steps: OpenTask[]
  /** What the reviews read in them, newest first; never an item marked not right. */
  readings: { session_id: string; insight_run: string; item: string; at: number | null; headline: string; observation: string;
    quote: { text: string; start: number; line: number | null } | null; partly: boolean; note: string | null; new: boolean }[]
  numbers: {
    talk_share: WithAndEveryone
    questions: WithAndEveryone
    talked_over: WithAndEveryone
    fillers: WithAndEveryone & { words_with: number; min_words: number }
    /** Listening (B13): his wait after them, and their longest stretch, against everyone. */
    patience: WithAndEveryone & { waits_with: number; min_waits: number }
    their_longest: WithAndEveryone
    /** Each conversation with them, oldest first: share 0–1; questions and talking over per 10 minutes; fillers per 100 words. */
    each: { session_id: string; at: number; share: number; questions: number | null; talked_over: number | null; fillers: number | null }[]
  }
  focus: Focus | null
  /** B15: "Next time with them, I want to…", while it waits; and the last one a conversation took. */
  intention: Intention | null
  /** B36: when the plan for the next conversation with them was made, if one is ready. */
  prepared_at?: number | null
  last_intention: TakenIntention | null
  /** Where the coach's latest note saw what to work on with them. */
  coach: { note_id: string; at: number; theme: string; moments: number; try: { when: string; instead: string } | null } | null
}

/** A moment the owner marked (octo/marks.py): seconds into the conversation, and the line it followed. */
export interface Mark { id: string; at: number; source: 'recorder' | 'phone'; line: number | null }

/** A "you know" the recorder flashed while recording (octo/fillers.py recorder_cues): seconds into the conversation. */
export interface RecorderCue { at: number; score: number | null; line: number | null }

/** Practice drills (B18, octo/drills.py): kept apart from conversations (K4). */
export interface DrillTake {
  id: string
  prompt: string
  duration_s: number
  created_at: number
  status: 'queued' | 'done' | 'failed'
  error: string | null
  /** False once the audio went at 60 days; the numbers stay. */
  audio: boolean
}
export interface DrillsSummary {
  today: string | null
  prompts: string[]
  /** The focus's measure, when it's one of the six: it leads. */
  lead: DeliveryMeasure | null
  takes: (DrillTake & { words: number | null })[]
}
/** A take's row: the delivery row against every take before it (the pitch memos first), and the last one's number. */
export interface DrillRow extends DeliveryRow { last: number | null }
// Where the point came (B18, 22 Oct): one call per heard take; its quote checked against what was heard and timed.
export type DrillPoint =
  | { state: 'waiting' | 'off' }
  | { state: 'skipped'; why: string }
  | { state: 'failed'; error: string }
  | { state: 'done'; quote: { text: string; line: number; at: number; measured: boolean } | null; dropped?: string
      short: string; short_words: number; build_up_right: boolean; build_up_why: string }
export interface DrillDetail extends DrillTake {
  text: string
  rows: DrillRow[]
  words: number | null
  min_words: number
  lead?: DeliveryMeasure | null
  point: DrillPoint | null
}
/** Rehearsals (B26, octo/rehearse.py): typed, the model as the other person; kept apart from conversations (K4). */
export interface RehearsalRow {
  id: string
  /** Someone from People (name), or someone described (who). */
  person_id: string | null
  name: string | null
  who: string | null
  created_at: number
  lines: number
  reviewed: boolean
}
export interface RehearsalQuote { line: number; text: string; speaker: 'me' | 'them' }
export interface RehearsalReview {
  skipped?: string
  summary?: string
  one_thing?: { headline: string; why: string; quotes: RehearsalQuote[]; next: number | null } | null
  conduct?: { principle: number; title: string; held: Held | null; observation: string; quotes: RehearsalQuote[] }[]
  next?: { title: string; detail: string; why: string }[]
  limits?: string
  dropped?: { section: string; reason: string }[]
}
export interface Rehearsal extends RehearsalRow {
  hard: string
  want: string
  turns: { by: 'me' | 'them'; text: string; at: number }[]
  review: RehearsalReview | null
  /** The last call's failure, and when a usage limit resets (null for any other failure). */
  error: string | null
  waiting_until: number | null
  max_turns: number
  /** All the model is told of them: their readings as their page shows them. */
  readings: { session_id: string; at: number | null; headline: string; observation: string; partly: boolean; note: string | null }[]
  /** Why the model can't be asked now, or null. */
  why_not: string | null
}
/** B31: talking a conversation through (octo/talk.py). */
export interface TalkMoment { line: number; start: number; text: string; who: string; self: boolean; uncertain: boolean }
export interface Talk {
  turns: { by: 'me' | 'model'; text: string; at: number; moments?: TalkMoment[]; dropped?: number }[]
  /** A reply is being written, in the background: ask again in a few seconds. */
  running: boolean
  error: string | null
  waiting_until: number | null
  /** Why a message can't be sent now (no review yet, a usage limit), or null. */
  why_not: string | null
  max_turns: number
  max_line: number
}
export interface RehearsalsSummary {
  rehearsals: RehearsalRow[]
  people: { id: string; name: string; intention: string | null }[]
  why_not: string | null
}
export interface DrillLine {
  takes: number
  memos: number
  lately_n: number
  rows: { measure: DeliveryMeasure; start: number | null; lately: number | null; latest: number | null }[]
}
