<script setup lang="ts">
// Fillers, in one place (23 Oct, design review): your list and where each is cued, the recorder's cue (B20, B22,
// rec-27), training a cue for any filler (B29), the phone's and the Mac's live cue (B21), and the check by ear. The trend
// stays on Progress. On a phone only the list (PHONE_ROUTES): the recorder's cue and training are the Mac's.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import FillerTargets from '../components/FillerTargets.vue'
import { lab } from '../lab/api'
import { leansLine, alsoLine, type Target } from '../lab/filler'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { FillersSummary } from '../lab/types'

/** The filler cue (B20, rec-24), as the recorder last said: nulls until it's been seen on firmware that has it. */
interface Filler { model: boolean | null; on: boolean | null; threshold: number | null; ready: boolean | null; count: number | null; loop?: Loop
  /** rec-27 (B29): takes models as data; the filler it runs; the one Undo puts back; one it went back from at boot */
  models?: boolean; name?: string | null; before?: string | null; reverted?: string | null; recording?: boolean }
/** The loop (B22, octo/filler_loop.py): the live cue labelled by the transcript, the weekly retrain, a better one waiting. */
interface Bench { conversations: number; before_mean: number; now_mean: number; better: boolean }
interface Loop {
  live: { days: number; conversations: number; cues: number; right: number; his: number; caught: number; waiting: number }
  train: { started_at: number; running: boolean; error?: string; bench?: Bench; candidate?: boolean } | null
  install: { running: boolean; error?: string; threshold?: number; finished_at?: number; target?: string; undone?: boolean; undo?: boolean } | null
  candidate: { made_at: number; threshold: number; line: string } | null
  /** B29: the filler on the recorder, the training job running now, and every filler that could be trained. */
  target?: string
  job?: { target: string; started_at: number } | null
  targets?: Target[]
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}

const f = ref<FillersSummary | null>(null)
const filler = ref<Filler | null>(null)
const liveCue = ref<{ mac: boolean; phrases: string[] } | null>(null)
const busy = ref(false)
const error = ref('')
const note = ref('')
async function act(fn: () => Promise<void>, done: string) {
  busy.value = true
  error.value = ''
  note.value = ''
  try { await fn(); note.value = done } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}

let poll: ReturnType<typeof setTimeout> | undefined
const loadFiller = () => call<Filler>('GET', '/api/octo/settings/recorder/filler').then((r) => {
  filler.value = r
  clearTimeout(poll)
  if (r.loop?.install?.running || r.loop?.job) poll = setTimeout(loadFiller, 5000)  // an update or a training takes minutes
}).catch(() => {})
onUnmounted(() => clearTimeout(poll))
onMounted(() => {
  lab.fillers().then((r) => { f.value = r }).catch((e) => { error.value = e instanceof Error ? e.message : String(e) })
  if (!onMac) return
  loadFiller()
  call<{ mac: boolean; phrases: string[] }>('GET', '/api/octo/fillers/live').then((c) => { liveCue.value = c }).catch(() => {})
})

// ---- your list ---------------------------------------------------------------------------------
const typed = ref('')
const word = (phrase: string, on: boolean, source: 'typed' | 'suggested' = 'typed') => act(async () => {
  f.value = await lab.fillerWord(phrase, on, source)
  if (source === 'typed' && on) typed.value = ''
  if (onMac) await loadFiller()  // the training rows follow the list
}, '')
const targets = computed(() => filler.value?.loop?.targets ?? [])

// ---- the recorder ------------------------------------------------------------------------------
const cue = computed(() => filler.value?.loop?.target ?? filler.value?.name ?? 'you know')
const live = computed(() => filler.value?.loop?.live)
const day = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
const setFiller = (on: boolean) => act(async () => {
  filler.value = await call<Filler>('POST', '/api/octo/settings/recorder/filler', { on })
  await loadFiller()
}, on ? `The cue is on: the recorder shows “${cue.value}” while it records.` : 'The cue on the recorder is off.')
const install = () => act(async () => { await call('POST', '/api/octo/settings/recorder/filler/install', {}); await loadFiller() }, '')
const undo = () => act(async () => { await call('POST', '/api/octo/settings/recorder/filler/undo', {}); await loadFiller() }, '')
/** Where a filler is cued: the recorder runs one trained filler; the phone and the Mac hear the whole list. */
const onRecorder = (t: Target) => t.target === cue.value && !!filler.value?.model

// ---- the phone and the Mac --------------------------------------------------------------------
const setLiveCue = (mac: boolean) => act(async () => { liveCue.value = await call('POST', '/api/octo/fillers/live', { mac }) }, '')
</script>

<template>
  <main class="page">
    <div>
      <h1>Fillers</h1>
      <p class="muted">Your filler words, and where Octo shows them to you as you say them. How often you say them, over time,
        is on <a :href="href.progress">Progress</a>.</p>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <p v-else-if="note" class="ok small" aria-live="polite">{{ note }}</p>

    <!-- 1. the list -->
    <section class="card pad" aria-labelledby="s-list">
      <h2 id="s-list">Your fillers</h2>
      <p class="muted small">The recorder shows the one filler it was trained for; the phone and the Mac show every one on
        your list.</p>
      <p v-if="!f" class="muted loading" role="status">Loading…</p>
      <template v-else>
        <ul v-if="targets.length" class="rows">
          <li v-for="t in targets" :key="t.target">
            <div class="what">
              <span class="name">{{ t.target }}</span>
              <span v-if="alsoLine(t)" class="muted small">{{ alsoLine(t) }}</span>
              <span class="muted small">{{ t.his }} example{{ t.his === 1 ? '' : 's' }} in your recorder conversations<template
                v-if="leansLine(t)"> · {{ leansLine(t) }}</template></span>
            </div>
            <span class="tags small">
              <span v-if="onRecorder(t)" class="tag on">On the recorder</span>
              <span class="tag">Phone &amp; Mac</span>
            </span>
          </li>
        </ul>
        <p v-else-if="f.common.length" class="small">Most often: {{ f.common.map((c) => `${c.phrase} ×${c.count}`).join(' · ') }}</p>
        <div class="row-head"><h3>Change the list</h3>
          <span class="muted small">Changing it recounts every conversation</span></div>
        <ul class="chips" aria-label="Your filler words">
          <li v-for="p in f.phrases" :key="p" class="chip">{{ p }}
            <button type="button" class="x" :aria-label="`Remove ${p}`" :disabled="busy" @click="word(p, false)">×</button></li>
          <li class="add">
            <form @submit.prevent="typed.trim() && word(typed, true)">
              <input v-model="typed" maxlength="40" placeholder="Add a word or phrase" aria-label="Add a filler word or phrase">
              <button type="submit" class="link small" :disabled="busy || !typed.trim()">Add</button>
            </form>
          </li>
        </ul>
        <details v-if="f.suggestions.lean_on.length || f.suggestions.turn_starts.length" class="more small">
          <summary>Words you lean on more than the people you talk with</summary>
          <ul class="lean">
            <li v-for="s in f.suggestions.lean_on" :key="s.phrase">
              <b>{{ s.phrase }}</b> · {{ s.rate.toFixed(1) }} per 100 of your words, {{ s.others.toFixed(1) }} for the others
              <button type="button" class="link small" :disabled="busy" @click="word(s.phrase, true, 'suggested')">Count it</button>
            </li>
            <li v-for="s in f.suggestions.turn_starts" :key="`start-${s.word}`">
              “{{ s.word[0].toUpperCase() + s.word.slice(1) }}…” starts {{ Math.round(s.share * 100) }}% of your turns
              ({{ Math.round(s.others * 100) }}% of theirs)
            </li>
          </ul>
          <p class="muted">Suggestions only: some of these carry meaning when you use them. Your list, your call.</p>
        </details>
      </template>
    </section>

    <!-- 2. the recorder -->
    <section v-if="onMac" class="card pad" aria-labelledby="s-rec">
      <h2 id="s-rec">On the recorder</h2>
      <p class="muted small">While it records, the recorder listens for one filler and shows it on its screen, with a count for
        the recording. It's spotted on the recorder by a small model trained on your own voice; nothing more leaves it. Each
        one is marked on the conversation's transcript.</p>
      <p v-if="!filler || filler.model === null" class="muted small">The recorder hasn't been seen with firmware that has it yet.</p>
      <p v-else-if="!filler.model" class="muted small">This recorder's firmware has no filler model.</p>
      <template v-else>
        <label class="check"><input type="checkbox" :checked="!!filler.on" :disabled="busy"
               @change="setFiller(($event.target as HTMLInputElement).checked)"> Show the cue for “{{ cue }}” on the recorder</label>
        <p v-if="filler.on && filler.ready === false" class="muted small">It isn't running: the recorder had no memory for it.</p>
        <template v-if="filler.loop && live">
          <p v-if="live.cues || live.his" class="small">Last {{ live.days }} days: right {{ live.right }} of {{ live.cues }}
            flashes · caught {{ live.caught }} of {{ live.his }} of yours<span class="muted"> (checked against the transcripts,
            while it was on)<template v-if="live.waiting">; {{ live.waiting }} conversation{{ live.waiting === 1 ? '' : 's' }}
            not transcribed yet</template>.</span></p>
          <p v-else class="muted small">No conversations with it on in the last {{ live.days }} days to check yet.</p>
          <div v-if="filler.loop.candidate" class="better">
            <p class="small"><strong>A better cue is ready</strong>, retrained {{ day(filler.loop.candidate.made_at) }}.</p>
            <details class="more small">
              <summary>How it did in testing</summary>
              <p class="muted">In testing it {{ filler.loop.candidate.line }}.</p>
            </details>
            <p v-if="filler.models" class="muted small">Installing sends it to the recorder over the Wi-Fi (seconds; recording
              has to be off). The one on it now stays there, to put back.</p>
            <p v-else class="muted small">Installing builds it into the recorder's firmware and updates it over the Wi-Fi (a few
              minutes; recording has to be off).</p>
            <button type="button" class="btn btn-small" :disabled="busy || !!filler.loop.install?.running" @click="install">
              {{ filler.loop.install?.running ? 'Installing…' : 'Install it' }}</button>
          </div>
          <p v-if="filler.loop.install?.running" class="muted small" aria-live="polite">Installing: the recorder's screen
            shows how far.</p>
          <p v-else-if="filler.loop.install?.error" class="error small" role="alert">{{ filler.loop.install.error }}</p>
          <p v-else-if="filler.loop.install?.finished_at" class="ok small">{{ filler.loop.install.undone ? 'Put back' : 'Installed' }}
            {{ day(filler.loop.install.finished_at) }}: the recorder runs {{ filler.loop.install.undone ? 'the one before' : 'the new cue' }}.</p>
          <p v-if="filler.reverted" class="error small" role="alert">The recorder couldn't run the cue for “{{ filler.reverted }}”
            and went back to the one before, with the cue off.</p>
          <p v-if="filler.before" class="line small">
            <button type="button" class="link" :disabled="busy || !!filler.recording || !!filler.loop.install?.running || !!filler.loop.job"
                    @click="undo">Undo: put “{{ filler.before }}” back</button>
            <span v-if="filler.recording" class="muted">Stop recording first: hold the recorder's button 1 s.</span>
          </p>
        </template>
      </template>
    </section>

    <!-- 3. training -->
    <section v-if="onMac && filler?.loop?.targets" class="card pad" aria-labelledby="s-train">
      <h2 id="s-train">Train</h2>
      <p class="muted small">Octo trains a cue for a filler on your own voice, from recorder conversations, and offers it for the
        recorder if it does well enough. One trains at a time. Examples are counted from the transcript (only “you know” has
        been checked by ear).</p>
      <FillerTargets :targets="filler.loop.targets" :job="filler.loop.job ?? null" :installed="cue" :takes="!!filler.models"
                     :recording="!!filler.recording" :installing="!!filler.loop.install?.running" @changed="loadFiller" />
      <p v-if="filler.loop.train && (filler.loop.train.running || filler.loop.train.error || !filler.loop.train.candidate)"
         class="muted small">
        <template v-if="filler.loop.train.running">Retraining “{{ cue }}” now on every conversation so far (about an hour).</template>
        <template v-else-if="filler.loop.train.error">The last weekly retrain ({{ day(filler.loop.train.started_at) }}) failed:
          {{ filler.loop.train.error }}</template>
        <template v-else-if="filler.loop.train.bench && !filler.loop.train.candidate">Retrained “{{ cue }}” {{
          day(filler.loop.train.started_at) }}: not better yet (caught {{ Math.round(filler.loop.train.bench.now_mean * 100) }}%
          in testing against {{ Math.round(filler.loop.train.bench.before_mean * 100) }}% now). It tries again each week as
          conversations come in.</template>
      </p>
      <p v-else class="muted small">Each week, at night, the lab retrains “{{ cue }}” on every conversation so far and offers it
        above if it does better.</p>
    </section>

    <!-- 4. the phone and the Mac -->
    <section class="card pad" aria-labelledby="s-live">
      <h2 id="s-live">On the phone and the Mac</h2>
      <p class="muted small">These hear every filler on your list, with no training: Apple's own speech recognition, on the
        device. What they hear is only counted, never kept or sent; the lab's transcript stays the record.</p>
      <label v-if="onMac" class="check"><input type="checkbox" :checked="!!liveCue?.mac" :disabled="!liveCue || busy"
             @change="setLiveCue(($event.target as HTMLInputElement).checked)"> Show my fillers in the menu bar while a call
        records</label>
      <p v-if="onMac" class="muted small">Your mic only, on calls this Mac records (<a :href="href.mac">This Mac</a> → Calls). macOS 26 or later.</p>
      <p class="muted small">On the phone: Octo app → Memos → “Show my fillers while recording”. The switch is kept on the
        phone.</p>
    </section>

    <!-- 5. the check by ear -->
    <section class="card pad" aria-labelledby="s-ear">
      <h2 id="s-ear">Check by ear</h2>
      <p class="small"><template v-if="f?.checks.checks">Done {{ f.checks.checks }} time{{ f.checks.checks === 1 ? '' : 's' }}:
        you heard {{ f.checks.by_ear }} fillers, the transcript kept {{ f.checks.by_app }} in the same stretches.</template>
        <template v-else>Not done yet. Until it is, the trend on Progress says it isn't checked.</template></p>
      <p class="muted small">Listen to about three minutes of one conversation and tap for each filler you say; Octo shows its
        count beside yours, so you know how much of them the transcript keeps.</p>
      <a v-if="onMac" class="link" :href="href.fillerCheck">Count your fillers by ear →</a>
      <p v-else class="muted small">On your Mac.</p>
    </section>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
h1 { margin: 4px 0 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h3 { margin: 6px 0 0; font-size: 14px; font-weight: 600; }
p { margin: 0; }
a { color: var(--accent); }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 10px; }
.row-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.check { display: flex; gap: 8px; align-items: center; font-size: 14px; min-height: 32px; }
.rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.rows li { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px 12px; padding: 8px 0; border-top: 1px solid var(--line); }
.rows li:first-child { border-top: 0; }
.what { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 260px; }
.name { font-weight: 600; font-size: 14px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; }
.tag { padding: 2px 8px; border: 1px solid var(--line); border-radius: 999px; color: var(--ink-2); }
.tag.on { border-color: var(--accent); color: var(--accent); }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 2px; padding: 2px 4px 2px 12px; border: 1px solid var(--line-strong); border-radius: 999px; font-size: 14px; }
.x { border: 0; background: none; color: var(--muted); cursor: pointer; width: 28px; height: 28px; border-radius: 50%; font-size: 16px; line-height: 1; }
.x:hover { background: var(--quote); color: var(--ink); }
.add form { display: flex; align-items: center; gap: 6px; }
.add input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; width: 190px; min-height: 32px; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: none; min-height: 32px; }
.link:disabled { color: var(--muted); cursor: default; }
a.link { display: inline-flex; align-items: center; align-self: flex-start; font-size: 14px; }
.better { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; }
.more summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; gap: 8px; color: var(--accent); list-style: none; }
.more summary::-webkit-details-marker { display: none; }
.more summary::before { content: ''; width: 6px; height: 6px; border-right: 1.6px solid currentColor; border-bottom: 1.6px solid currentColor;
  transform: rotate(-45deg); transition: transform 0.15s; margin: 0 2px; }
.more[open] summary::before { transform: rotate(45deg); }
.lean { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
@media (max-width: 760px) {
  .page { padding: 16px 16px 40px; }
  .pad { padding: 16px; }
  .x { min-height: 44px; min-width: 44px; }
  .add input { width: 150px; min-height: 44px; }
  .link, .more summary, .check { min-height: 44px; }
}
</style>
