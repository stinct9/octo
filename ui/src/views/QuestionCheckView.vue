<script setup lang="ts">
// The questions check by ear (B4, 13 Oct, docs/backlog.md): the 1:1 questions the app counted, "nobody spoke
// next" first (a third of them, which looked high), each to hear with the 10 seconds after it, then one tap:
// you answered, it went unanswered, or it wasn't really a question. What the owner hears is kept against what the app
// said, so whether "who spoke next" holds up is measured before a closer look at answers is built.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { mss, when } from '../lab/format'
import { href } from '../lab/router'
import type { QuestionChecks, QuestionToCheck, QuestionVerdict } from '../lab/types'

const items = ref<QuestionToCheck[] | null>(null)
const summary = ref<QuestionChecks | null>(null)
const error = ref('')
onMounted(async () => {
  try { const r = await lab.questionCheck(); items.value = r.items; summary.value = r.summary }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
})
onUnmounted(() => audio.value?.pause())

const APP: Record<string, string> = { you: 'You spoke next', other: 'Someone else spoke next', nobody: 'Nobody spoke within 8 s' }
const VERDICT: Record<QuestionVerdict, string> = { answered: 'I answered it', unanswered: 'It went unanswered', not_question: 'Not really a question' }
const HEARD: Record<QuestionVerdict, string> = { answered: 'answered', unanswered: 'unanswered', not_question: 'not a question' }
const left = computed(() => (items.value ?? []).filter((i) => !i.verdict).length)

// One player; each question plays from just before it to 10 s after it (or to the end of the reply).
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref<string | null>(null)
let stopAt = 0
const keyOf = (i: QuestionToCheck) => `${i.session_id}:${i.start}`
function play(i: QuestionToCheck) {
  const a = audio.value
  if (!a) return
  if (playing.value === keyOf(i) && !a.paused) { a.pause(); return }
  const src = lab.audioUrl(i.session_id)
  if (!a.src.endsWith(src)) { a.src = src; a.load() }  // a new conversation's audio: fetch its length first
  stopAt = Math.max(i.end + 10, i.reply_end ?? 0)
  playing.value = keyOf(i)
  const go = () => { a.currentTime = Math.max(0, i.start - 0.3); void a.play() }
  if (a.readyState >= 1) go(); else a.addEventListener('loadedmetadata', go, { once: true })
}
function onTime() { const a = audio.value; if (a && a.currentTime >= stopAt) a.pause() }

const saving = ref<string | null>(null)
async function mark(i: QuestionToCheck, v: QuestionVerdict) {
  saving.value = keyOf(i)
  try {
    const next = i.verdict === v ? null : v  // the same tap again takes it back
    summary.value = await lab.saveQuestionCheck(i.session_id, i.start, next)
    i.verdict = next
    error.value = ''
  } catch (e) { error.value = `Couldn't save that: ${e instanceof Error ? e.message : e}` } finally { saving.value = null }
}
const heard = (app: string) => summary.value?.by_app[app as keyof QuestionChecks['by_app']] ?? {}
</script>

<template>
  <main class="page">
    <a :href="href.progress" class="back">← Progress</a>
    <h1>Questions, by ear</h1>
    <p class="lead">The questions people asked you in one-to-ones, as the app counted them. Hear each, with the
      10 seconds after it, and say what happened. "Nobody spoke within 8 s" ones come first.</p>
    <audio ref="audio" preload="none" @pause="playing = null" @ended="playing = null" @timeupdate="onTime" />
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="items === null && !error" class="muted loading" role="status">Loading…</p>
    <p v-else-if="items && !items.length" class="muted">No questions in one-to-ones yet.</p>

    <section v-if="summary?.checked" class="card pad" aria-labelledby="sofar">
      <h2 id="sofar">So far · {{ summary.checked }} heard</h2>
      <ul class="sofar">
        <li v-for="app in ['nobody', 'you', 'other']" :key="app" v-show="Object.keys(heard(app)).length">
          <b>{{ APP[app] }}:</b>
          <template v-for="(n, v, k) in heard(app)" :key="v">{{ k ? ' · ' : ' ' }}{{ n }} {{ HEARD[v as QuestionVerdict] }}</template>
        </li>
      </ul>
    </section>

    <p v-if="items?.length" class="muted small">{{ left }} of {{ items.length }} to hear.</p>
    <ol v-if="items?.length" class="qs">
      <li v-for="i in items" :key="keyOf(i)" class="card q" :class="{ done: i.verdict }">
        <div class="meta small muted">{{ when(i.at) }} · {{ mss(i.start) }} · the app: {{ APP[i.outcome] }}</div>
        <button type="button" class="say" :aria-pressed="playing === keyOf(i)" @click="play(i)">
          <span class="ts">{{ playing === keyOf(i) ? '❚❚' : '▶' }}</span>
          <span><b>{{ i.by_name ?? `Speaker ${i.by}` }}</b> “{{ i.text }}”</span>
        </button>
        <div class="verdicts" role="group" :aria-label="`What happened after ${i.by_name ?? 'their'} question`">
          <button v-for="(label, v) in VERDICT" :key="v" type="button" class="btn btn-small" :class="{ on: i.verdict === v }"
                  :aria-pressed="i.verdict === v" :disabled="saving === keyOf(i)" @click="mark(i, v)">{{ label }}</button>
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
.sofar { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
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
