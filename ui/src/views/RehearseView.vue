<script setup lang="ts">
// Rehearse a hard conversation, typed (B26, octo/rehearse.py): the model plays the other person, from what the reviews
// read in them (or the owner's description), and says it's a guess; then a review against the owner's principles and
// what to try in the real one. Kept apart (K4): never a conversation.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import HeldMark from '../components/HeldMark.vue'
import { lab } from '../lab/api'
import { day, when } from '../lab/format'
import { guessLine, otherName, sends } from '../lab/rehearse'
import { href } from '../lab/router'
import { canDelete } from '../lab/where'
import type { Rehearsal, RehearsalsSummary } from '../lab/types'

const props = defineProps<{ id?: string; person?: string }>()
const page = ref<RehearsalsSummary | null>(null)
const r = ref<Rehearsal | null>(null)
const error = ref('')
const fail = (e: unknown) => { error.value = e instanceof Error ? e.message : String(e) }

// ---- a new one ----
const SOMEONE = ''
const pick = ref<string>(props.person ?? SOMEONE)
const who = ref('')
const hard = ref('')
const want = ref('')
const fromIntention = ref(false)
const chosen = computed(() => page.value?.people.find((p) => p.id === pick.value) ?? null)
watch(chosen, (p) => {  // B15: the person's open intention is what they want from it, until the owner writes otherwise
  if (!want.value.trim() || fromIntention.value) { want.value = p?.intention ?? ''; fromIntention.value = !!p?.intention }
}, { immediate: true })
const ready = computed(() => !!hard.value.trim() && !!want.value.trim() && (!!chosen.value || !!who.value.trim()))
const busy = ref(false)
async function begin() {
  busy.value = true
  error.value = ''
  try {
    const got = await lab.startRehearsal(chosen.value ? { person_id: chosen.value.id, hard: hard.value, want: want.value }
      : { who: who.value, hard: hard.value, want: want.value })
    location.hash = href.rehearsal(got.id)
  } catch (e) { fail(e) } finally { busy.value = false }
}

// ---- one ----
const line = ref('')
const thread = ref<HTMLElement | null>(null)
const box = ref<HTMLTextAreaElement | null>(null)
const scrollDown = () => nextTick(() => { thread.value?.lastElementChild?.scrollIntoView({ block: 'nearest' }) })
async function say(text: string) {
  const cur = r.value
  if (!cur || busy.value) return
  busy.value = true
  error.value = ''
  if (text.trim()) cur.turns.push({ by: 'me', text: text.trim(), at: Date.now() / 1000 })  // shown while they answer
  line.value = ''
  void scrollDown()
  try { r.value = await lab.sayRehearsal(cur.id, text) } catch (e) {
    fail(e)
    r.value = await lab.rehearsal(cur.id).catch(() => cur)
  } finally {
    busy.value = false
    void scrollDown()
    void nextTick(() => box.value?.focus())
  }
}
function key(e: KeyboardEvent) {
  if (sends(e)) { e.preventDefault(); if (line.value.trim()) void say(line.value) }
}
const reviewing = ref(false)
async function stop() {
  if (!r.value) return
  reviewing.value = true
  error.value = ''
  try { r.value = await lab.stopRehearsal(r.value.id) } catch (e) { fail(e) } finally { reviewing.value = false }
}
async function remove() {
  if (!r.value || !confirm('Delete this rehearsal? What you typed, its lines and its review go for good.')) return
  try { await lab.deleteRehearsal(r.value.id); location.hash = href.rehearse } catch (e) { fail(e) }
}
const unanswered = computed(() => !!r.value && !r.value.review && r.value.turns[r.value.turns.length - 1]?.by === 'me')
const mine = computed(() => r.value?.turns.filter((t) => t.by === 'me').length ?? 0)
const again = computed(() => (r.value?.person_id ? href.rehearseWith(r.value.person_id) : href.rehearse))

onMounted(async () => {
  try {
    if (props.id) { r.value = await lab.rehearsal(props.id); if (!r.value.review) void scrollDown() } else page.value = await lab.rehearsals()
  } catch (e) { fail(e) }
})
</script>

<template>
  <main class="page">
    <a :href="href.practice" class="back">← Practice</a>
    <h1>Rehearse a conversation</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <template v-if="!id">
      <p v-if="!page && !error" class="muted loading" role="status">Loading…</p>
      <section v-else-if="page" class="card pad" aria-labelledby="new-h">
        <h2 id="new-h">Play it through before you have it</h2>
        <p class="small muted">You type what you'd say; the model answers as the other person. When you stop, a review against
          your principles, and what to try in the real one.</p>
        <label class="field"><span>Who with</span>
          <select v-model="pick">
            <option :value="SOMEONE">Someone I'll describe</option>
            <option v-for="p in page.people" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select></label>
        <label v-if="!chosen" class="field"><span>Who they are</span>
          <input v-model="who" type="text" maxlength="300" placeholder="a report who's upset about a rating"></label>
        <p v-else class="small muted">The model plays {{ chosen.name }} only from what the reviews read in them, as their page shows it
          (never a reading you marked not right): a guess, not {{ chosen.name }}.</p>
        <label class="field"><span>What's hard about it</span>
          <input v-model="hard" type="text" maxlength="300" placeholder="In your words"></label>
        <label class="field"><span>What you want from it</span>
          <input v-model="want" type="text" maxlength="300" placeholder="In your words" @input="fromIntention = false"></label>
        <p v-if="fromIntention && chosen" class="small muted">From your intention for {{ chosen.name }}; change it if this is different.</p>
        <div class="actions">
          <button type="button" class="btn btn-primary" :disabled="!ready || busy" @click="begin">{{ busy ? 'Starting…' : 'Start' }}</button>
          <span v-if="page.why_not" class="small muted">Not now: {{ page.why_not }}.</span>
        </div>
        <p class="small muted">Each line it answers is one call to your reviews' model, and the review one more, in the day's calls.
          It's sent what the reviews read in them, what you type here and the exchange; nothing else of the lab.</p>
      </section>

      <section v-if="page?.rehearsals.length" class="card pad" aria-labelledby="old-h">
        <h2 id="old-h">Your rehearsals</h2>
        <ul class="list">
          <li v-for="x in page.rehearsals" :key="x.id">
            <a :href="href.rehearsal(x.id)">With {{ otherName(x) }}</a>
            <span class="small muted">{{ when(x.created_at) }} · {{ x.lines }} line{{ x.lines === 1 ? '' : 's' }} of yours<template
              v-if="x.reviewed"> · reviewed</template></span>
          </li>
        </ul>
        <p class="small muted">Kept apart from your conversations: never in the list, a review, search, the coach or Progress.
          Forgetting someone deletes the rehearsals with them.</p>
      </section>
    </template>

    <p v-else-if="!r && !error" class="muted loading" role="status">Loading…</p>
    <template v-else-if="r">
      <section class="card pad" aria-labelledby="r-h">
        <div class="head">
          <h2 id="r-h">With {{ otherName(r) }}</h2>
          <span class="muted small">{{ when(r.created_at) }}</span>
        </div>
        <p class="guess small" role="note">{{ guessLine(r) }}</p>
        <details v-if="r.person_id" class="told">
          <summary class="small">What it was told of {{ r.name }}</summary>
          <ul v-if="r.readings.length">
            <li v-for="x in r.readings" :key="`${x.session_id}${x.headline}`" class="small">
              <b>{{ x.headline }}</b> {{ x.observation }}<span v-if="x.partly" class="muted"> · you said partly right<template
                v-if="x.note">: “{{ x.note }}”</template></span>
              <span v-if="x.at" class="muted"> · {{ day(x.at) }}</span>
            </li>
          </ul>
          <p v-else class="small muted">Nothing yet: no review has read anything in {{ r.name }}.</p>
        </details>
        <dl class="setup small">
          <dt>What's hard</dt><dd>{{ r.hard }}</dd>
          <dt>What you want</dt><dd>{{ r.want }}</dd>
        </dl>

        <ol ref="thread" class="thread" aria-label="The rehearsal">
          <li v-for="(t, i) in r.turns" :key="i" :class="t.by">
            <span class="by small muted">{{ t.by === 'me' ? 'You' : `${otherName(r)} (the model)` }}</span>
            <p>{{ t.text }}</p>
          </li>
          <li v-if="busy && !reviewing" class="them pending" role="status"><span class="by small muted">{{ otherName(r) }} (the model)</span>
            <p class="muted">…</p></li>
        </ol>

        <template v-if="!r.review">
          <p v-if="unanswered && !busy" class="small">
            <span class="muted">{{ r.waiting_until ? `Waiting for the usage limit to reset (${when(r.waiting_until)}).` : 'No answer to your last line yet.' }}</span>
            <button type="button" class="btn btn-small" :disabled="!!r.why_not" @click="say('')">Ask again</button></p>
          <form class="say" @submit.prevent="line.trim() && say(line)">
            <label class="sr" for="say-box">What you'd say</label>
            <textarea id="say-box" ref="box" v-model="line" rows="2" maxlength="2000" :disabled="busy || reviewing || mine >= r.max_turns"
                      :placeholder="r.turns.length ? 'Your reply' : `What you'd open with, to ${otherName(r)}`" @keydown="key" />
            <div class="actions">
              <button type="submit" class="btn btn-primary" :disabled="!line.trim() || busy || reviewing || !!r.why_not || unanswered">Say it</button>
              <button type="button" class="btn" :disabled="busy || reviewing || !r.turns.some((t) => t.by === 'them')" @click="stop">
                {{ reviewing ? 'Reviewing…' : 'Stop for the review' }}</button>
            </div>
          </form>
          <p v-if="r.why_not" class="small muted">Not now: {{ r.why_not }}.</p>
          <p v-else class="small muted">Enter sends, Shift+Enter is a new line. Each answer is one call ({{ mine }} of {{ r.max_turns }} lines).</p>
        </template>
      </section>

      <section v-if="r.review" class="card pad" aria-labelledby="rv-h">
        <h2 id="rv-h">The review</h2>
        <p v-if="r.review.skipped" class="muted">{{ r.review.skipped }}</p>
        <template v-else>
          <p>{{ r.review.summary }}</p>
          <div v-if="r.review.one_thing" class="one">
            <span class="eyebrow">The one thing</span>
            <p class="oh">{{ r.review.one_thing.headline }}</p>
            <p class="small">{{ r.review.one_thing.why }}</p>
            <p v-for="q in r.review.one_thing.quotes" :key="q.line" class="q small">“{{ q.text }}”</p>
          </div>
          <div v-if="r.review.next?.length">
            <h3>To try in the real one</h3>
            <ul class="tries">
              <li v-for="n in r.review.next" :key="n.title"><b>{{ n.title }}</b><span class="small"> {{ n.detail }}</span>
                <span v-if="n.why" class="small muted"> {{ n.why }}</span></li>
            </ul>
          </div>
          <div v-if="r.review.conduct?.length">
            <h3>Against your principles</h3>
            <ul class="conduct">
              <li v-for="c in r.review.conduct" :key="c.principle">
                <div class="ch"><span>{{ c.principle }}. {{ c.title }}</span><HeldMark v-if="c.held" :held="c.held" /></div>
                <p class="small">{{ c.observation }}</p>
                <p v-for="q in c.quotes" :key="q.line" class="q small">“{{ q.text }}”</p>
              </li>
            </ul>
          </div>
          <p v-if="r.review.limits" class="small muted">{{ r.review.limits }}</p>
          <p class="small muted">Only your own lines are judged: the other person's are the model's. Quotes are checked against what you
            typed; one that didn't match was left out.</p>
        </template>
      </section>
      <div class="actions">
        <a v-if="r.review" class="btn btn-primary" :href="again">Rehearse again</a>
        <a class="btn" :href="href.rehearse">All rehearsals</a>
        <button v-if="canDelete" type="button" class="btn btn-small" @click="remove">Delete this rehearsal</button>
      </div>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 20px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
.back { font-size: 14px; color: var(--accent); text-decoration: none; align-self: flex-start; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 19px; font-weight: 600; flex-grow: 1; }
h3 { margin: 6px 0 6px; font-size: 15px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.field span { color: var(--ink-2); font-weight: 500; }
.field input, .field select, .say textarea { font: inherit; font-size: 15px; color: var(--ink); background: var(--ground); border: 1px solid var(--line);
  border-radius: 9px; padding: 8px 10px; min-height: 40px; min-width: 0; width: 100%; box-sizing: border-box; }
.say textarea { resize: vertical; line-height: 1.4; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.guess { border-left: 3px solid var(--warn); padding: 6px 10px; background: var(--warn-soft); border-radius: 0 8px 8px 0; color: var(--ink); }
.told summary { cursor: pointer; color: var(--accent); min-height: 36px; display: flex; align-items: center; }
.told ul { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; }
.setup { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px 12px; margin: 0; }
.setup dt { color: var(--muted); }
.setup dd { margin: 0; }
.thread { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.thread li { display: flex; flex-direction: column; gap: 2px; max-width: 85%; }
.thread li p { padding: 8px 12px; border-radius: 12px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
.thread .me { align-self: flex-end; align-items: flex-end; }
.thread .me p { background: var(--accent-soft); color: var(--ink); }
.thread .them p { background: var(--quote); }
.say { display: flex; flex-direction: column; gap: 8px; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; flex-direction: column; gap: 2px; }
.list a { min-height: 32px; display: inline-flex; align-items: center; align-self: flex-start; }
.one { display: flex; flex-direction: column; gap: 4px; border-left: 3px solid var(--accent); padding-left: 12px; }
.one .eyebrow { color: var(--accent); }
.oh { font-weight: 600; }
.q { padding: 6px 10px; background: var(--quote); border-radius: 8px; color: var(--ink-2); }
.tries, .conduct { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.tries li { display: flex; flex-direction: column; gap: 2px; }
.conduct li { display: flex; flex-direction: column; gap: 4px; padding-top: 8px; border-top: 1px solid var(--line); }
.ch { display: flex; justify-content: space-between; gap: 10px; align-items: center; font-weight: 500; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 640px) {
  .page { padding: 14px 16px 40px; }
  .pad { padding: 18px 16px; }
  .thread li { max-width: 92%; }
  .btn { min-height: 44px; }
  .field input, .field select, .say textarea { min-height: 44px; font-size: 16px; }  /* 16 px: iOS doesn't zoom into the field */
}
</style>
