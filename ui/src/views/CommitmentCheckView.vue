<script setup lang="ts">
// The commitments check (B14, 21 Oct, docs/backlog.md): every promise the step found, oldest first, each to hear,
// then one tap: right, partly (the wrong person, date or wording) or wrong (not a promise). The counts say whether
// the step can be trusted; the first ten are the ones asked for. Nothing here changes the commitments themselves.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { mss, when } from '../lab/format'
import { href } from '../lab/router'
import type { Commitment, CommitmentChecks, CommitmentVerdict } from '../lab/types'

const r = ref<CommitmentChecks | null>(null)
const error = ref('')
onMounted(async () => {
  try { r.value = await lab.commitmentCheck() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
})
onUnmounted(() => audio.value?.pause())

const VERDICT: Record<CommitmentVerdict, string> = { right: 'Right', partly: 'Partly: person, date or wording off', wrong: 'Wrong: not a promise' }
const firstDone = computed(() => (r.value?.items ?? []).slice(0, r.value?.first ?? 10).filter((c) => c.checked).length)

// One player; each plays its quote from just before it to 2 s after.
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref<string | null>(null)
let stopAt = 0
function play(c: Commitment) {
  const a = audio.value
  if (!a) return
  if (playing.value === c.id && !a.paused) { a.pause(); return }
  const src = lab.audioUrl(c.session_id)
  if (!a.src.endsWith(src)) { a.src = src; a.load() }
  stopAt = c.end + 2
  playing.value = c.id
  const go = () => { a.currentTime = Math.max(0, c.start - 0.3); void a.play() }
  if (a.readyState >= 1) go(); else a.addEventListener('loadedmetadata', go, { once: true })
}
function onTime() { const a = audio.value; if (a && a.currentTime >= stopAt) a.pause() }

const saving = ref<string | null>(null)
async function mark(c: Commitment, v: CommitmentVerdict) {
  saving.value = c.id
  try {
    r.value = await lab.judgeCommitment(c.id, c.checked === v ? null : v)  // the same tap again takes it back
    error.value = ''
  } catch (e) { error.value = `Couldn't save that: ${e instanceof Error ? e.message : e}` } finally { saving.value = null }
}
</script>

<template>
  <main class="page">
    <a :href="href.progress" class="back">← Progress</a>
    <h1>Commitments, checked</h1>
    <p class="lead">The promises the lab found, oldest first. Hear each and say whether it got it right: a real
      promise, by the right person, to the right person. The first {{ r?.first ?? 10 }} are enough to tell.</p>
    <audio ref="audio" preload="none" @pause="playing = null" @ended="playing = null" @timeupdate="onTime" />
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="r === null && !error" class="muted loading" role="status">Loading…</p>
    <p v-else-if="r && !r.items.length" class="muted">No commitments found yet.</p>

    <section v-if="r?.checked" class="card pad" aria-labelledby="sofar">
      <h2 id="sofar">So far · {{ r.checked }} checked</h2>
      <p>{{ r.counts.right }} right · {{ r.counts.partly }} partly · {{ r.counts.wrong }} wrong</p>
    </section>

    <p v-if="r?.items.length" class="muted small">{{ firstDone }} of the first {{ Math.min(r.first, r.items.length) }} checked
      · {{ r.items.length }} in all.</p>
    <ol v-if="r?.items.length" class="qs">
      <li v-for="c in r.items" :key="c.id" class="card q" :class="{ done: c.checked }">
        <div class="meta small muted">{{ when(c.at) }} · {{ mss(c.start) }}<template v-if="c.due"> · due {{ c.due }}</template></div>
        <div><b>{{ c.by }}</b> → {{ c.to }}: {{ c.what }}</div>
        <button type="button" class="say" :aria-pressed="playing === c.id" @click="play(c)">
          <span class="ts">{{ playing === c.id ? '❚❚' : '▶' }}</span>
          <span>“{{ c.quote }}”</span>
        </button>
        <div class="verdicts" role="group" :aria-label="`Did the lab get ${c.mine ? 'your' : `${c.by}'s`} commitment right?`">
          <button v-for="(label, v) in VERDICT" :key="v" type="button" class="btn btn-small" :class="{ on: c.checked === v }"
                  :aria-pressed="c.checked === v" :disabled="saving === c.id" @click="mark(c, v)">{{ label }}</button>
        </div>
      </li>
    </ol>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 20px 24px 48px; display: flex; flex-direction: column; gap: 14px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.lead { color: var(--ink-2); }
.pad { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.qs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.q { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.q.done { opacity: 0.7; }
.say { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 8px; text-align: left; border: 0; background: var(--quote);
  border-radius: 8px; padding: 10px; font: inherit; color: inherit; cursor: pointer; min-height: 44px; }
.ts { color: var(--accent); }
.verdicts { display: flex; gap: 8px; flex-wrap: wrap; }
.verdicts .btn-small { min-height: 44px; }
.verdicts .on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) { .page { padding: 14px 16px 40px; } .verdicts .btn-small { flex: 1 1 100%; } }
</style>
