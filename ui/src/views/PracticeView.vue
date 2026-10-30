<script setup lang="ts">
// Practice (B18, coaching plan C7): today's prompt from the owner's own list, a take recorded here, and the six delivery
// numbers (B10) against the takes before it, then where its point came (one call per take, octo/drills.py). Kept apart
// (K4): a take is never a conversation.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { lab } from '../lab/api'
import { DELIVERY_NAME, deliveryValue, stateWord, usualText } from '../lab/delivery'
import { mss, when } from '../lab/format'
import { cantRecord, lastText, recordType, takesSpread } from '../lab/practice'
import { href, route } from '../lab/router'
import { canDelete } from '../lab/where'
import type { DeliveryMeasure, DrillDetail, DrillsSummary } from '../lab/types'

const props = defineProps<{ take?: string }>()
const page = ref<DrillsSummary | null>(null)
const error = ref('')
const load = () => lab.drills().then((p) => { page.value = p }).catch((e) => { error.value = e instanceof Error ? e.message : String(e) })
onMounted(load)

// Which prompt: today's, or another from the list.
const pick = ref(0)
const prompt = computed(() => {
  const p = page.value
  if (!p || !p.prompts.length) return null
  const start = Math.max(0, p.prompts.indexOf(p.today ?? ''))
  return p.prompts[(start + pick.value) % p.prompts.length]
})

// Recording: the page's MediaRecorder (the phone's Lab tab, a browser on the Mac); not in the Octo app's window.
const inMacApp = !!(window as unknown as { webkit?: { messageHandlers?: { octo?: unknown } } }).webkit?.messageHandlers?.octo
const type = typeof MediaRecorder !== 'undefined' ? recordType((t) => MediaRecorder.isTypeSupported(t)) : null
const why = cantRecord({ inMacApp, mic: !!navigator.mediaDevices?.getUserMedia, type })
const recording = ref(false)
const sending = ref(false)
const elapsed = ref(0)
let rec: MediaRecorder | null = null
let chunks: Blob[] = []
let tick: ReturnType<typeof setInterval> | null = null

async function start() {
  error.value = ''
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    chunks = []
    rec = new MediaRecorder(stream, { mimeType: type! })
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); void send(new Blob(chunks, { type: type! })) }
    rec.start(1000)
    recording.value = true
    elapsed.value = 0
    const t0 = Date.now()
    tick = setInterval(() => { elapsed.value = (Date.now() - t0) / 1000 }, 250)
  } catch (e) {
    error.value = e instanceof Error && e.name === 'NotAllowedError' ? 'The microphone wasn\'t allowed.' : String(e)
  }
}
function stop() {
  if (tick) clearInterval(tick)
  recording.value = false
  rec?.stop()
}
async function send(blob: Blob) {
  sending.value = true
  try {
    const { id } = await lab.addDrill(blob, 'take.m4a', prompt.value!)
    location.hash = href.take(id)
    void load()
  } catch (e) {
    error.value = `The take wasn't saved: ${e instanceof Error ? e.message : String(e)}`
  } finally {
    sending.value = false
  }
}
onUnmounted(() => { if (recording.value) { if (tick) clearInterval(tick); rec?.stream.getTracks().forEach((t) => t.stop()) } })

// One take: asked again every 3 s until the lab has heard it.
const take = ref<DrillDetail | null>(null)
const open = ref<DeliveryMeasure | null>(null)
let poll: ReturnType<typeof setTimeout> | null = null
async function loadTake(id: string) {
  if (poll) clearTimeout(poll)
  try {
    take.value = await lab.drill(id)
    if (take.value.status === 'queued') poll = setTimeout(() => loadTake(id), 3000)
    else {
      if (take.value.point?.state === 'waiting') poll = setTimeout(() => loadTake(id), 5000)  // its call takes ~30 s
      void load()
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
watch(() => props.take, (id) => { take.value = null; open.value = null; if (id) void loadTake(id) }, { immediate: true })
onUnmounted(() => { if (poll) clearTimeout(poll) })
const again = () => { location.hash = href.practice }
const player = ref<HTMLAudioElement | null>(null)
function fromPoint(at: number) {
  const a = player.value
  if (!a) return
  const go = () => { a.currentTime = Math.max(0, at - 0.3); void a.play() }
  if (a.readyState >= 1) go(); else { a.addEventListener('loadedmetadata', go, { once: true }); a.load() }
}

async function remove(id: string) {
  if (!confirm('Delete this take? Its audio and numbers go for good.')) return
  try {
    await lab.deleteDrill(id)
    if (route.current.name === 'practice' && route.current.take === id) again()
    void load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

// The prompt list, edited in place like the filler list.
const adding = ref('')
const listOpen = ref(false)
async function setPrompt(text: string, on: boolean) {
  try {
    page.value = await lab.drillPrompt(text, on)
    adding.value = ''
    pick.value = 0
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <main class="page">
    <h1>Practice</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!page && !error" class="muted loading" role="status">Loading…</p>
    <template v-else-if="page">
      <section v-if="take" class="card pad" aria-labelledby="take-h">
        <div class="head">
          <h2 id="take-h">{{ take.prompt }}</h2>
          <span class="muted small">{{ when(take.created_at) }} · {{ mss(take.duration_s) }}</span>
        </div>
        <audio v-if="take.audio" ref="player" class="replay" controls preload="none" :src="`/api/octo/drills/${take.id}/audio`" />
        <p v-else class="muted small">The audio went after 60 days; the numbers stay.</p>
        <p v-if="take.status === 'queued'" class="muted" role="status">Listening to it… the lab hears a take as soon as the step it's on finishes.</p>
        <p v-else-if="take.status === 'failed'" class="error">It couldn't be heard: {{ take.error }}</p>
        <template v-else>
          <!-- What a take shows: the six, the focus's first, then where the point came. -->
          <ul class="rows" aria-label="What a take shows">
            <li v-for="r in take.rows" :key="r.measure" :class="{ open: open === r.measure, lead: r.measure === take.lead }">
              <button type="button" class="row" :aria-expanded="open === r.measure" @click="open = open === r.measure ? null : r.measure">
                <span class="name">{{ DELIVERY_NAME[r.measure] }}<span v-if="r.measure === take.lead" class="muted small"> · your focus</span></span>
                <span class="val">{{ deliveryValue(r.measure, r.value) }}</span>
                <span class="usual muted">{{ usualText(r) || lastText(r) }}</span>
                <span class="state">{{ stateWord(r).replace('than usual', 'than your usual') }}</span>
                <span class="chev" aria-hidden="true">›</span>
              </button>
              <div v-if="open === r.measure" class="detail">
                <p class="small">{{ takesSpread(r, take.min_words, take.words) }}</p>
                <p v-if="r.last != null && usualText(r)" class="small muted">{{ lastText(r) }}</p>
              </div>
            </li>
          </ul>
          <section v-if="take.point" class="point" aria-labelledby="point-h">
            <h3 id="point-h">Where the point came</h3>
            <p v-if="take.point.state === 'waiting'" class="muted small" role="status">Finding it…</p>
            <p v-else-if="take.point.state === 'off'" class="muted small">Choose a model for reviews on the Pipeline page to see this.</p>
            <p v-else-if="take.point.state === 'skipped'" class="muted small">Too short to tell ({{ take.point.why }}).</p>
            <p v-else-if="take.point.state === 'failed'" class="error small">It couldn't be found: {{ take.point.error }}</p>
            <template v-else-if="take.point.state === 'done'">
              <template v-if="take.point.quote">
                <p class="quote">“{{ take.point.quote.text }}”</p>
                <p class="small">The point came <b>{{ Math.round(take.point.quote.at) }} s</b> into a {{ Math.round(take.duration_s) }} s take<span
                  v-if="!take.point.quote.measured" class="muted"> (where its line starts: the words' times weren't found)</span>.
                  <button v-if="take.audio" type="button" class="btn btn-small" @click="fromPoint(take.point.quote.at)">▶ From the point</button></p>
              </template>
              <p v-else class="muted small">Its quote didn't match what the lab heard, so there's no time for it.</p>
              <p class="small"><b>In {{ take.point.short_words }} words:</b> {{ take.point.short }}</p>
              <p class="small muted">{{ take.point.build_up_right ? 'Building up first was the right call' : 'The point could have come first' }}<template
                v-if="take.point.build_up_why">: {{ take.point.build_up_why }}</template></p>
            </template>
          </section>
          <details class="said"><summary class="small">What it heard · {{ take.words }} words</summary><p class="small">{{ take.text }}</p></details>
          <p class="small muted">Counted from the transcript, like a conversation's, against your earlier takes (the pitch memos
            you left out of the counts start the line). A number, not a grade. Where the point came is one call to your
            reviews' model with what it heard here, nothing else.</p>
        </template>
        <div class="actions">
          <button type="button" class="btn btn-primary" @click="again">Try again</button>
          <button v-if="canDelete" type="button" class="btn btn-small" @click="remove(take.id)">Delete this take</button>
        </div>
      </section>

      <section v-else class="card pad" aria-labelledby="today-h">
        <span class="eyebrow">Today's prompt</span>
        <h2 id="today-h" class="prompt">{{ prompt ?? 'No prompts on your list' }}</h2>
        <p v-if="page.lead" class="small muted">Your focus leads: {{ DELIVERY_NAME[page.lead].toLowerCase() }} comes first after each take.</p>
        <div class="rec">
          <span class="timer mono" :class="{ on: recording }" role="timer" aria-live="off">{{ mss(elapsed) }}</span>
          <template v-if="!why">
            <button v-if="!recording" type="button" class="btn btn-primary" :disabled="!prompt || sending" @click="start">
              {{ sending ? 'Saving…' : 'Record' }}</button>
            <button v-else type="button" class="btn btn-danger" @click="stop">Stop</button>
          </template>
          <button v-if="page.prompts.length > 1 && !recording" type="button" class="btn btn-small" @click="pick++">Another prompt</button>
        </div>
        <p v-if="why" class="small muted">{{ why }}</p>
        <details class="list" :open="listOpen" @toggle="listOpen = ($event.target as HTMLDetailsElement).open">
          <summary class="small">Your prompts · {{ page.prompts.length }}</summary>
          <ul class="chips">
            <li v-for="p in page.prompts" :key="p">{{ p }}
              <button type="button" class="x" :aria-label="`Take “${p}” off the list`" @click="setPrompt(p, false)">×</button></li>
          </ul>
          <form class="add" @submit.prevent="setPrompt(adding, true)">
            <input v-model="adding" type="text" maxlength="200" placeholder="Add a prompt in your words" aria-label="Add a prompt">
            <button type="submit" class="btn btn-small" :disabled="!adding.trim()">Add</button>
          </form>
        </details>
      </section>

      <section v-if="page.takes.length" class="card pad" aria-labelledby="takes-h">
        <h2 id="takes-h">Your takes</h2>
        <ul class="takes">
          <li v-for="t in page.takes" :key="t.id">
            <a :href="href.take(t.id)" :aria-current="take?.id === t.id ? 'page' : undefined">{{ when(t.created_at) }}</a>
            <span class="small muted">{{ t.prompt }} · {{ mss(t.duration_s) }}<template v-if="t.words != null"> · {{ t.words }} words</template><template
              v-else-if="t.status === 'queued'"> · being heard</template><template v-else-if="t.status === 'failed'"> · couldn't be heard</template></span>
          </li>
        </ul>
        <p class="small muted">Kept apart from your conversations: never in the list, a review, search or the coach.
          <a :href="href.progress">Their own line is on Progress.</a> The audio goes after 60 days, as all audio.</p>
      </section>
      <section v-if="!take" class="card pad" aria-labelledby="rehearse-h">
        <h2 id="rehearse-h">Rehearse a hard conversation</h2>
        <p class="small muted">Play it through in text before you have it, with the model as the other person, then a review
          against your principles.</p>
        <a class="btn btn-small go" :href="href.rehearse">Rehearse a conversation</a>
      </section>
      <a class="fillers" :href="href.fillers">Your fillers →</a>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 20px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 19px; font-weight: 600; flex-grow: 1; }
.prompt { font-size: 22px; line-height: 1.3; }
.pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 14px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.rec { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.timer { font-size: 32px; font-variant-numeric: tabular-nums; min-width: 4ch; color: var(--muted); }
.timer.on { color: var(--ink); }
.replay { width: 100%; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.rows { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
.rows > li { border-bottom: 1px solid var(--line); }
.row { width: 100%; display: grid; grid-template-columns: 11rem minmax(0, 1fr) 7.5rem 10rem 1rem; align-items: center; gap: 12px;
  border: 0; background: none; color: var(--ink); font: inherit; font-size: 14px; text-align: left; padding: 8px 2px; min-height: 44px; cursor: pointer; }
.row:hover { background: var(--quote); }
.name { font-weight: 500; }
.lead .name { font-weight: 600; }
.val { font-variant-numeric: tabular-nums; }
.state { color: var(--ink-2); }
.chev { color: var(--muted); transition: transform 0.15s; }
.open .chev { transform: rotate(90deg); }
.detail { padding: 2px 2px 12px; display: flex; flex-direction: column; gap: 6px; }
.point { display: flex; flex-direction: column; gap: 6px; }
.point h3 { margin: 0; font-size: 15px; font-weight: 600; }
.point .quote { background: var(--quote); border-radius: 8px; padding: 8px 10px; }
.point .btn-small { margin-left: 6px; }
.said summary, .list summary { cursor: pointer; padding: 4px 0; }
.said p { margin: 6px 0 0; line-height: 1.5; }
.chips { list-style: none; margin: 8px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.chips li { font-size: 14px; border: 1px solid var(--line); border-radius: 10px; padding: 4px 4px 4px 12px; display: flex; align-items: center;
  justify-content: space-between; gap: 6px; }
.x { border: 0; background: none; color: var(--muted); font: inherit; cursor: pointer; min-width: 36px; min-height: 36px; }
.add { display: flex; gap: 6px; }
.add input { flex: 1; font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 9px;
  padding: 4px 10px; min-height: 36px; min-width: 0; }
.takes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.takes li { display: flex; flex-direction: column; gap: 2px; }
.takes a[aria-current='page'] { font-weight: 600; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
p { margin: 0; }
@media (max-width: 640px) {
  .page { padding: 16px 16px 40px; }
  .pad { padding: 18px 16px; }
  .row { grid-template-columns: minmax(0, 1fr) auto 1rem; grid-template-areas: 'name state chev' 'val usual chev'; row-gap: 2px; }
  .name { grid-area: name; } .state { grid-area: state; text-align: right; } .chev { grid-area: chev; }
  .val { grid-area: val; color: var(--ink-2); } .usual { grid-area: usual; text-align: right; }
  .btn { min-height: 44px; }
}
.go { align-self: flex-start; }
.fillers { color: var(--accent); text-decoration: none; font-size: 14px; display: inline-flex; align-items: center; min-height: 44px; align-self: flex-start; }
</style>
