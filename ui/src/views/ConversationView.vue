<script setup lang="ts">
// One conversation, calmer since 6 Oct (docs/coach-plan.md §4.1): one reading column; what happened,
// the one thing and what to do first; the full review as headlines that open; one question at the
// end, "What here was new to you?"; speakers, numbers, the transcript and the fixing tools folded.
// The page holds the conversation, the player and the answers; its sections are in components/conversation/.
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Commitments from '../components/Commitments.vue'
import ConversationHead from '../components/conversation/ConversationHead.vue'
import FocusCheck from '../components/FocusCheck.vue'
import { focusText } from '../lab/delivery'
import FocusEditor from '../components/FocusEditor.vue'
import FullReview from '../components/conversation/FullReview.vue'
import OwnerWords from '../components/OwnerWords.vue'
import PartsPanel from '../components/PartsPanel.vue'
import { markedText } from '../lab/marks'
import PlayerCard from '../components/conversation/PlayerCard.vue'
import QuestionsCard from '../components/conversation/QuestionsCard.vue'
import TalkThrough from '../components/TalkThrough.vue'
import RemovePanel from '../components/RemovePanel.vue'
import ReviewCard from '../components/conversation/ReviewCard.vue'
import SpeakersStrip from '../components/conversation/SpeakersStrip.vue'
import { offers } from '../lab/calendar'
import SplitPanel from '../components/SplitPanel.vue'
import TranscriptCard from '../components/conversation/TranscriptCard.vue'
import { lab, type FeedbackChange } from '../lab/api'
import { forgetConversation, kept, keepConversation } from '../lab/kept'
import { ASIDE, mss, speakerColors, when } from '../lab/format'
import { pref } from '../lab/remembered'
import { itemText, newToYouEntries } from '../lab/review'
import { href } from '../lab/router'
import { canEdit } from '../lab/where'
import type { AsideKind, DeliveryMeasure, Evidence, FixResult, Focus, FocusAnswer, ItemFeedback, Measure, Quote, SessionDetail, Speaker, Todo } from '../lab/types'

const props = defineProps<{ id: string; line?: number }>()

const s = ref<SessionDetail | null>(kept.conversations.get(props.id) ?? null)  // as last seen, until it's asked again
const error = ref('')
const player = ref<InstanceType<typeof PlayerCard> | null>(null)
const audio = computed(() => player.value?.audio ?? null)
const playing = ref(false)
const t = ref(0)
const duration = computed(() => s.value?.duration_s ?? 0)

// ---- loading -----------------------------------------------------------------------------------
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true  // a load still in flight when the page closes mustn't start polling again
// The page-level error is for the first load only. Later, a failed refresh keeps what's shown,
// says so, and tries again (review 4 Oct, M20: one failed action used to blank the whole page).
const stale = ref('')
let first = true
async function load() {
  try {
    const got = await lab.session(props.id)
    if (!alive) return
    s.value = got
    keepConversation(got)
    error.value = ''
    stale.value = ''
    if (first) { first = false; showSpeakers.value = !settledOf(got); if (props.line != null) openAt(props.line) }
  } catch (e) {
    if (!alive) return
    const msg = e instanceof Error ? e.message : String(e)
    if (msg === 'no such conversation') { forgetConversation(props.id); s.value = null }  // deleted meanwhile
    if (s.value && msg !== 'no such conversation') { stale.value = msg; timer = setTimeout(load, 5000); return }
    error.value = msg
  }
  if (s.value?.todo.kind === 'processing') timer = setTimeout(load, 5000)
  if (s.value && !ttAsked && Object.keys(s.value.ticktick).some((k) => !s.value?.feedback[k]?.acted)) {
    ttAsked = true  // once per visit: it asks TickTick about each task
    lab.ticktickDone(s.value.id).then((r) => { ttDone.value = r.done }).catch(() => {})
  }
}
let ttAsked = false
const ttDone = ref<Record<string, boolean | null>>({})

// A split (the lab's when the people change clearly, or the owner's), undone in one tap: the two become one again.
const joining = ref(false)
const joinError = ref('')
async function joinBack() {
  joining.value = true
  joinError.value = ''
  try {
    const r = await lab.joinBack(props.id)
    if (r.session !== props.id) location.hash = href.conversation(r.session)
    else await load()
  } catch (e) { joinError.value = e instanceof Error ? e.message : String(e) } finally { joining.value = false }
}

// A clear "two conversations here?" (split.STRONG_*), put first rather than in the speakers' strip: the lab doesn't cut
// these (the owner, 31 Oct), so the owner splits or says it's one.
const strong = computed(() => s.value?.split_suggestions.find((g) => g.strong) ?? null)
const strongBusy = ref(false)
const strongError = ref('')
async function answerStrong(split: boolean) {
  if (!strong.value) return
  strongBusy.value = true
  strongError.value = ''
  try {
    await (split ? lab.split(props.id, [strong.value.at]) : lab.keepWhole(props.id, strong.value.at))
    await load()
  } catch (e) { strongError.value = e instanceof Error ? e.message : String(e) } finally { strongBusy.value = false }
}

// ---- who's who ---------------------------------------------------------------------------------
const color = computed(() => speakerColors(s.value?.speakers ?? []))
const byLetter = computed(() => Object.fromEntries((s.value?.speakers ?? []).map((sp) => [sp.letter, sp])))

function nameOf(sp: Speaker | undefined): string {
  if (!sp) return '?'
  if (sp.name && (sp.state === 'confirmed' || sp.state === 'auto')) return sp.name
  if (sp.name && sp.state === 'ambiguous') return [sp.name, ...sp.alternatives].join(' or ') + '?'
  if (sp.name) return `${sp.name}?`
  return `Speaker ${sp.letter}`
}
// A later part's speakers, as the whole review lettered them ("C2": speaker C of part 2; B35).
const partPeople = computed(() => s.value?.parts?.people ?? {})
const who = (letter: string | null) =>
  letter && !byLetter.value[letter] && partPeople.value[letter] ? partPeople.value[letter].name : nameOf(letter ? byLetter.value[letter] : undefined)
const isMe = (letter: string | null) => !!(letter && (byLetter.value[letter]?.self ?? partPeople.value[letter]?.self))
const settledOf = (x: SessionDetail) => x.todo.kind !== 'confirm'
const settled = computed(() => s.value !== null && settledOf(s.value))

// ---- the player --------------------------------------------------------------------------------
// One discussion in parts (B35): on the first part's page the transcript and the whole review run on into the later
// parts, timed from the first's start. Those lines play from their own part's audio, in a player of their own.
const allLines = computed(() => [...(s.value?.lines ?? []), ...(s.value?.parts?.lines ?? [])])
const later = computed(() => {
  const list = s.value?.parts?.head === s.value?.id ? s.value?.parts?.list ?? [] : []
  return list.slice(1).map((x) => ({ id: x.id, off: x.at - list[0].at, length: x.duration_s }))
})
const other = typeof Audio === 'undefined' ? null : new Audio()
let otherOff = 0
const playingPart = ref<{ off: number; length: number } | null>(null)  // the player shows its own time and length
/** The player for a moment (seconds from the first part's start), and where that moment is in its audio. */
function audioFor(seconds: number): { a: HTMLAudioElement | null; at: number } {
  const part = [...later.value].reverse().find((x) => seconds >= x.off - 1)
  if (!part || !other) { other?.pause(); playingPart.value = null; return { a: audio.value, at: seconds } }
  audio.value?.pause()
  if (!other.src.endsWith(lab.audioUrl(part.id))) other.src = lab.audioUrl(part.id)
  otherOff = part.off
  playingPart.value = { off: part.off, length: part.length }
  return { a: other, at: Math.max(0, seconds - part.off) }
}
other?.addEventListener('play', () => { playing.value = true })
other?.addEventListener('pause', () => { playing.value = false })
other?.addEventListener('timeupdate', () => {
  t.value = otherOff + other.currentTime
  if (clip.value && t.value >= clip.value.end) { other.pause(); clip.value = null }
})
const current = computed(() => {
  let found = -1
  for (const ln of allLines.value) if (ln.start <= t.value + 0.05) found = ln.id
  return found
})
// A clip plays its line and stops, like "Hear it first" (review 4 Oct, M21); anything else played
// or sought cancels it. Transcript lines still play from there on.
const clip = ref<{ item: string; end: number } | null>(null)
function playFrom(seconds: number) {
  const { a, at } = audioFor(seconds)
  if (!a) return
  clip.value = null
  a.currentTime = at
  void a.play()
}
function playClip(item: string, start: number, end: number) {
  const { a, at } = audioFor(start)
  if (!a) return
  if (clip.value?.item === item && playing.value) { a.pause(); clip.value = null; return }
  a.currentTime = Math.max(0, at - 0.3)
  clip.value = { item, end: end + 0.4 }
  void a.play()
}
function playLine(start: number) {
  const ln = allLines.value.find((l) => Math.abs(l.start - start) < 0.05)
  if (!ln) { playFrom(start); return }
  playClip(`line-${ln.id}`, ln.start, ln.end)
}
// Opened from a search result (B12): the transcript open at that line, and the line played like "hear it".
function openAt(id: number) {
  const ln = allLines.value.find((l) => l.id === id)
  if (!ln) return
  showLines.value = true
  void nextTick(() => {
    document.getElementById(`line-${ln.id}`)?.scrollIntoView({ block: 'center' })
    playClip(`line-${ln.id}`, ln.start, ln.end)
  })
}
function playQuote(q: Quote) {
  const ln = allLines.value.find((l) => l.id === q.line)
  playClip(`o-${q.line}`, q.start, ln?.end ?? q.start + 8)
}
const playingClip = computed(() => (playing.value ? clip.value?.item ?? null : null))
function onTime(e: Event) {
  const a = e.target as HTMLAudioElement
  t.value = a.currentTime
  if (clip.value && a.currentTime >= clip.value.end) { a.pause(); clip.value = null }
}
function toggle() {
  if (other && !other.paused) { other.pause(); clip.value = null; return }
  if (playingPart.value) { playFrom(t.value); return }  // a later part paused: play on from there
  const a = audio.value
  if (!a) return
  clip.value = null
  if (a.paused) void a.play()
  else a.pause()
}
function step(dir: 1 | -1) {
  const lines = allLines.value
  const i = lines.findIndex((l) => l.id === current.value)
  const next = lines[Math.min(lines.length - 1, Math.max(0, i + dir))]
  if (next) {
    playFrom(next.start)
    void nextTick(() => document.getElementById(`line-${next.id}`)?.scrollIntoView({ block: 'nearest' }))
  }
}

// Space plays/pauses and j/k move between lines, only when not typing somewhere.
function onKey(e: KeyboardEvent) {
  const el = e.target instanceof Element ? e.target : null
  if (el?.closest('input, textarea, select, [contenteditable], dialog')) return  // typing
  if (e.metaKey || e.ctrlKey || e.altKey) return
  if (e.key === ' ') {
    if (el?.closest('button, a, summary, label')) return  // Space presses the focused control
    e.preventDefault(); toggle()
  }
  else if (e.key === 'j') step(1)
  else if (e.key === 'k') step(-1)
  else if (e.key === 'Escape') head.value?.close()
}

// ---- fixing who said what ------------------------------------------------------------------------
const known = ref<string[]>([])
const others = (letter: string) => (s.value?.speakers ?? []).filter((x) => x.letter !== letter).map((x) => ({ letter: x.letter, name: nameOf(x) }))
// Worked out once per change of speakers, not per redraw: while a clip plays the page redraws ~4 times a second,
// and a new list each time redrew every speaker row with it, the name field too (13 Oct, naming while hearing).
const invitees = computed(() => offers(s.value?.calendar, s.value?.speakers ?? []))
const othersOf = computed(() => Object.fromEntries((s.value?.speakers ?? []).map((x) => [x.letter, others(x.letter)])))
function applyFix(r: FixResult) {
  if (!s.value) return
  Object.assign(s.value, { speakers: r.speakers, todo: r.todo, lines: r.lines, turns: r.turns, corrections: r.corrections, left_out: r.left_out })
  clearTimeout(timer)
  timer = setTimeout(load, 1500)
}
const fixError = ref('')
async function fix(action: () => Promise<FixResult>) {
  try { applyFix(await action()); fixError.value = '' } catch (e) { fixError.value = `Couldn't change that: ${e instanceof Error ? e.message : e}` }
}
const merge = (letter: string, into: string) => fix(() => lab.merge(props.id, letter, into))
const moveLine = (line: number, to: string) => fix(() => lab.moveLine(props.id, line, to))
const undo = () => fix(() => lab.undoFix(props.id))
function redo() { clearTimeout(timer); void load() }
const retrying = ref(false)
const retryError = ref('')
async function tryAgain() {
  retrying.value = true
  try { const r = await lab.retry(props.id); if (s.value) s.value.todo = r.todo; retryError.value = ''; redo() }
  catch (e) { retryError.value = `Couldn't start it: ${e instanceof Error ? e.message : e}` } finally { retrying.value = false }
}
// An answered review is kept when what it was made from changes; this asks for a fresh one.
const redoing = ref(false)
const redoError = ref('')
async function redoReview() {
  redoing.value = true
  try { const r = await lab.redoReview(props.id); if (s.value) s.value.todo = r.todo; redoError.value = ''; redo() }
  catch (e) { redoError.value = `Couldn't start it: ${e instanceof Error ? e.message : e}` } finally { redoing.value = false }
}
function gone() { forgetConversation(props.id); location.hash = href.conversations }

// Not a conversation (B5, 13 Oct): the TV, music, singing. Offered when nobody here is you, and from ⋯ on any.
const askAside = ref(false)
const asideBusy = ref(false)
const asideError = ref('')
const nobodyIsYou = computed(() => !!s.value && !s.value.speakers.some((sp) => sp.self))
const offerAside = computed(() => !!s.value && !s.value.aside && (askAside.value || nobodyIsYou.value))
async function setAside(kind: AsideKind | null) {
  asideBusy.value = true
  try {
    const r = await lab.aside(props.id, kind)
    if (s.value) Object.assign(s.value, { aside: r.aside, todo: r.todo })
    if (kind) forgetConversation(props.id)  // out of the list
    askAside.value = false
    asideError.value = ''
    if (!kind) redo()  // put back: processing carries on
  } catch (e) { asideError.value = `Couldn't: ${e instanceof Error ? e.message : e}` } finally { asideBusy.value = false }
}
const provisionalError = ref('')
async function clearProvisional() {
  try { await lab.clearProvisional(props.id); if (s.value) s.value.provisional = null; provisionalError.value = '' }
  catch (e) { provisionalError.value = `Couldn't save that: ${e instanceof Error ? e.message : e}` }
}
function changed(r: { speakers: Speaker[]; todo: Todo }) {
  if (!s.value) return
  s.value.speakers = r.speakers
  s.value.todo = r.todo
  clearTimeout(timer)
  timer = setTimeout(load, 1500)  // the pipeline picks the change up in the background
}
// "hear them": their longest line, or their longest turn when there are no words (B1, 13 Oct: a
// conversation whose words were all dropped had speakers and nothing to play), as a clip of up to 10 s.
function longest<T extends { start: number; end: number }>(xs: T[]): T | undefined {
  return xs.reduce<T | undefined>((a, x) => (!a || x.end - x.start > a.end - a.start ? x : a), undefined)
}
function sampleOf(letter: string): { start: number; end: number } | undefined {
  return longest((s.value?.lines ?? []).filter((l) => l.speaker === letter))
    ?? longest((s.value?.turns ?? []).filter((x) => x.speaker === letter))
}
function sample(letter: string) {
  const at = sampleOf(letter)
  if (at) playClip(`sp-${letter}`, at.start, Math.min(at.end, at.start + 10))
}

// ---- the folded parts, remembered in the lab --------------------------------------------------
const showSpeakers = ref(false)
// Speakers to confirm is the one thing blocking the review: open them when it comes to that.
watch(() => s.value?.todo.kind, (kind, was) => { if (kind === 'confirm' && was !== 'confirm') showSpeakers.value = true })
const lineById = computed(() => Object.fromEntries(allLines.value.map((l) => [l.id, l])))
const showLines = pref('octo:transcript', false)
// Your fillers underlined in your lines (6 Oct), switched from How you showed up.
const markFillers = pref('octo:mark-fillers', true)

const smooth = (): ScrollBehavior => (matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')
// The ⋯ menu's tools, opened at the bottom of the page.
const head = ref<InstanceType<typeof ConversationHead> | null>(null)
const tools = ref<'' | 'split' | 'remove'>('')
const toolsEl = ref<HTMLElement | null>(null)
function openTool(which: 'split' | 'remove') {
  head.value?.close()
  tools.value = which
  void nextTick(() => toolsEl.value?.scrollIntoView({ block: 'start', behavior: smooth() }))
}

onMounted(() => {
  void load()
  void lab.people().then((ps) => { known.value = ps.map((p) => p.name) }).catch(() => {})
  void lab.focus().then((f) => { activeFocus.value = f.active }).catch(() => {})
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  alive = false
  other?.pause()  // a later part's player isn't in the page: it would play on
  clearTimeout(timer)
  window.removeEventListener('keydown', onKey)
})

// ---- the review ----------------------------------------------------------------------------------
const ins = computed(() => (s.value?.insights && !s.value.insights.skipped ? s.value.insights : null))
const isReview = computed(() => ins.value?.kind === 'review')
const askEntries = computed(() => newToYouEntries(ins.value))
// Rated item by item before 6 Oct: reviewed then. Shown as answered, with what was marked new; it can be changed.
const legacyAnswer = computed(() => !!s.value && !s.value.answer && s.value.todo.kind === 'reviewed')
const shownAnswer = computed(() => {
  const cur = s.value
  if (!cur || cur.answer || !legacyAnswer.value) return cur?.answer ?? null
  const picked = askEntries.value.filter((e) => cur.feedback[e.id]?.novelty === 'new').map((e) => e.id)
  return { nothing_new: picked.length === 0, new: picked, answered_at: cur.reviewed_at ?? 0 }
})
// Each headline in the full review can be heard without opening it ("Hear it first", 2 Oct): its
// quote's whole line, then it stops.
const canHear = computed(() => !!s.value && !s.value.audio_deleted)
function hear(item: string, q: Quote | null) {
  if (!q) return
  const ln = (s.value?.lines ?? []).find((l) => l.id === q.line)
  playClip(`i-${item}`, q.start, ln?.end ?? q.start + 8)
}
const quotesFound = computed(() => {
  const x = ins.value
  if (!x) return { found: 0, missing: 0 }
  const ev: Evidence[] = [...(x.one_thing ? [x.one_thing] : []), ...(x.reading ?? []), ...(x.between ?? []), ...x.conduct]
  return { found: ev.reduce((a, e) => a + e.quotes.length, 0), missing: ev.reduce((a, e) => a + (e.unverified_quotes ?? 0), 0) }
})

const changeError = ref('')
async function change(item: string, c: FeedbackChange) {
  const cur = s.value
  if (!cur?.insight_run) return
  const before: ItemFeedback | undefined = cur.feedback[item]
  const empty: ItemFeedback = { verdict: null, novelty: null, useful: null, acted: false, note: null, let_go: false }
  const shown = { ...c, ...(c.verdict === '' ? { verdict: null } : {}) } as Partial<ItemFeedback>
  cur.feedback = { ...cur.feedback, [item]: { ...(before ?? empty), ...shown } }  // show it at once; the server confirms
  try {
    const r = await lab.feedback(cur.id, cur.insight_run, item, c)
    cur.feedback = r.feedback
    cur.todo = r.todo
    changeError.value = ''
  } catch (e) {
    const reverted = { ...cur.feedback }
    if (before) reverted[item] = before
    else delete reverted[item]
    cur.feedback = reverted
    changeError.value = `Couldn't save that: ${e instanceof Error ? e.message : e}`
  }
}

const answering = ref(false)
const answerError = ref('')
async function submitAnswer(picked: string[], nothingNew: boolean) {
  const cur = s.value
  if (!cur?.insight_run) return
  answering.value = true
  try {
    const r = await lab.answer(cur.id, cur.insight_run, picked, nothingNew)
    cur.feedback = r.feedback
    cur.answer = r.answer
    cur.todo = r.todo
    answerError.value = ''
  } catch (e) {
    answerError.value = `Couldn't save that: ${e instanceof Error ? e.message : e}`
  } finally {
    answering.value = false
  }
}

const highlight = ref<string | null>(null)
function goToStep() {
  const i = ins.value?.one_thing?.next
  if (i == null) return
  highlight.value = `n${i}`
  void nextTick(() => document.getElementById(`item-n${i}`)?.scrollIntoView({ block: 'center', behavior: smooth() }))
  setTimeout(() => { if (highlight.value === `n${i}`) highlight.value = null }, 2400)
}

// ---- TickTick ------------------------------------------------------------------------------------
const sending = ref<string | null>(null)
const ttError = ref('')
async function toTickTick(item: string) {
  const cur = s.value
  if (!cur?.insight_run || sending.value) return
  sending.value = item
  ttError.value = ''
  try {
    cur.ticktick = (await lab.ticktick(cur.id, cur.insight_run, item)).ticktick
  } catch (e) {
    ttError.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = null
  }
}
async function toTickTickAll() {
  const cur = s.value
  for (const [i] of (cur?.insights?.next ?? []).entries()) {
    const item = `n${i}`
    if (cur?.ticktick[item] || cur?.feedback[item]?.acted || cur?.feedback[item]?.let_go) continue
    await toTickTick(item)
    if (ttError.value) break  // stop at the first failure; the message says why
  }
}

// ---- the focus (coaching plan C2) ----------------------------------------------------------------
const activeFocus = ref<Focus | null>(null)
const focusItem = computed(() => (activeFocus.value?.session_id === props.id ? activeFocus.value.item : null))
// From a review item, or (item null) from a row of How you showed up, carrying its number.
const practising = ref<{ item: string | null; text: string; measure?: Measure } | null>(null)
const focusSaving = ref(false)
const focusError = ref('')
function practise(item: string) {
  focusError.value = ''
  practising.value = { item, text: itemText(ins.value, item) }
}
function practiseMeasure(m: DeliveryMeasure) {
  focusError.value = ''
  practising.value = { item: null, text: focusText(m), measure: m }
}
async function saveFocus(text: string, measure: Measure | null) {
  const cur = s.value
  const item = practising.value?.item
  if (!cur || !practising.value || (item && !cur.insight_run)) return
  focusSaving.value = true
  try {
    const from = item && cur.insight_run ? { session_id: cur.id, run: cur.insight_run, item } : undefined
    activeFocus.value = (await lab.startFocus(text, from, measure)).active
    practising.value = null
  } catch (e) {
    focusError.value = e instanceof Error ? e.message : String(e)
  } finally {
    focusSaving.value = false
  }
}
// ---- B15: how it went in the owner's words (above the review, so it's written first; beside what the review saw once
// both exist).
async function saveReflection(text: string) {
  const cur = s.value
  if (cur) cur.reflection = (await lab.reflection(cur.id, text)).reflection
}
const reflectBeside = computed(() => isReview.value && !!ins.value && !!s.value?.reflection)

const checking = ref(false)
const checkError = ref('')
async function answerFocus(a: FocusAnswer | null) {
  const cur = s.value
  if (!cur?.focus) return
  checking.value = true
  try {
    const r = await lab.focusCheck(cur.id, cur.focus.id, a)
    cur.focus = r.focus  // "did" marks the item in the review it came from, not one here
    checkError.value = ''
  } catch (e) {
    checkError.value = `Couldn't save that: ${e instanceof Error ? e.message : e}`
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <main class="page">
    <a :href="href.conversations" class="back">← Conversations</a>
    <p v-if="error && !s" class="error" role="alert">{{ error === 'no such conversation' ? 'This conversation no longer exists.' : error }}</p>
    <p v-else-if="!s" class="muted loading" role="status">Loading…</p>

    <template v-else>
      <ConversationHead ref="head" :s="s" :retrying="retrying" @retry="tryAgain" @tool="openTool" @aside="askAside = true" />
      <p v-if="s.split" class="split-note small" role="note">
        <template v-if="s.split.by === 'lab'">The lab split this from the conversation {{ s.split.side }} it: the people
          changed after a long pause.</template>
        <template v-else>You split this from the conversation {{ s.split.side }} it.</template>{{ ' ' }}<a
          v-if="s.split.other" :href="href.conversation(s.split.other)">Open that one</a>
        <template v-if="canEdit"> · <button type="button" class="link" :disabled="joining" @click="joinBack">Join back</button></template>
        <span v-else class="muted"> · Join back on your Mac.</span>
        <span v-if="joinError" class="error" role="alert"> {{ joinError }}</span>
      </p>
      <PartsPanel :s="s" :can-edit="canEdit" @changed="load" />
      <section v-if="strong" class="aside-note" aria-label="Two conversations?">
        <span><strong>Two conversations?</strong> The people change at {{ mss(strong.at) }}:
          {{ strong.before.map((l) => nameOf(byLetter[l])).join(', ') }} before,
          {{ strong.after.map((l) => nameOf(byLetter[l])).join(', ') }} after.</span>
        <div class="aside-kinds">
          <button v-if="!s.audio_deleted" type="button" class="btn btn-small" @click="playFrom(Math.max(0, strong.at - 10))">Hear the change</button>
          <template v-if="canEdit">
            <button type="button" class="btn btn-small btn-primary" :disabled="strongBusy" @click="answerStrong(true)">Split here</button>
            <button type="button" class="btn btn-small" :disabled="strongBusy" @click="answerStrong(false)">It's one</button>
          </template>
          <span v-else class="muted small">Split it on your Mac.</span>
        </div>
      </section>
      <p v-if="strongError" class="error small" role="alert">{{ strongError }}</p>
      <p v-if="s.todo.kind === 'failed' && s.todo.error" class="muted small note-line">{{ s.todo.error }}</p>
      <p v-if="retryError" class="error small" role="alert">{{ retryError }}</p>
      <section v-if="s.aside" class="aside-note" role="note">
        <span><strong>Not a conversation · {{ ASIDE[s.aside] }}.</strong> It's out of your list, isn't processed and never
          counts.<template v-if="s.audio_days"> Its audio goes after {{ s.audio_days }} days like any other.</template></span>
        <button type="button" class="btn btn-small" :disabled="asideBusy" @click="setAside(null)">It's a conversation</button>
      </section>
      <section v-else-if="offerAside" class="aside-ask" aria-label="Not a conversation?">
        <span class="muted">{{ nobodyIsYou && !askAside ? "Nobody here is you. Not a conversation?" : "Not a conversation?" }}</span>
        <div class="aside-kinds">
          <button v-for="(label, kind) in ASIDE" :key="kind" type="button" class="btn btn-small" :disabled="asideBusy"
                  @click="setAside(kind)">{{ label }}</button>
          <button v-if="askAside" type="button" class="link small" @click="askAside = false">Cancel</button>
        </div>
      </section>
      <p v-if="asideError" class="error small" role="alert">{{ asideError }}</p>
      <p v-if="s.provisional" class="outdated small">Provisional: {{ s.provisional }}. It counts on Progress, marked
        as provisional. Listen to two or three stretches against the transcript; if it holds up,
        <button type="button" class="link" @click="clearProvisional">it holds up: count it in full</button>.
        <span v-if="provisionalError" class="error" role="alert"> {{ provisionalError }}</span></p>
      <p v-if="s.language_unsure" class="muted small note-line">Language unsure: English, Hindi, Kannada and Gujarati together got
        under half of Whisper's vote, so it may have heard something else.</p>
      <p v-if="stale" class="error small" role="status">Couldn't refresh ({{ stale }}); showing what was loaded, trying again.</p>

      <PlayerCard ref="player" :s="s" :t="playingPart ? t - playingPart.off : t" :duration="playingPart?.length ?? duration"
                  :playing="playing" :color="color" :who="who" :name-of="nameOf"
                  @playing="playing = $event" @time="onTime" @toggle="toggle" @seek="playFrom((playingPart?.off ?? 0) + $event)" />

      <SpeakersStrip v-model:show-speakers="showSpeakers" :s="s" :color="color" :name-of="nameOf" :known="known" :others-of="othersOf"
                     :invitees="invitees" :has-sample="(l) => !!sampleOf(l)" :playing-clip="playingClip" :fix-error="fixError"
                     @changed="changed" @sample="sample" @merge="merge" @undo="undo" @split="openTool('split')" />

      <FocusCheck v-if="s.focus" :focus="s.focus" :saving="checking" @answer="answerFocus" />
      <p v-if="checkError" class="error small" role="alert">{{ checkError }}</p>
      <p v-if="s.marks?.length" class="marked small"><span aria-hidden="true">⚑ </span>{{ markedText(s.marks.length) }}:
        <template v-for="(m, i) in s.marks" :key="m.id">{{ i ? ', ' : '' }}<button type="button" class="link"
          :aria-label="`Hear the moment you marked at ${mss(m.at)}`"
          @click="m.line != null ? openAt(m.line) : playFrom(Math.max(0, m.at - 60))">{{ mss(m.at) }}</button></template><span
          class="muted">. The review reads the minute before {{ s.marks.length === 1 ? 'it' : 'each' }}.</span></p>
      <p v-if="s.review_outdated" class="outdated small"><template v-if="s.parts?.list && !s.parts.whole">This review reads
        only this part: you'd answered it before its parts were put together. Redo it to have the whole discussion read
        as one.</template><template v-else>This review was made before later changes (a speaker fix, a
        name, a new transcript).</template> Your answers stay with it and still count.
        <button type="button" class="link" :disabled="redoing" @click="redoReview">Redo the review</button>
        <span v-if="redoError" class="error" role="alert"> {{ redoError }}</span></p>

      <section v-if="!s.aside && !reflectBeside && s.todo.kind !== 'part'" class="card reflect" aria-label="In your words">
        <OwnerWords label="How did that go, in your words?" :text="s.reflection?.text ?? null" :save="saveReflection" multiline :max="4000"
                    placeholder="Optional. Before you read the review." />
        <p class="muted small">The review doesn't wait for it or read it; the coach reads it beside your marks.</p>
      </section>

      <section v-for="pl in s.planned ?? []" :key="pl.person_id" class="card planned" :aria-label="`What you planned with ${pl.name}`">
        <p class="small muted">What you planned with {{ pl.name }} · {{ when(pl.at) }}</p>
        <p class="aim">{{ pl.aim }}</p>
        <details v-if="pl.questions.length">
          <summary class="small">The questions you meant to ask</summary>
          <div v-for="(g, i) in pl.questions" :key="i" class="small"><strong>{{ g.purpose }}</strong>
            <ul><li v-for="(q, j) in g.items" :key="j">{{ q }}</li></ul></div>
        </details>
        <a class="small" :href="href.prepare(pl.person_id)">The whole plan</a>
      </section>

      <ReviewCard v-if="!s.aside" v-model:mark-fillers="markFillers" :s="s" :ins="ins" :settled="settled" :show-speakers="showSpeakers"
                  :reflect-beside="reflectBeside" :save-reflection="saveReflection" :can-hear="canHear" :color="color" :who="who"
                  :focus-item="focusItem" :playing-clip="playingClip" :sending="sending" :tt-done="ttDone" :highlight="highlight"
                  :tt-error="ttError" :change-error="changeError"
                  @confirm="showSpeakers = true" @play="playClip" @play-quote="playQuote" @focus="practiseMeasure" @changed="load"
                  @practise="practise" @step="goToStep" @change="change" @ticktick="toTickTick" @ticktick-all="toTickTickAll" />

      <!-- The full review asks the question that makes a conversation reviewed, right after what to do (product/UI
           review, 28 Oct: it had drifted 3.4 screens down); each headline is ticked where it's read (the owner, 4 Nov). -->
      <FullReview v-if="settled" :ins="ins" :is-review="isReview" :feedback="s.feedback" :can-hear="canHear" :is-me="isMe" :who="who"
                  :color="color" :focus-item="focusItem" :playing-clip="playingClip"
                  :ask="askEntries.length > 0 && !!s.insight_run" :answer="shownAnswer" :legacy="legacyAnswer"
                  :saving="answering" :error="answerError"
                  @hear="hear" @play="playLine" @practise="practise" @change="change" @submit="submitAnswer" />

      <Commitments v-if="settled && s.commitments" :block="s.commitments" :at="s.started_at" :connected="s.ticktick_connected"
                   :can-hear="canHear" :playing="playingClip" @play="playClip" />

      <TalkThrough v-if="settled && isReview && !s.aside" :session-id="s.id" @open="openAt" />

      <details v-if="ins" class="about muted small">
        <summary>About this review</summary>
        <p>{{ ins.model }}<template v-if="ins.seconds"> · took {{ Math.round(ins.seconds) }} s</template> · rubric of {{ ins.rubric.slice(0, 6) }}</p>
        <p>{{ quotesFound.found }} quote{{ quotesFound.found === 1 ? '' : 's' }} found in the transcript<template v-if="quotesFound.missing">;
          {{ quotesFound.missing }} not found, left out</template>.</p>
        <p v-if="ins.no_moment.length">No moment for principles {{ ins.no_moment.join(', ') }}: nothing is written for them.</p>
        <p v-if="ins.dropped.length">Dropped for lack of evidence:
          {{ ins.dropped.map((d) => d.headline || `principle ${d.principle}`).join('; ') }}.</p>
        <p v-if="ins.limits">What it can't see: {{ ins.limits }}</p>
      </details>

      <QuestionsCard v-if="s.questions?.asked" :questions="s.questions" :line-by-id="lineById" :who="who" :color="color"
                     :playing-clip="playingClip" @play="playClip" />

      <TranscriptCard v-model:show-lines="showLines" :s="s" :current="current" :playing="playing" :t="t" :mark-fillers="markFillers"
                      :color="color" :who="who" :name-of="nameOf" @play="playFrom" @move="moveLine" />

      <section v-if="tools" ref="toolsEl" class="tools" aria-label="Tools">
        <div class="tools-head"><span class="muted small">Tools for this conversation</span>
          <button type="button" class="link" @click="tools = ''">Close</button></div>
        <SplitPanel v-if="tools === 'split'" :id="s.id" :suggestions="s.split_suggestions" :lines="s.lines" :duration="duration" :now="t"
                    :audio-deleted="s.audio_deleted" :who="who" @play="playLine" @split="redo" />
        <RemovePanel v-if="tools === 'remove'" :id="s.id" :delete-only="!canEdit" :speakers="s.speakers" :left-out="s.left_out" :duration="duration" :now="t"
                     :audio-deleted="s.audio_deleted" :name-of="nameOf" :parts="s.parts?.list ?? null"
                     @fixed="applyFix" @redo="redo" @gone="gone" @ungrouped="load" />
      </section>
    </template>
    <FocusEditor :open="!!practising" :initial="practising?.text ?? ''" :replaces="activeFocus?.text ?? null"
                 :measure="practising?.measure ?? null" :saving="focusSaving" :error="focusError" @save="saveFocus"
                 @cancel="practising = null" />
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 860px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; }
/* stays at the top while you read, so going back needs no scroll (28 Oct) */
.page > .back { position: sticky; top: 0; z-index: 5; padding: 10px 0; margin: -10px 0; background: var(--ground); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.marked { color: var(--accent, currentColor); }
p.marked { margin: 0; }
.error { color: var(--fail); margin: 0; }
.note-line { margin: -6px 0 0; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.outdated { margin: 0; padding: 10px 12px; border-radius: 10px; background: var(--warn-soft); color: var(--ink); }
.split-note { margin: 0 0 12px; color: var(--muted); }
/* Not a conversation (B5): asked when nobody here is you; once set aside, said once, with the way back. */
.aside-ask, .aside-note { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; margin: 0 0 12px; }
.aside-note { padding: 10px 12px; border-radius: 10px; background: var(--quote); }
.aside-kinds { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.aside-kinds .btn-small, .aside-note .btn-small { min-height: 44px; }

.reflect { padding: 14px 18px; display: flex; flex-direction: column; gap: 6px; }
.reflect p { margin: 0; }
.planned { padding: 14px 18px; display: flex; flex-direction: column; gap: 6px; }
.planned p { margin: 0; }
.planned .aim { font-weight: 500; }
.planned summary { cursor: pointer; padding: 6px 0; }
.planned ul { margin: 4px 0 6px; padding-left: 18px; }

.about { padding: 0 4px; }
.about summary { cursor: pointer; min-height: 36px; display: flex; align-items: center; }
.about p { margin: 4px 0 0; }

.tools { display: flex; flex-direction: column; gap: 10px; scroll-margin-top: 96px; }
.tools-head { display: flex; justify-content: space-between; align-items: center; }
.tools-head .link { min-height: 36px; }

@media (max-width: 760px) {
  .page { padding: 14px 16px 40px; gap: 14px; }
  .link { min-height: 44px; }
  p .link { min-height: 0; padding: 12px 0; margin: -12px 0; }  /* in a line of text: the tap area grows, the line doesn't */
}
</style>
