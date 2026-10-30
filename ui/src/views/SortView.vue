<script setup lang="ts">
// Sort them (B5 part 2, 13 Oct, docs/backlog.md): the recordings that may not be conversations (nobody in
// them is you, and few words or settled without you; octo/aside.py `maybe`), one at a time. A clip of up
// to 10 s plays, then one tap: it's a conversation, TV, music, something else, or skip. Undo takes back
// the last answer and leaves none. Nothing is set aside without a tap.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { ASIDE, mss, speakerColors, when } from '../lab/format'
import { href } from '../lab/router'
import type { AsideKind, SessionDetail, SessionSummary } from '../lab/types'

const queue = ref<SessionSummary[] | null>(null)
const at = ref(0)
const s = ref<SessionDetail | null>(null)
const error = ref('')
const busy = ref(false)
type Answer = { id: string; kind: AsideKind | 'conversation' | 'skip' }
const answers = ref<Answer[]>([])

const audio = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const heard = ref(false)  // after the first tap the browser lets each next clip start by itself
let clipEnd = 0

const item = computed(() => queue.value?.[at.value] ?? null)
const done = computed(() => queue.value !== null && at.value >= queue.value.length)
const setAside = computed(() => answers.value.filter((a) => a.kind !== 'conversation' && a.kind !== 'skip').length)
const kept = computed(() => answers.value.filter((a) => a.kind === 'conversation').length)

async function start() {
  try {
    const { sessions } = await lab.recentSessions(36500)
    queue.value = sessions.filter((x) => x.maybe_aside)
    error.value = ''
    await open()
  } catch (e) { error.value = `Couldn't load them: ${e instanceof Error ? e.message : e}` }
}
async function open() {
  s.value = null
  if (!item.value) return
  try { s.value = await lab.session(item.value.id) } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  if (heard.value) setTimeout(play, 150)  // once the audio element has the new source
}
// The clip: the longest stretch anyone speaks, up to 10 s (the same idea as "hear them", B1).
const clip = computed(() => {
  const turns = s.value?.turns ?? []
  const t = turns.reduce<(typeof turns)[number] | null>((a, x) => (!a || x.end - x.start > a.end - a.start ? x : a), null)
  return t ? { start: t.start, end: Math.min(t.end, t.start + 10) } : { start: 0, end: Math.min(10, s.value?.duration_s ?? 10) }
})
function play() {
  const a = audio.value
  if (!a || !s.value) return
  if (!a.paused) { a.pause(); return }
  heard.value = true
  a.currentTime = Math.max(0, clip.value.start - 0.3)
  clipEnd = clip.value.end + 0.4
  void a.play()
}
function onTime() { const a = audio.value; if (a && a.currentTime >= clipEnd) a.pause() }

async function answer(kind: Answer['kind']) {
  const it = item.value
  if (!it || busy.value) return
  heard.value = true
  audio.value?.pause()
  busy.value = true
  try {
    if (kind !== 'skip') await lab.aside(it.id, kind === 'conversation' ? null : kind)
    answers.value = [...answers.value, { id: it.id, kind }]
    at.value += 1
    error.value = ''
    await open()
  } catch (e) { error.value = `Couldn't save that: ${e instanceof Error ? e.message : e}` } finally { busy.value = false }
}
async function undo() {
  const last = answers.value[answers.value.length - 1]
  if (!last || busy.value) return
  busy.value = true
  try {
    if (last.kind !== 'skip') await lab.unanswerAside(last.id)
    answers.value = answers.value.slice(0, -1)
    at.value -= 1
    error.value = ''
    await open()
  } catch (e) { error.value = `Couldn't undo: ${e instanceof Error ? e.message : e}` } finally { busy.value = false }
}

// On the Mac: Space plays, C / T / M / O answer, S skips, U undoes.
const KEYS: Record<string, Answer['kind']> = { c: 'conversation', t: 'tv', m: 'music', o: 'other', s: 'skip' }
function onKey(e: KeyboardEvent) {
  if (e.metaKey || e.ctrlKey || e.altKey || (e.target instanceof Element && e.target.closest('input, textarea, select'))) return
  const k = e.key.toLowerCase()
  if (k === ' ') { e.preventDefault(); play() } else if (k === 'u') void undo()
  else if (KEYS[k] && !done.value) void answer(KEYS[k])
}
onMounted(() => { void start(); window.addEventListener('keydown', onKey) })
onUnmounted(() => { window.removeEventListener('keydown', onKey); audio.value?.pause() })
</script>

<template>
  <main class="page">
    <a :href="href.conversations" class="back">← Conversations</a>
    <h1>Sort them</h1>
    <p class="lead">Recordings that may not be conversations: nobody in them is you. Listen, then say what each one is.
      Set-aside ones leave your list and aren't processed; you can put any back.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="queue === null && !error" class="muted loading" role="status">Loading…</p>

    <section v-else-if="done" class="card pad" aria-live="polite">
      <h2>{{ queue?.length ? 'All sorted' : 'Nothing to sort' }}</h2>
      <p v-if="queue?.length" class="muted">{{ setAside }} set aside · {{ kept }} kept as conversation{{ kept === 1 ? '' : 's' }}<template
        v-if="answers.length - setAside - kept"> · {{ answers.length - setAside - kept }} skipped</template>.</p>
      <div class="row">
        <button v-if="answers.length" type="button" class="link" :disabled="busy" @click="undo">Undo the last</button>
        <span class="grow" /><a class="btn" :href="href.conversations">Back to conversations</a>
      </div>
    </section>

    <section v-else-if="item" class="card pad" aria-live="polite">
      <div class="row">
        <span class="muted small">{{ at + 1 }} of {{ queue?.length }}</span><span class="grow" />
        <a class="link small" :href="href.conversation(item.id)">Open it</a>
      </div>
      <h2>{{ when(item.started_at) }}</h2>
      <p class="muted small">{{ mss(item.duration_s) }} · {{ item.source }}</p>
      <div class="chips">
        <span v-for="sp in item.speakers" :key="sp.letter" class="chip" :class="speakerColors(item.speakers)[sp.letter]">{{ sp.name }}</span>
      </div>
      <p v-if="s?.audio_deleted" class="muted small">The audio was deleted after {{ s.audio_days ?? 60 }} days.</p>
      <template v-else>
        <audio v-if="s" ref="audio" :src="lab.audioUrl(s.id)" preload="auto" @play="playing = true" @pause="playing = false"
               @ended="playing = false" @timeupdate="onTime" />
        <button type="button" class="btn listen" :disabled="!s" @click="play">{{ playing ? 'Pause' : heard ? 'Play again' : 'Play a clip' }}</button>
      </template>
      <div class="answers">
        <button type="button" class="btn" :disabled="busy" @click="answer('conversation')">It's a conversation</button>
        <button v-for="(label, kind) in ASIDE" :key="kind" type="button" class="btn" :disabled="busy" @click="answer(kind)">{{ label }}</button>
      </div>
      <div class="row">
        <button type="button" class="link" :disabled="busy || !answers.length" @click="undo">Undo the last</button>
        <span class="grow" />
        <button type="button" class="link" :disabled="busy" @click="answer('skip')">Skip</button>
      </div>
      <p class="muted small keys">Keys: Space plays · C conversation · T TV · M music · O something else · S skip · U undo</p>
    </section>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 640px; margin: 0 auto; padding: 20px 24px 48px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 20px; font-weight: 600; }
p { margin: 0; }
.lead { color: var(--ink-2); }
.pad { padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; }
.row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.grow { flex: 1; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { padding: 1px 10px; border-radius: 999px; font-size: 13px; font-weight: 500; background: var(--c-soft); color: var(--c); }
.listen { min-height: 56px; font-size: 16px; }
.answers { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.answers .btn { min-height: 52px; justify-content: center; }
.answers .btn:first-child { grid-column: 1 / -1; }
.link { border: 0; background: none; padding: 0 8px; margin: 0 -8px; color: var(--accent); cursor: pointer; font: inherit; text-decoration: none; min-height: 44px; min-width: 44px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (pointer: coarse) { .keys { display: none; } }
@media (max-width: 640px) { .page { padding: 14px 16px 40px; } }
</style>
