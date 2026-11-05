<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import FillersCard from '../components/FillersCard.vue'
import FocusBanner from '../components/FocusBanner.vue'
import HeldMark from '../components/HeldMark.vue'
import { lab } from '../lab/api'
import { kept } from '../lab/kept'
import { DELIVERY_NAME, deliveryValue } from '../lab/delivery'
import { today, when } from '../lab/format'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { Progress, QuestionsSummary } from '../lab/types'
import BetaResults from '../components/BetaResults.vue'
import WeekCard from '../components/WeekCard.vue'

// The order (review 5 Nov, item 8): the counts, your focus and its numbers over time, fillers, by principle, then your
// weeks folded and the checks by ear in one card at the end. Where every conversation is went to the list's footer.

const p = ref<Progress | null>(kept.progress)  // as last seen, then asked again
const error = ref('')
onMounted(async () => {
  try { p.value = kept.progress = await lab.progress() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  lab.questions().then((q) => { qs.value = q }).catch(() => {})
})
// Others' questions and who spoke next (B4): 1:1s and groups apart, since in a group many aren't for you.
const qs = ref<QuestionsSummary | null>(null)
const pct = (n: number, of: number) => (of ? `${Math.round((100 * n) / of)}%` : '—')

// With the year when it isn't this one: the 2020-2024 memos' weeks looked like this autumn's (M33).
const fmtDate = (iso: string) => {
  const d = new Date(`${iso}T00:00`)
  const year = d.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' as const }
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', ...year }).replaceAll(',', '')
}
// Weeks before the lab began counting reviews, in one row: old memos added later.
const countedWeeks = computed(() => (p.value?.weekly ?? []).filter((w) => w.week >= weekOf(p.value!.counts.since)))
const before = computed(() => {
  const old = (p.value?.weekly ?? []).filter((w) => w.week < weekOf(p.value!.counts.since))
  return old.length ? { conversations: old.reduce((a, w) => a + w.conversations, 0), questions: old.reduce((a, w) => a + w.questions, 0),
    talked_over: old.reduce((a, w) => a + w.talked_over, 0) } : null
})
function weekOf(iso: string): string {  // the Monday of that week, as the server keys weeks
  const d = new Date(`${iso}T00:00`)
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const pastWeeks = computed(() => countedWeeks.value.filter((w) => w.week < weekOf(today())))  // this one isn't over yet
const opened = reactive(new Set<string>())  // weeks opened: each loads its card once
// Which principles came up, merged into By principle: conversations with evidence for each.
const cameUp = (n: number) => p.value?.coverage.find((c) => c.n === n)?.conversations ?? 0
const daysSince = computed(() => {
  const at = p.value?.counts.last_conversation_at
  return at ? Math.max(0, Math.floor((Date.now() / 1000 - at) / 86400)) : null
})
// "New to me" can't separate anything while every answer is "new" (review 4 Oct, M02): say so. Since
// 6 Oct "Nothing new this time" is an answer of its own (one question per conversation).
const allNew = computed(() => !!p.value && p.value.counts.novelty.new > 0 && p.value.counts.novelty.knew === 0
  && !p.value.counts.novelty.nothing_new)
const untouched = computed(() => (p.value?.coverage ?? []).filter((c) => c.conversations === 0).length)
/** "held 3 · partly 1 of 5": how a principle went where the review judged it (since 6 Oct). */
function heldLine(seen: { held?: string | null }[]): string {
  const judged = seen.filter((x) => x.held)
  const n = (h: string) => judged.filter((x) => x.held === h).length
  return ['held', 'partly', 'missed', 'depends'].filter((h) => n(h)).map((h) => `${h} ${n(h)}`).join(' · ') + ` of ${judged.length}`
}
</script>

<template>
  <main class="page">
    <h1>Progress</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!p" class="muted loading" role="status">Loading…</p>
    <template v-else>
      <section class="card pad" aria-labelledby="counts">
        <div class="head">
          <h2 id="counts">Your reviews</h2>
          <span class="muted small">Since {{ fmtDate(p.counts.since) }}</span>
        </div>
        <p v-if="p.counts.last_conversation_at" class="small" :class="daysSince && daysSince >= 2 ? 'warn' : 'muted'">
          Last conversation recorded {{ when(p.counts.last_conversation_at) }}<template v-if="daysSince">, {{ daysSince }} day{{ daysSince === 1 ? '' : 's' }} ago</template>.</p>
        <div class="tiles">
          <div class="tile">
            <span class="muted small">Conversations reviewed</span>
            <span class="big">{{ p.counts.checks.reviewed.value }}</span>
            <span v-if="p.counts.checks.reviewed.provisional" class="muted small">{{ p.counts.checks.reviewed.provisional }} of them provisional</span>
          </div>
          <div class="tile">
            <span class="muted small">With something new to you</span>
            <span class="big">{{ p.counts.checks.new_share.value }} <span class="of">of {{ p.counts.checks.new_share.of }}</span></span>
            <span v-if="p.counts.checks.recent_new.of < p.counts.checks.new_share.of" class="small">
              {{ p.counts.checks.recent_new.value }} of the last {{ p.counts.checks.recent_new.of }}</span>
            <span class="muted small">{{ p.counts.novelty.nothing_new }} with nothing new · {{ p.counts.novelty.new }} item{{ p.counts.novelty.new === 1 ? '' : 's' }} marked new<template
              v-if="p.counts.novelty.knew"> · {{ p.counts.novelty.knew }} “knew it” (before 6 Oct)</template></span>
          </div>
          <div class="tile">
            <span class="muted small">Insights you acted on</span>
            <span class="big">{{ p.counts.checks.acted.value }}</span>
          </div>
        </div>
        <p v-if="allNew" class="note small">Every answer so far said something was new to you, so “new” can't tell much
          yet. “Nothing new this time” is an answer too: it counts the conversation as reviewed.</p>
        <p class="muted small">Insights you marked not right: {{ p.counts.wrong.value }} of {{ p.counts.wrong.of }} shown, a measure of trust.
          A conversation counts once its review ran and you've answered “What here was new to you?”<template
            v-if="p.counts.novelty.knew"> (one question since 6 Oct; before, both questions on every item)</template>.</p>
      </section>

      <FocusBanner />
      <section class="card pad" aria-labelledby="ot">
        <h2 id="ot">Over time</h2>
        <p v-if="p.weekly.length === 0" class="muted">Nothing yet.</p>
        <table v-else>
          <thead><tr><th>Week of</th><th class="r">Conversations</th><th class="r">Your share of talk</th><th class="r">Questions you asked¹</th><th class="r">You talked over others²</th></tr></thead>
          <tbody>
            <tr v-for="w in countedWeeks" :key="w.week">
              <td>{{ fmtDate(w.week) }}</td><td class="r">{{ w.conversations }}</td>
              <td class="r">{{ w.share === null ? '—' : `${Math.round(w.share * 100)}%` }}</td>
              <td class="r">{{ w.questions }}</td><td class="r">{{ w.talked_over }}</td>
            </tr>
            <tr v-if="before" class="muted">
              <td>Before {{ fmtDate(p.counts.since) }}</td><td class="r">{{ before.conversations }}</td><td class="r">—</td>
              <td class="r">{{ before.questions }}</td><td class="r">{{ before.talked_over }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">Counts only, no targets. ¹ ² approx.</p>
      </section>
      <FillersCard />
      <section v-if="p.drills && (p.drills.takes || p.drills.memos)" class="card pad" aria-labelledby="dr">
        <div class="head">
          <h2 id="dr">Practice</h2>
          <span class="muted small">{{ p.drills.takes }} take{{ p.drills.takes === 1 ? '' : 's' }}, apart from the counts above</span>
        </div>
        <table>
          <thead><tr><th></th><th v-if="p.drills.memos" class="r">Pitch memos</th><th class="r">Last {{ p.drills.lately_n || '' }} takes</th><th class="r">Latest take</th></tr></thead>
          <tbody>
            <tr v-for="r in p.drills.rows" :key="r.measure">
              <td>{{ DELIVERY_NAME[r.measure] }}</td><td v-if="p.drills.memos" class="r">{{ deliveryValue(r.measure, r.start) }}</td>
              <td class="r">{{ deliveryValue(r.measure, r.lately) }}</td><td class="r">{{ deliveryValue(r.measure, r.latest) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small"><template v-if="p.drills.memos">The line starts with the {{ p.drills.memos }} pitch memo{{ p.drills.memos === 1 ? '' : 's' }}
          you left out of the counts. </template>Takes are pooled, never averaged; no targets. <a :href="href.practice">Practice</a></p>
      </section>
      <section class="card pad" aria-labelledby="bp">
        <div class="head">
          <h2 id="bp">By principle</h2>
          <span class="muted small">Your words, in how many of {{ p.insight_conversations }} reviewed conversations each came up, and what each review said</span>
        </div>
        <p v-if="!p.by_principle.length" class="muted small">No principles yet: once you've written yours in
          <a v-if="onMac" :href="href.setup">Set up Octo</a><template v-else>Set up Octo, on the Mac</template>, what each review
          said about them shows here.</p>
        <details v-for="bp in p.by_principle" :key="bp.n" class="bp" :open="bp.seen.length > 0 && bp.seen.length <= 2">
          <summary><span class="muted">{{ bp.n }}</span> {{ bp.text }} <span class="muted small">· {{ cameUp(bp.n) ? `came up in ${cameUp(bp.n)}` : 'not come up yet' }}</span>
            <span v-if="bp.seen.some((x) => x.held)" class="marks" :aria-label="heldLine(bp.seen)">
              <span v-for="x in [...bp.seen].reverse()" :key="`${x.session_id}${x.item}`" class="mk"
                    :title="`${when(x.at)}: ${x.held ?? (p.first_lab ? 'before 6 Oct, not judged' : 'not judged')}`"><HeldMark v-if="x.held" :held="x.held" bare icon /><span v-else class="dash">–</span></span>
              <span class="small muted">{{ heldLine(bp.seen) }}</span></span></summary>
          <ul>
            <li v-for="x in bp.seen" :key="`${x.session_id}${x.item}`">
              <a :href="`${href.conversation(x.session_id)}`">{{ when(x.at) }}<template v-if="x.with.length"> · with {{ x.with.join(', ') }}</template></a>
              <HeldMark v-if="x.held" :held="x.held" class="hm" />
              <span v-if="x.verdict" class="verdict small" :class="x.verdict">you said {{ x.verdict }}</span>
              <p class="obs"><b v-if="x.title">{{ x.title.replace(/\.$/, '') }}.</b> {{ x.observation }}</p>
            </li>
          </ul>
        </details>
        <p v-if="p.insight_conversations > 0 && untouched" class="muted small">{{ untouched }} of {{ p.coverage.length }} haven't come up yet.</p>
      </section>
      <section v-if="pastWeeks.length" class="card pad" aria-labelledby="yw">
        <details class="yws"><summary><h2 id="yw">Your weeks</h2> <span class="muted small">· {{ pastWeeks.length }}</span></summary>
        <details v-for="w in pastWeeks" :key="w.week" class="yw" @toggle="opened.add(w.week)">
          <summary>Week of {{ fmtDate(w.week) }}</summary>
          <WeekCard v-if="opened.has(w.week)" :week="w.week" />
        </details>
        <p class="muted small">The Monday card for each week: heard, reviewed, acted on, and your focus's number then.</p>
        </details>
      </section>
      <section v-if="qs && (qs.one.asked || qs.group.asked)" class="card pad" aria-labelledby="qq">
        <div class="head">
          <h2 id="qq">Questions from others</h2>
          <span class="muted small">Who spoke next, from the transcript's question marks</span>
        </div>
        <table>
          <thead><tr><th></th><th class="r">Questions</th><th class="r">You spoke next</th><th class="r">Someone else</th><th class="r">Nobody within 8 s</th></tr></thead>
          <tbody>
            <tr v-for="(row, k) in { one: qs.one, group: qs.group }" :key="k">
              <td>{{ k === 'one' ? 'One to one' : 'Groups' }} <span class="muted small">· {{ row.conversations }} conversation{{ row.conversations === 1 ? '' : 's' }}</span></td>
              <td class="r">{{ row.asked }}</td><td class="r">{{ pct(row.you, row.asked) }}</td>
              <td class="r">{{ pct(row.other, row.asked) }}</td><td class="r">{{ pct(row.nobody, row.asked) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">A rough first count, nothing judged: in a group many questions aren't for you, and who spoke
          next isn't whether it was answered. Each conversation's page lists its questions to hear.</p>
      </section>
      <section class="card pad" aria-labelledby="ce">
        <h2 id="ce">Checks by ear</h2>
        <p class="muted small">Whether what the lab counts holds up when you listen.</p>
        <ul class="ce">
          <li v-if="onMac"><a class="link" :href="href.fillerCheck">Your fillers</a> <span class="muted small">· your count against the
            app's, before the fillers trend can be trusted</span></li>
          <li><a class="link" :href="href.questionCheck">Questions from others, in 1:1s</a> <span class="muted small">· does "who spoke
            next" hold up? It decides whether a closer look at answers is worth building.</span></li>
          <li><a class="link" :href="href.commitmentCheck">Commitments</a> <span class="muted small">· the first ten: is each a real
            promise, by the right person? It says whether the step can be trusted.</span></li>
        </ul>
      </section>
    </template>
    <BetaResults v-if="p && !p.first_lab" />
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 1040px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 20px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 19px; font-weight: 600; flex-grow: 1; }
.pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 14px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); }
p { margin: 0; }
.yw summary { cursor: pointer; padding: 4px 0; }
.yw[open] { padding-bottom: 8px; }
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.tile { border: 1px solid var(--line); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 6px; }
.big { font-size: 30px; font-weight: 600; }
.of { font-size: 16px; font-weight: 400; color: var(--muted); }
.warn { color: var(--warn); }
.note { padding: 10px 12px; border-radius: 10px; background: var(--warn-soft); color: var(--ink); }
.yws summary { cursor: pointer; }
.yws summary h2 { display: inline; }
.yws[open] summary { margin-bottom: 8px; }
.ce { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; }
.link { color: var(--accent); }
.bp summary { cursor: pointer; padding: 6px 0; line-height: 1.4; }
.bp ul { list-style: none; margin: 0 0 8px; padding: 0 0 0 22px; display: flex; flex-direction: column; gap: 10px; }
.bp a { color: var(--accent); text-decoration: none; font-size: 14px; }
.obs { margin: 2px 0 0; color: var(--ink-2); font-size: 14px; }
.verdict { margin-left: 8px; color: var(--muted); }
.hm { margin-left: 8px; }
.marks { display: inline-flex; align-items: center; gap: 4px; margin-left: 8px; flex-wrap: wrap; }
.mk { display: inline-flex; color: var(--ink-2); }
.dash { width: 12px; text-align: center; color: var(--faint); }
.verdict.wrong { color: var(--fail); }
.r { text-align: right; }
table { border-collapse: collapse; font-size: 14px; width: 100%; }
th { text-align: left; font-size: 12px; font-weight: 500; color: var(--muted); padding: 8px 0; }
td { padding: 10px 0; border-top: 1px solid var(--line); }
@media (max-width: 760px) {
  .page { padding: 16px 16px 40px; }
  .tiles { grid-template-columns: minmax(0, 1fr); }
  th:nth-child(5), td:nth-child(5) { display: none; }
}
</style>
