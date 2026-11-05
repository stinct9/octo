<script setup lang="ts">
// A page per person, for before you talk (docs/coach-plan.md §4.4): what the lab already holds about
// you and them, read from what's stored, no model call. What to have in mind first (your focus, the
// coach's theme where it showed with them, open steps), then what the reviews read in them, you with
// them in numbers, and the conversations.
import { computed, onMounted, ref } from 'vue'
import Commitments from '../components/Commitments.vue'
import FromReviews from '../components/FromReviews.vue'
import QuietLine from '../components/QuietLine.vue'
import OwnerWords from '../components/OwnerWords.vue'
import { lab } from '../lab/api'
import { kept } from '../lab/kept'
import { day, MEASURE_LABEL, measureValue, todoLabel, when } from '../lab/format'
import { HAPPENED, happenedText } from '../lab/intention'
import { href } from '../lab/router'
import type { Measure, PersonPage, WithAndEveryone } from '../lab/types'

const props = defineProps<{ id: string }>()
const p = ref<PersonPage | null>(kept.people.get(props.id) ?? null)  // as last seen, then asked again
const error = ref('')
onMounted(async () => {
  try {
    p.value = await lab.person(props.id)
    kept.people.set(props.id, p.value)
  } catch (e) {
    p.value = null
    kept.people.delete(props.id)
    error.value = e instanceof Error ? e.message : String(e)
  }
})

const stepSessions = computed(() => [...new Set((p.value?.steps ?? []).map((t) => t.session_id))])
const allReadings = ref(false)
const readings = computed(() => (allReadings.value ? p.value?.readings ?? [] : (p.value?.readings ?? []).slice(0, 3)))
const conversations = (n: number) => `${n} conversation${n === 1 ? '' : 's'}`

// You with them against everyone: one row per measure, only where there's something on either side.
const ROWS = ['talk_share', 'questions', 'talked_over', 'fillers', 'patience', 'their_longest'] as const  // the last two: B13
const rows = computed(() => {
  const n = p.value?.numbers
  if (!n) return []
  return ROWS.map((m) => ({ m, ...(n[m] as WithAndEveryone) })).filter((r) => r.with || r.everyone)
})
const fmt = (m: Measure, v: { value: number } | null) => (v ? measureValue(m, v.value) : '–')
const share = (v: number) => `${Math.round(v * 100)}%`
const one = (v: number | null) => (v === null ? '–' : v.toFixed(1))

// B15: the next conversation where they're confirmed takes it, and its review says whether it happened.
async function saveIntention(text: string) {
  const cur = p.value
  if (cur) cur.intention = (await lab.intention(cur.id, text)).intention
}
</script>

<template>
  <main class="page">
    <a :href="href.people" class="back">← People</a>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!p" class="muted loading" role="status">Loading…</p>
    <template v-else>
      <div>
        <h1>{{ p.name }}</h1>
        <p class="muted">{{ p.count ? `${conversations(p.count)} together` : 'No conversation with them confirmed yet' }}<template
          v-if="p.last_at"> · latest: {{ when(p.last_at) }}</template></p>
        <QuietLine v-if="!p.self" :person="p.id" />
      </div>

      <section v-if="p.focus || p.coach || !p.self" class="card before" aria-labelledby="b-h">
        <h2 id="b-h" class="eyebrow">Before you talk</h2>
        <div v-if="!p.self" class="item">
          <span class="k">Prepare</span>
          <span><a class="btn btn-small" :href="href.prepare(p.id)">{{ p.prepared_at ? 'Your plan' : 'Prepare for your next conversation' }}</a>
            <span class="muted small"> · {{ p.prepared_at ? `made ${when(p.prepared_at)}` : 'questions and a shape for it' }}</span></span>
        </div>
        <div v-if="!p.self" class="item">
          <span class="k">Your intention</span>
          <div class="next">
            <OwnerWords :label="`Next time with ${p.name}, I want to…`" :text="p.intention?.text ?? null" :save="saveIntention" :max="300" />
            <p class="muted small">The next conversation where {{ p.name }} is confirmed takes it, and its review says whether it happened.</p>
            <p v-if="p.last_intention" class="small last">Last time you wanted to: “{{ p.last_intention.text }}” ·
              <span :class="p.last_intention.happened ? HAPPENED[p.last_intention.happened].tone : 'muted'">{{
                happenedText(p.last_intention.happened, p.last_intention.reviewed) }}</span>
              <a :href="href.conversation(p.last_intention.session_id)">Open the conversation</a></p>
          </div>
        </div>
        <div v-if="!p.self" class="item">
          <span class="k">Rehearse</span>
          <span><a class="first" :href="href.rehearseWith(p.id)">Rehearse a conversation with {{ p.name }}</a>
            <span class="muted small"> · in text, the model playing them from what the reviews read in them</span></span>
        </div>
        <div v-if="p.focus" class="item">
          <span class="k">Your focus</span>
          <span>{{ p.focus.text }}</span>
        </div>
        <div v-if="p.coach" class="item">
          <span class="k">The coach</span>
          <span>“{{ p.coach.theme }}” showed in {{ p.coach.moments }} moment{{ p.coach.moments === 1 ? '' : 's' }} with {{ p.name }}.
            <a :href="href.coach">Open the note</a></span>
        </div>
      </section>

      <FromReviews v-if="stepSessions.length" :only="stepSessions" :title="`Open steps with ${p.name}`" />
      <Commitments v-if="!p.self" :person="p.id" :name="p.name" :skip="p.steps.map((t) => t.promise ?? '').filter(Boolean)" />

      <section v-if="p.readings.length" class="card pad" aria-labelledby="r-h">
        <div class="head"><h2 id="r-h">What the reviews read in {{ p.name }}</h2><span class="muted small">newest first</span></div>
        <ul class="readings">
          <li v-for="r in readings" :key="`${r.session_id}-${r.item}`">
            <div class="meta muted small">{{ r.at ? day(r.at) : '' }}<span v-if="r.partly" class="flag">You said partly right<template
              v-if="r.note">: “{{ r.note }}”</template></span></div>
            <div class="rh">{{ r.headline }}</div>
            <p class="obs">{{ r.observation }}</p>
            <p v-if="r.quote" class="q">“{{ r.quote.text }}”</p>
            <a class="open small" :href="href.conversation(r.session_id)">Open the conversation</a>
          </li>
        </ul>
        <button v-if="p.readings.length > 3" type="button" class="link small" @click="allReadings = !allReadings">
          {{ allReadings ? 'Fewer' : `All ${p.readings.length}` }}</button>
        <p class="muted small">A description from each review, never a verdict on them. Readings you marked not right are left out.</p>
      </section>

      <section v-if="rows.length" class="card pad" aria-labelledby="n-h">
        <div class="head"><h2 id="n-h">You with {{ p.name }}</h2></div>
        <table class="cmp">
          <caption class="sr">Your numbers with {{ p.name }} and with everyone</caption>
          <thead><tr><th scope="col" /><th scope="col" class="r">With {{ p.name }}</th><th scope="col" class="r">With everyone</th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.m">
              <th scope="row">{{ MEASURE_LABEL[r.m] }}</th>
              <td class="r"><b>{{ fmt(r.m, r.with) }}</b><span v-if="r.with?.lo != null && r.with?.hi != null" class="band small muted">
                {{ measureValue(r.m, r.with.lo) }}–{{ measureValue(r.m, r.with.hi) }}</span></td>
              <td class="r">{{ fmt(r.m, r.everyone) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">Pooled over the conversations, not averaged. The small range with a number is ordinary variation: two numbers
          whose ranges overlap may not differ at all. Questions come from punctuation, talking over from turn edges.<template
            v-if="!p.numbers.fillers.with"> Fillers need {{ p.numbers.fillers.min_words }} of your words with {{ p.name }} on the line
            ({{ p.numbers.fillers.words_with }} so far).</template> Your wait and their longest stretch come from word times, not checked by
          ear yet; middle values, not pooled.</p>
        <details v-if="p.numbers.each.length" class="each">
          <summary>Each conversation</summary>
          <table>
            <thead><tr><th scope="col">When</th><th scope="col" class="r">Your share</th><th scope="col" class="r">Questions / 10 min</th>
              <th scope="col" class="r">Talking over / 10 min</th><th scope="col" class="r">Fillers / 100 words</th></tr></thead>
            <tbody>
              <tr v-for="e in p.numbers.each" :key="e.session_id">
                <td><a :href="href.conversation(e.session_id)">{{ day(e.at) }}</a></td>
                <td class="r">{{ share(e.share) }}</td><td class="r">{{ one(e.questions) }}</td>
                <td class="r">{{ one(e.talked_over) }}</td><td class="r">{{ one(e.fillers) }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </section>

      <section v-if="p.conversations.length" class="card list" aria-labelledby="c-h">
        <div class="head pad-h"><h2 id="c-h">Conversations with {{ p.name }}</h2>
          <span v-if="p.count > p.conversations.length" class="muted small">the newest {{ p.conversations.length }} of {{ p.count }}</span></div>
        <a v-for="c in p.conversations" :key="c.session_id" class="conv" :href="href.conversation(c.session_id)">
          <span class="when">{{ when(c.at) }}<span class="muted small">{{ c.minutes }} min<template v-if="c.others.length"> · with {{ c.others.join(', ') }}</template></span></span>
          <span class="what">{{ c.summary || c.one_thing || 'No review yet' }}</span>
          <span class="state small" :class="todoLabel(c.todo).tone"><span class="dot" aria-hidden="true" />{{ todoLabel(c.todo).text }}</span>
        </a>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 900px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 18px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 4px 0 0; }
.pad { padding: 18px 22px; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.head h2 { flex: 1; }
.before { padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; border-left: 3px solid var(--accent); }
.before h2 { font-size: 12px; color: var(--accent); }
.item { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 12px; align-items: baseline; }
.k { font-size: 13px; color: var(--muted); }
.item a { color: var(--accent); margin-left: 4px; }
.item a.first { margin-left: 0; }
.next { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.last .done { color: var(--pass); }
.last .warn { color: var(--warn); }
.readings { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.readings li { padding: 12px 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 4px; }
.readings li:first-child { border-top: 0; padding-top: 2px; }
.meta { display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; }
.flag { color: var(--warn); }
.rh { font-weight: 600; }
.obs { margin: 0; color: var(--ink-2); }
.q { margin: 2px 0 0; padding: 6px 10px; background: var(--quote); border-radius: 8px; color: var(--ink-2); }
.open { color: var(--accent); text-decoration: none; align-self: flex-start; min-height: 32px; display: inline-flex; align-items: center; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; align-self: flex-start; min-height: 36px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 6px; border-top: 1px solid var(--line); font-weight: 400; }
thead th { border-top: 0; font-size: 12px; color: var(--muted); font-weight: 500; }
tbody th { color: var(--ink-2); }
.r { text-align: right; white-space: nowrap; }
.band { margin-left: 8px; white-space: nowrap; }
.each td:first-child { white-space: nowrap; }
td a { color: var(--accent); text-decoration: none; }
.each summary { cursor: pointer; color: var(--accent); font-size: 14px; min-height: 36px; display: flex; align-items: center; }
.each table { margin-top: 6px; }
.list { overflow: hidden; }
.pad-h { padding: 16px 22px 8px; }
.conv { display: grid; grid-template-columns: 170px minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 12px 22px;
  border-top: 1px solid var(--line); color: var(--ink); text-decoration: none; }
.conv:hover { background: var(--quote); }
.when { display: flex; flex-direction: column; font-weight: 500; }
.what { color: var(--ink-2); min-width: 0; }
.state { white-space: nowrap; display: inline-flex; align-items: center; gap: 7px; font-weight: 500; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.state.muted { color: var(--muted); font-weight: 400; }
.state.warn { color: var(--warn); }
.state.accent { color: var(--accent); }
.state.done { color: var(--pass); font-weight: 400; }
.state.fail { color: var(--fail); }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 640px) {
  .page { padding: 14px 16px 40px; }
  .pad { padding: 16px; }
  .item { grid-template-columns: 1fr; gap: 2px; }
  .conv { grid-template-columns: 1fr; gap: 4px; padding: 12px 16px; }
  .pad-h { padding: 14px 16px 6px; }
  .each { overflow-x: auto; }
  .each table { min-width: 560px; }
  .cmp .band { display: block; margin-left: 0; }
}
</style>
