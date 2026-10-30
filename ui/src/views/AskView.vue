<script setup lang="ts">
// Ask your conversations (B24, octo/ask.py): a question in plain words, answered from the transcripts. The Mac picks
// the lines first (the people, dates and words in it) and sends only those; every moment is checked against them and
// opens its conversation at that line. An answer not kept lives in the lab's memory for a while, never on disk.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { sentText } from '../lab/ask'
import { when } from '../lab/format'
import { href } from '../lab/router'
import type { AskEntry, AskSummary } from '../lab/types'

const sum = ref<AskSummary | null>(null)
const question = ref('')
const asking = ref(false)
const busy = ref<string | null>(null)  // the answer being kept or deleted
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  try { sum.value = await lab.asks(); error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  const running = sum.value?.live.some((a) => a.status === 'running')
  if (running && !timer) timer = setInterval(load, 3000)
  else if (!running && timer) { clearInterval(timer); timer = undefined }
}
onMounted(load)
onUnmounted(() => { if (timer) clearInterval(timer) })

async function ask() {
  if (question.value.trim().length < 3) return
  asking.value = true
  try { await lab.ask(question.value); question.value = ''; await load() }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { asking.value = false }
}
async function act(a: AskEntry, what: 'keep' | 'delete') {
  busy.value = a.id
  try { await (what === 'keep' ? lab.keepAsk(a.id) : lab.deleteAsk(a.id)); await load() }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = null }
}
const running = computed(() => sum.value?.live.some((a) => a.status === 'running') ?? false)
const cantAsk = computed(() => (running.value ? null : sum.value?.why_not ?? null))
</script>

<template>
  <main class="page">
    <a :href="href.conversations" class="back">← Conversations</a>
    <h1>Ask your conversations</h1>
    <p class="lead">A question in your own words, for what search can't find by exact words. The Mac picks the conversations
      it needs first, by the people, the dates and the words in it, and sends only those lines (at most
      {{ sum?.max_lines ?? 400 }}) to your review's model, other people's words in them too. Every moment it quotes is checked
      against what was sent. An answer you don't keep is gone after {{ sum?.live_minutes ?? 30 }} minutes, or when the lab restarts.</p>

    <form class="bar" @submit.prevent="ask">
      <input v-model="question" type="text" class="q" maxlength="300" aria-label="Your question"
             placeholder="What has Ravi said about his workload this month?">
      <button type="submit" class="btn btn-primary" :disabled="asking || running || !!cantAsk || question.trim().length < 3">
        {{ asking ? 'Picking…' : 'Ask' }}</button>
    </form>
    <p v-if="cantAsk" class="muted small">Can't ask now: {{ cantAsk }}.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!sum && !error" class="muted loading" role="status">Loading…</p>

    <template v-for="group in [{ title: '', list: sum?.live ?? [] }, { title: 'Kept', list: sum?.kept ?? [] }]" :key="group.title">
      <h2 v-if="group.title && group.list.length" class="sec">{{ group.title }}</h2>
      <article v-for="a in group.list" :key="a.id" class="card pad answer">
        <div class="meta"><h3>{{ a.question }}</h3><span class="muted small nowrap">{{ when(a.at) }}</span></div>

        <div v-if="a.status === 'running'" class="status" role="status">
          <span class="dots" aria-hidden="true"><i /><i /><i /></span>
          <span>Reading what was sent… a minute or so. This page updates by itself.</span>
        </div>
        <p v-else-if="a.status === 'failed'" class="error" role="alert">Couldn't answer: {{ a.error }}</p>
        <template v-else-if="a.answer">
          <p v-if="a.answer.not_found" class="prose">{{ a.answer.not_found }}</p>
          <ul v-if="a.answer.claims.length" class="claims">
            <li v-for="(c, i) in a.answer.claims" :key="i">
              <p>{{ c.text }}</p>
              <a v-for="(q, j) in c.quotes" :key="j" class="moment" :href="href.line(q.session_id, q.line)">
                <span class="who" :class="{ me: q.self }">{{ q.who }}<template v-if="q.uncertain"> (unsure)</template></span>
                <span class="muted small">{{ when(q.at) }} · hear it</span>
                <span class="text">“{{ q.text }}”</span>
              </a>
              <p v-if="!c.quotes.length" class="muted small">No moment the lab could check backs this.</p>
            </li>
          </ul>
          <p v-if="a.answer.dropped" class="muted small">{{ a.answer.dropped }} quote{{ a.answer.dropped === 1 ? '' : 's' }}
            weren't in what was sent, so they're left out.</p>
          <p v-if="a.answer.limits" class="muted small">{{ a.answer.limits }}</p>
        </template>

        <details v-if="a.sent" class="fold">
          <summary><span class="t small">{{ sentText(a.sent) }}</span></summary>
          <ul class="fold-body sent">
            <li v-for="c in a.sent.conversations" :key="c.session_id">
              <a :href="href.conversation(c.session_id)">{{ when(c.at) }}<template v-if="c.with.length"> · with {{ c.with.join(', ') }}</template>
                <span class="muted small"> · {{ c.lines }} line{{ c.lines === 1 ? '' : 's' }}{{ c.whole ? ', all of it' : '' }}</span></a>
            </li>
          </ul>
        </details>

        <div v-if="a.status !== 'running'" class="acts">
          <button v-if="!a.kept_at && a.status === 'done'" type="button" class="btn btn-small" :disabled="busy === a.id"
                  @click="act(a, 'keep')">Keep</button>
          <span v-if="a.kept_at" class="muted small">Kept · deleted with any conversation it read</span>
          <button type="button" class="btn btn-small" :disabled="busy === a.id" @click="act(a, 'delete')">Delete</button>
        </div>
      </article>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 820px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 14px; text-decoration: none; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 8px 0 0; font-size: 17px; font-weight: 600; }
h3 { margin: 0; font-size: 16px; font-weight: 600; line-height: 1.4; flex: 1; }
p { margin: 0; }
.lead { color: var(--ink-2); }
.bar { display: flex; gap: 10px; align-items: center; }
.q { flex: 1 1 260px; min-width: 0; min-height: 44px; padding: 0 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--surface); color: var(--ink); font: inherit; font-size: 15px; }
.bar .btn { min-height: 44px; }
.pad { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.meta { display: flex; align-items: baseline; gap: 12px; }
.status { display: flex; align-items: center; gap: 12px; color: var(--ink-2); }
.prose { color: var(--ink-2); }
.claims { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.claims li { display: flex; flex-direction: column; gap: 6px; }
.moment { display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: baseline; padding: 8px 12px; border-radius: 10px;
  background: var(--quote); color: inherit; text-decoration: none; min-height: 44px; }
.moment:hover { background: var(--accent-soft); }
.moment .text { flex-basis: 100%; font-size: 14px; overflow-wrap: anywhere; }
.who { font-weight: 500; }
.who.me { color: var(--me); }
.fold { border-top: 1px solid var(--line); }
.fold summary { display: flex; align-items: center; gap: 10px; padding: 10px 2px 0; cursor: pointer; list-style: none; min-height: 44px; }
.fold summary::-webkit-details-marker { display: none; }
.fold summary::before { content: '›'; color: var(--muted); font-size: 18px; transition: transform 0.15s; width: 10px; }
.fold[open] summary::before { transform: rotate(90deg); }
.fold .t { flex: 1; color: var(--ink-2); }
.fold-body { margin: 6px 0 0; padding: 0 2px 0 22px; display: flex; flex-direction: column; gap: 6px; list-style: none; }
.sent a { text-decoration: none; display: block; padding: 4px 0; }
.acts { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
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
  .meta { flex-direction: column; gap: 2px; }
  .bar { flex-wrap: wrap; }
  .bar .btn { width: 100%; justify-content: center; }
}
@media (pointer: coarse) { .sent a { padding: 11px 0; } }
</style>
