<script setup lang="ts">
// The filler check by ear (S0 in the coaching plan, docs/coach-plan.md §4.2): the owner listens to about
// three minutes of one conversation and taps for each filler he hears himself say; then the app's
// count for the same stretch is shown beside his, with what it found underlined. It says how much of
// his fillers the transcription keeps, so the trend on Progress means what it says.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { mss, pieces, when } from '../lab/format'
import { href } from '../lab/router'
import type { FillerCheckResult, FillerCheckStart } from '../lab/types'

const start = ref<FillerCheckStart | null>(null)
const error = ref('')
const chosen = ref('')
async function load(sessionId?: string) {
  try {
    start.value = await lab.fillerCheck(sessionId)
    chosen.value = start.value.session_id ?? ''
    error.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  reset()
}
onMounted(() => { void load(); window.addEventListener('keydown', onKey) })
onUnmounted(() => window.removeEventListener('keydown', onKey))

const w = computed(() => start.value?.window ?? null)
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const t = ref(0)
const taps = ref<number[]>([])
const result = ref<FillerCheckResult | null>(null)
const saving = ref(false)
function reset() { taps.value = []; result.value = null; t.value = w.value?.start ?? 0; audio.value?.pause() }
function play() {
  const a = audio.value
  if (!a || !w.value) return
  if (!a.paused) { a.pause(); return }
  if (a.currentTime < w.value.start || a.currentTime >= w.value.end) a.currentTime = w.value.start
  void a.play()
}
function onTime() {
  const a = audio.value
  if (!a || !w.value) return
  t.value = a.currentTime
  if (a.currentTime >= w.value.end) a.pause()
}
function tap() { if (!result.value) taps.value = [...taps.value, t.value] }
function onKey(e: KeyboardEvent) {
  const el = e.target instanceof Element ? e.target : null
  if (el?.closest('input, textarea, select')) return
  if ((e.key === 'f' || e.key === 'F') && !e.metaKey && !e.ctrlKey) { e.preventDefault(); tap() }
}
async function done() {
  if (!w.value || !chosen.value) return
  audio.value?.pause()
  saving.value = true
  try { result.value = await lab.saveFillerCheck(chosen.value, w.value.start, w.value.end, taps.value.length); error.value = '' }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { saving.value = false }
}
const progress = computed(() => (w.value ? Math.min(1, Math.max(0, (t.value - w.value.start) / (w.value.end - w.value.start))) : 0))
const summary = computed(() => result.value?.summary ?? start.value?.summary ?? null)
/** What the two counts say about the line on Progress. */
const verdict = computed(() => {
  const r = result.value
  if (!r) return ''
  const said = `The app counted ${r.by_app} where you heard ${r.by_ear}`
  if (!r.by_ear) return r.by_app ? `${said}. Look at what it underlined: if those are words you meant, take them off your list.` : 'Neither of you counted any: try a stretch where you talk more.'
  const ratio = r.by_app / r.by_ear
  if (ratio < 0.7) return `${said}: much of it never reaches the transcript, so the line on Progress runs low. Compare it with itself, not with a number from elsewhere.`
  if (ratio > 1.3) return `${said}. Look at what it underlined: if it counts words you meant (a real "you know"), take them off your list; if they're fillers you missed while tapping, the line is fine.`
  return `${said}: close, so the line on Progress is a fair measure of what you say.`
})
</script>

<template>
  <main class="page">
    <a :href="href.fillers" class="back">← Fillers</a>
    <h1>Filler check</h1>
    <p class="lead">Count your own fillers by ear on about three minutes of one conversation, then see what the app
      counted in the same stretch. It shows how many of your fillers the transcription keeps, so the line on Progress
      means what it says. About five minutes; once or twice is enough.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!start && !error" class="muted loading" role="status">Loading…</p>
    <section v-else-if="start && !start.candidates.length" class="card pad">
      <p>Nothing to check yet: it needs an English conversation on the line whose audio is still kept.</p>
    </section>
    <template v-else-if="start">
      <section class="card pad">
        <label class="pick">Conversation
          <select v-model="chosen" :disabled="taps.length > 0 && !result" @change="load(chosen)">
            <option v-for="c in start.candidates" :key="c.session_id" :value="c.session_id">
              {{ when(c.at) }}{{ c.with.length ? ` · with ${c.with.join(', ')}` : '' }} · {{ c.words }} of your words</option>
          </select>
        </label>
        <p v-if="w" class="muted small">The stretch: {{ mss(w.start) }} to {{ mss(w.end) }}, with {{ mss(w.jay_s) }} of you talking.</p>
        <audio v-if="chosen" ref="audio" :src="lab.audioUrl(chosen)" preload="metadata"
               @play="playing = true" @pause="playing = false" @timeupdate="onTime" />
      </section>

      <section v-if="w && !result" class="card pad listen" aria-labelledby="l-h">
        <h2 id="l-h">1 · Listen and tap</h2>
        <p class="small muted">Tap for each filler <b>you</b> say: um, uh, you know, kind of, or anything you'd rather say
          less. Not the other people's. Press F on the keyboard if that's easier.</p>
        <div class="player">
          <button type="button" class="btn btn-primary" @click="play">{{ playing ? 'Pause' : t > (w.start + 0.5) ? 'Play on' : 'Play the stretch' }}</button>
          <div class="bar" aria-hidden="true"><div :style="{ width: `${progress * 100}%` }" /></div>
          <span class="mono small muted">{{ mss(Math.max(0, t - w.start)) }} / {{ mss(w.end - w.start) }}</span>
        </div>
        <button type="button" class="tapper" @click="tap">Filler <span class="count">{{ taps.length }}</span></button>
        <div class="row">
          <button type="button" class="link" :disabled="!taps.length" @click="taps = taps.slice(0, -1)">Undo the last tap</button>
          <span class="grow" />
          <button type="button" class="btn" :disabled="saving || progress < 0.98" :title="progress < 0.98 ? 'Listen to the whole stretch first' : undefined"
                  @click="done">{{ saving ? 'Saving…' : 'Done: compare with the app' }}</button>
        </div>
      </section>

      <section v-if="result" class="card pad" aria-labelledby="c-h">
        <h2 id="c-h">2 · You and the app</h2>
        <div class="vs">
          <div><span class="big">{{ result.by_ear }}</span><span class="small muted">by ear</span></div>
          <div><span class="big">{{ result.by_app }}</span><span class="small muted">counted by the app, in {{ result.words }} of your words</span></div>
        </div>
        <p class="small">{{ verdict }}</p>
        <h3>Your lines in the stretch, with what the app counted underlined</h3>
        <ul class="lines">
          <li v-for="ln in result.lines" :key="ln.id"><span class="mono small muted">{{ mss(ln.start) }}</span>
            <span><template v-for="(pc, k) in pieces(ln.text, ln.spans)" :key="k"><mark v-if="pc.on" class="f">{{ pc.t }}</mark><template v-else>{{ pc.t }}</template></template></span></li>
        </ul>
        <div class="row"><button type="button" class="link" @click="reset">Check another stretch</button>
          <span class="grow" /><a class="link" :href="href.fillers">Back to Fillers</a></div>
      </section>

      <p v-if="summary?.checks" class="muted small">All checks so far: {{ summary.checks }} · by ear {{ summary.by_ear }}, the app
        {{ summary.by_app }}<template v-if="summary.share !== null"> ({{ Math.round(summary.share * 100) }}%)</template>.</p>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 20px 24px 48px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h3 { margin: 6px 0 0; font-size: 14px; font-weight: 600; }
p { margin: 0; }
.lead { color: var(--ink-2); }
.pad { padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; }
.pick { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
select { font: inherit; font-size: 15px; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; min-height: 44px; }
.player { display: flex; align-items: center; gap: 14px; }
.bar { flex: 1; height: 6px; border-radius: 3px; background: var(--chip); overflow: hidden; }
.bar > div { height: 6px; background: var(--accent); }
.tapper { width: 100%; min-height: 120px; border-radius: 16px; border: 2px solid var(--accent); background: var(--accent-soft); color: var(--accent);
  font: inherit; font-size: 22px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 16px;
  touch-action: manipulation; user-select: none; }
.tapper:active { background: var(--accent); color: var(--accent-ink); }
.count { font-size: 34px; font-variant-numeric: tabular-nums; }
.row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.grow { flex: 1; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: none; min-height: 40px; }
.vs { display: flex; gap: 40px; }
.vs > div { display: flex; flex-direction: column; }
.big { font-size: 34px; font-weight: 600; }
.lines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.lines li { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 10px; line-height: 1.5; }
mark.f { background: none; color: inherit; text-decoration: underline 2px var(--accent); text-underline-offset: 3px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) { .page { padding: 14px 16px 40px; } .player { flex-wrap: wrap; } }
</style>
