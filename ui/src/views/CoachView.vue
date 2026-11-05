<script setup lang="ts">
// The coach (docs/coach-plan.md §4.3; 6 Oct: "if this app were a coach, what would it tell me
// to improve on"). One note at a time, in reading order: what to work on, its moments to hear, what
// to try, how you'll know; what to keep; other patterns, folded; what changed since the last note;
// what it can't tell. Each part takes the owner's marks (not the counts on Progress). Earlier notes stay readable: ‹ › on the
// date line step through them, each at #/coach/<id> (B11).
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import CoachMarks from '../components/CoachMarks.vue'
import CoachMoments from '../components/CoachMoments.vue'
import FocusEditor from '../components/FocusEditor.vue'
import SolvedFold from '../components/SolvedFold.vue'
import { lab } from '../lab/api'
import { day, MEASURE_LABEL, measureValue, tallyText, when } from '../lab/format'
import { href } from '../lab/router'
import type { CoachEvidence, CoachMark, CoachNote, CoachPart, CoachSummary, Focus, Measure, SolvedInput } from '../lab/types'

const props = defineProps<{ noteId?: string }>()  // an earlier note, from #/coach/<id>; else the latest
const sum = ref<CoachSummary | null>(null)
const note = ref<CoachNote | null>(null)  // the note shown
const error = ref('')
const asking = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  try {
    sum.value = await lab.coach()
    if (!props.noteId) note.value = sum.value.latest
    error.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  if (sum.value?.running) { if (!timer) timer = setInterval(load, 4000) } else if (timer) { clearInterval(timer); timer = undefined }
  seen()
}
function seen() {
  const n = note.value
  if (n && n.status === 'done' && !n.seen) lab.coachSeen(n.id).then(() => { n.seen = true }).catch(() => {})
}
const activeFocus = ref<Focus | null>(null)
onMounted(() => {
  void load()
  lab.focus().then((f) => { activeFocus.value = f.active }).catch(() => {})
})
onUnmounted(() => { if (timer) clearInterval(timer); audio.value?.pause() })

async function ask() {
  asking.value = true
  try { sum.value = await lab.askCoach(); error.value = ''; if (!timer) timer = setInterval(load, 4000); if (props.noteId) location.hash = href.coach }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { asking.value = false }
}
const isLatest = computed(() => !!note.value && note.value.id === sum.value?.latest?.id)
async function openNote(id: string | undefined) {
  if (!id) { note.value = sum.value?.latest ?? null; return }
  try { note.value = await lab.coachNote(id); error.value = ''; seen() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
watch(() => props.noteId, openNote, { immediate: true })
// The notes newest first; the shown note's neighbours are the ‹ older and newer › ones.
const notes = computed(() => (sum.value?.latest ? [sum.value.latest, ...sum.value.earlier] : []))
const at = computed(() => notes.value.findIndex((n) => n.id === note.value?.id))
const older = computed(() => (at.value >= 0 ? notes.value[at.value + 1] ?? null : null))
const newer = computed(() => (at.value > 0 ? notes.value[at.value - 1] : null))
const noteHref = (id: string) => (id === sum.value?.latest?.id ? href.coach : href.coachNote(id))

const body = computed(() => note.value?.note ?? null)
const work = computed(() => body.value?.work_on ?? null)
const read = computed(() => note.value?.read ?? null)
const reviews = (n: number) => `${n} review${n === 1 ? '' : 's'}`
const conversations = (n: number) => `${n} conversation${n === 1 ? '' : 's'}`
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

// ---- marks --------------------------------------------------------------------------------------
const savingMark = ref<CoachPart | null>(null)
async function mark(part: CoachPart, m: { verdict?: CoachMark['verdict'] | ''; novelty?: CoachMark['novelty'] | '' }) {
  const n = note.value
  if (!n) return
  savingMark.value = part
  try { n.feedback = (await lab.markCoach(n.id, part, m)).feedback; error.value = '' }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { savingMark.value = null }
}

// ---- the focus ----------------------------------------------------------------------------------
const editing = ref(false)
const focusSaving = ref(false)
const focusError = ref('')
const knowMeasure = computed<Measure | null>(() => {
  const m = work.value?.know.measure
  return m && m !== 'focus' ? m : null
})
async function saveFocus(text: string, measure: Measure | null) {
  const n = note.value
  if (!n) return
  focusSaving.value = true
  try {
    const r = await lab.coachFocus(n.id, text, measure)
    note.value = r.note
    activeFocus.value = r.focus.active
    editing.value = false
    focusError.value = ''
  } catch (e) { focusError.value = e instanceof Error ? e.message : String(e) } finally { focusSaving.value = false }
}

// B38: who has solved what it says to work on, in the owner's words
async function saveSolved(s: SolvedInput) {
  if (note.value) note.value = await lab.coachSolved(note.value.id, s)
}

// ---- hearing a moment: its line from that conversation's audio, then it stops ------------------
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref<string | null>(null)
let clipEnd = 0
function play(e: CoachEvidence) {
  const a = audio.value
  if (!a || !e.quote) return
  if (playing.value === e.id) { a.pause(); return }
  const src = new URL(lab.audioUrl(e.session_id), location.href).href
  const start = Math.max(0, e.quote.start - 0.3)
  clipEnd = (e.quote.end ?? e.quote.start + 8) + 0.4
  playing.value = e.id
  const go = () => { a.currentTime = start; void a.play().catch(() => { playing.value = null }) }
  if (a.src !== src) { a.src = src; a.addEventListener('loadedmetadata', go, { once: true }); a.load() } else go()
}
function onTime() {
  const a = audio.value
  if (a && playing.value && a.currentTime >= clipEnd) a.pause()
}
</script>

<template>
  <main class="page">
    <div class="top">
      <h1>Coach</h1>
      <button v-if="sum && (sum.latest || sum.failed) && !sum.running" type="button" class="btn" :disabled="!sum.can_ask || asking"
              :title="sum.can_ask ? undefined : sum.why_not ?? undefined" @click="ask">{{ asking ? 'Asking…' : 'Ask again' }}</button>
    </div>
    <p class="lead">It reads the reviews of your conversations together, with what you marked new, not right and done, your
      focus and your numbers, and names one thing to work on. One Claude call each time you ask; it's sent the reviews,
      never the audio.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!sum && !error" class="muted loading" role="status">Loading…</p>

    <template v-if="sum">
      <section v-if="sum.running" class="card pad status" role="status">
        <span class="dots" aria-hidden="true"><i /><i /><i /></span>
        <div><b>Reading your {{ reviews(sum.reads) }} together…</b>
          <div class="muted small">A minute or two. You can leave this page; the note waits for you here and on home.</div></div>
      </section>
      <section v-else-if="sum.failed" class="card pad" role="alert">
        <b>The coach couldn't finish.</b> <span class="muted">{{ sum.failed.error }}</span>
      </section>
      <p v-if="!sum.can_ask && !sum.running && sum.why_not && (sum.latest || sum.failed)" class="muted small">{{ cap(sum.why_not) }}.</p>
      <p v-if="isLatest && sum.new_since > 0 && !sum.running" class="muted small">{{ reviews(sum.new_since) }} since this note.</p>

      <section v-if="!sum.latest && !sum.running && !sum.failed" class="card pad empty" aria-labelledby="ask-h">
        <h2 id="ask-h">What would a coach tell you to work on?</h2>
        <p class="muted">It needs reviews of at least {{ sum.min_conversations }} conversations: a pattern is something seen
          twice. You have {{ reviews(sum.conversations) }} it can read.</p>
        <div><button type="button" class="btn btn-primary" :disabled="!sum.can_ask || asking" @click="ask">{{ asking ? 'Asking…' : 'Ask the coach' }}</button></div>
        <p v-if="!sum.can_ask && sum.why_not" class="muted small">{{ cap(sum.why_not) }}.</p>
      </section>
    </template>

    <template v-if="note && body">
      <div class="meta muted small">
        <span>{{ isLatest ? 'Latest note' : 'Earlier note' }} · {{ when(note.created_at) }}<template v-if="read"> · read {{ reviews(read.conversations) }}
          ({{ read.answered }} you've answered)<template v-if="read.from && read.to">, {{ day(read.from) }} to {{ day(read.to) }}</template></template></span>
        <nav v-if="older || newer" class="step" aria-label="Coach notes">
          <a v-if="older" :href="noteHref(older.id)" class="link" :aria-label="`Earlier note, ${day(older.created_at)}`">‹ {{ day(older.created_at) }}</a>
          <a v-if="newer" :href="noteHref(newer.id)" class="link" :aria-label="`Later note, ${day(newer.created_at)}`">{{ day(newer.created_at) }} ›</a>
          <a v-if="newer && newer.id !== sum?.latest?.id" :href="href.coach" class="link">Latest</a>
        </nav>
      </div>

      <section v-if="work" class="work" aria-labelledby="w-h">
        <div class="eyebrow-c">Work on<span class="count">seen in {{ conversations(work.conversations) }}</span>
          <span v-if="note.focus?.active" class="badge">Your focus</span></div>
        <h2 id="w-h">{{ work.theme }}</h2>
        <p class="why">{{ work.why }}</p>
        <CoachMoments :evidence="work.evidence" :playing="playing" @play="play" />
        <div v-if="work.try" class="try">
          <div class="label">Try</div>
          <p>{{ work.focus_text }}</p>
        </div>
        <div v-if="body.would" class="try">
          <div class="label">What would {{ body.would.who }} do here?</div>
          <p>{{ body.would.text }}</p>
          <p class="small muted">From your note on {{ body.would.on === 'focus' ? 'your focus' : 'the last note' }}<template
            v-if="body.would.source"> ({{ body.would.source }})</template> only: your account of them, not what they think.</p>
        </div>
        <div class="know">
          <div class="label">You'll know</div>
          <p>{{ work.know.how }}</p>
          <p v-if="note.numbers" class="small num">{{ MEASURE_LABEL[note.numbers.measure] }}:
            <template v-if="note.numbers.since"><b>{{ measureValue(note.numbers.measure, note.numbers.since.value) }}</b> since this note
              ({{ conversations(note.numbers.since.conversations) }})</template><template v-else>no conversation since this note yet</template><template
              v-if="note.numbers.before"> · {{ measureValue(note.numbers.measure, note.numbers.before.value) }} before it</template>.
            <a v-if="note.numbers.measure === 'fillers'" :href="href.progress">Your fillers over time</a></p>
          <p v-else-if="work.know.measure === 'focus'" class="small muted">As your focus, each conversation after it asks whether it came up and whether you did it.</p>
        </div>
        <div class="acts">
          <template v-if="note.focus?.active">
            <span class="small"><b>Your focus</b> since {{ day(note.focus.started_at) }}<template v-if="tallyText(note.focus.tally)"> · {{ tallyText(note.focus.tally) }}</template></span>
          </template>
          <button v-else type="button" class="btn btn-primary" @click="editing = true; focusError = ''">Make it my focus</button>
        </div>
        <SolvedFold :value="note.solved" :save="saveSolved" />
        <CoachMarks :mark="note.feedback.work_on" label="what to work on" :saving="savingMark === 'work_on'" @change="mark('work_on', $event)" />
      </section>
      <section v-else class="card pad">
        <b>Nothing to work on held up across two conversations.</b>
        <span class="muted"> What it found in one conversation only is under About this note.</span>
      </section>

      <section v-if="body.keep" class="keep" aria-labelledby="k-h">
        <div class="eyebrow-k">Keep doing<span class="count">{{ conversations(body.keep.conversations) }}</span></div>
        <h3 id="k-h">{{ body.keep.theme }}</h3>
        <p class="why">{{ body.keep.why }}</p>
        <CoachMoments :evidence="body.keep.evidence" :playing="playing" :show="2" @play="play" />
        <CoachMarks :mark="note.feedback.keep" label="what to keep" :saving="savingMark === 'keep'" @change="mark('keep', $event)" />
      </section>

      <section v-if="body.also.length" class="also" aria-labelledby="a-h">
        <h3 id="a-h" class="sec">Also seen</h3>
        <details v-for="(a, i) in body.also" :key="a.theme" class="fold">
          <summary><span class="t">{{ a.theme }}</span><span class="muted small nowrap">{{ conversations(a.conversations) }}</span></summary>
          <div class="fold-body">
            <p class="why">{{ a.why }}</p>
            <CoachMoments :evidence="a.evidence" :playing="playing" :show="2" @play="play" />
            <CoachMarks :mark="note.feedback[`also${i}` as CoachPart]" :label="a.theme" :saving="savingMark === `also${i}`"
                        @change="mark(`also${i}` as CoachPart, $event)" />
          </div>
        </details>
      </section>

      <section v-if="body.changed" aria-labelledby="c-h">
        <h3 id="c-h" class="sec">Since the last note</h3>
        <p class="prose">{{ body.changed }}</p>
        <a v-if="older" :href="noteHref(older.id)" class="link">Read the note of {{ day(older.created_at) }}</a>
      </section>
      <section v-if="body.limits" aria-labelledby="l-h">
        <h3 id="l-h" class="sec">What it can't tell yet</h3>
        <p class="prose">{{ body.limits }}</p>
      </section>

      <details class="fold about">
        <summary><span class="t">About this note</span></summary>
        <ul class="fold-body small">
          <li>{{ note.model }}, one call<template v-if="read">: {{ reviews(read.conversations) }}, {{ read.items }} items in them<template
            v-if="read.left_out">; {{ read.left_out }} you marked not right were left out</template></template>.</li>
          <li>Each theme must point at review items from at least two conversations; the moments are those items' quotes, checked
            against the transcript when each review was made.</li>
          <li v-for="d in body.dropped" :key="d.section + d.theme">Dropped ({{ d.reason }}): “{{ d.theme }}”</li>
          <li v-if="body.unknown_evidence.length">{{ body.unknown_evidence.length }} item{{ body.unknown_evidence.length === 1 ? '' : 's' }} it cited
            didn't exist and {{ body.unknown_evidence.length === 1 ? 'was' : 'were' }} ignored.</li>
          <li>What it was sent: the reviews' text and quotes, your marks and notes on them, your focus and its answers, your numbers and
            your principles. Not the audio, not the transcripts. Deleting a conversation, or forgetting someone in it, deletes this note.</li>
        </ul>
      </details>
    </template>

    <section v-if="sum && sum.earlier.length" aria-labelledby="e-h">
      <h3 id="e-h" class="sec">Earlier notes</h3>
      <ul class="earlier">
        <li v-for="e in sum.earlier" :key="e.id">
          <a :href="noteHref(e.id)" class="row" :aria-current="note?.id === e.id ? 'page' : undefined">
            <span class="muted small nowrap">{{ when(e.created_at) }}</span><span>{{ e.theme ?? 'Nothing to work on held up' }}</span>
          </a>
        </li>
      </ul>
    </section>

    <audio ref="audio" preload="none" @timeupdate="onTime" @pause="playing = null" @ended="playing = null" />
    <FocusEditor :open="editing" :initial="work?.focus_text ?? ''" :measure="knowMeasure" :replaces="activeFocus?.text ?? null"
                 :saving="focusSaving" :error="focusError" @save="saveFocus" @cancel="editing = false" />
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 820px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 18px; }
.top { display: flex; align-items: center; gap: 12px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; flex: 1; }
h2 { margin: 0; font-size: 21px; font-weight: 600; line-height: 1.35; }
h3 { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.4; }
p { margin: 0; }
.lead { color: var(--ink-2); }
.pad { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.status { flex-direction: row; align-items: center; gap: 14px; }
.empty { gap: 12px; }
.meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.work { border-left: 3px solid var(--accent); padding: 4px 0 4px 18px; display: flex; flex-direction: column; gap: 12px; }
.keep { border-left: 3px solid var(--line-strong); padding: 2px 0 2px 18px; display: flex; flex-direction: column; gap: 10px; }
.eyebrow-c, .eyebrow-k { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.eyebrow-c { color: var(--accent); }
.eyebrow-k { color: var(--ink-2); }
.count { font-weight: 400; color: var(--muted); }
.why { color: var(--ink-2); }
.try, .know { border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.try { background: var(--accent-soft); }
.try p { font-size: 16px; font-weight: 500; color: var(--ink); }
.know { background: var(--quote); border: 1px solid var(--line); }
.label { font-size: 12px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; color: var(--muted); }
.try .label { color: var(--accent); }
.num { color: var(--ink-2); }
.num a { color: var(--accent); margin-left: 4px; }
.acts { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.badge { font-size: 12px; font-weight: 500; color: var(--accent); background: var(--accent-soft); padding: 2px 9px; border-radius: 999px; }
.sec { font-size: 15px; color: var(--ink); margin-bottom: 6px; }
.prose { color: var(--ink-2); }
.fold { border-top: 1px solid var(--line); }
.fold:last-of-type { border-bottom: 1px solid var(--line); }
.fold summary { display: flex; align-items: center; gap: 12px; padding: 12px 2px; cursor: pointer; list-style: none; min-height: 44px; }
.fold summary::-webkit-details-marker { display: none; }
.fold summary::before { content: '›'; color: var(--muted); font-size: 18px; transition: transform 0.15s; width: 10px; }
.fold[open] summary::before { transform: rotate(90deg); }
.fold .t { flex: 1; font-weight: 500; }
.fold-body { padding: 0 2px 14px 22px; display: flex; flex-direction: column; gap: 10px; margin: 0; }
ul.fold-body { list-style: disc; padding-left: 40px; color: var(--ink-2); gap: 6px; }
.about summary .t { font-weight: 400; color: var(--muted); }
.earlier { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.row { width: 100%; display: flex; gap: 14px; align-items: baseline; text-align: left; border: 0; border-top: 1px solid var(--line); background: none;
  color: var(--ink); font: inherit; padding: 10px 2px; min-height: 44px; cursor: pointer; }
.row:hover, .row[aria-current='page'] { background: var(--quote); }
.step { display: flex; gap: 16px; }
a.link { text-decoration: none; display: inline-flex; align-items: center; }
a.row { text-decoration: none; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; min-height: 36px; }
.dots { display: inline-flex; gap: 4px; }
.dots i { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); opacity: 0.3; animation: pulse 1.2s infinite ease-in-out; }
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes pulse { 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .dots i { animation: none; opacity: 0.7; } }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.nowrap { white-space: nowrap; }
.error { color: var(--fail); }
@media (max-width: 640px) {
  .page { padding: 14px 16px 40px; }
  h2 { font-size: 19px; }
  .work, .keep { padding-left: 14px; }
  .acts .btn { width: 100%; justify-content: center; }
}
</style>
