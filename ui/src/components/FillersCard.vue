<script setup lang="ts">
// Progress: the owner's fillers over time (docs/coach-plan.md §4.2). The line is their own words pooled, on
// one measuring stick, against his first fortnight; never a target and no red or green (A4). His list
// is changed on the Fillers page (#/fillers), and the whole history is recounted when he does.
import { computed, onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { day, when } from '../lab/format'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { FillersSummary, Measure } from '../lab/types'
import FillerTrend from './FillerTrend.vue'
import FocusEditor from './FocusEditor.vue'

const f = ref<FillersSummary | null>(null)
const error = ref('')
onMounted(async () => { try { f.value = await lab.fillers() } catch (e) { error.value = e instanceof Error ? e.message : String(e) } })

const t = computed(() => f.value?.trend)
const words = (n: number) => n.toLocaleString('en-GB')
const CHANGE: Record<'lower' | 'higher' | 'within', string> = {
  lower: 'Lower than your first fortnight, beyond ordinary variation.',
  higher: 'Higher than your first fortnight, beyond ordinary variation.',
  within: 'Within ordinary variation of your first fortnight.',
}
const changeText = computed(() => (t.value?.change ? CHANGE[t.value.change] : ''))

// ---- make it the focus -------------------------------------------------------------------------
const editing = ref(false)
const saving = ref(false)
const focusError = ref('')
const prefill = computed(() => {
  const top = f.value?.common[0]?.phrase
  return top ? `Pause instead of saying “${top}”` : 'Pause instead of filling'
})
async function saveFocus(text: string, measure: Measure | null) {
  saving.value = true
  try { await lab.startFocus(text, undefined, measure); editing.value = false; focusError.value = '' }
  catch (e) { focusError.value = e instanceof Error ? e.message : String(e) } finally { saving.value = false }
}
function open(id: string) { location.hash = href.conversation(id) }
</script>

<template>
  <section class="card pad" aria-labelledby="fil">
    <div class="head">
      <h2 id="fil">Fillers, over time</h2>
      <a v-if="onMac" class="link small" :href="href.fillerCheck">Check by ear</a>
      <button v-if="f && t?.points.length" type="button" class="practise" @click="editing = true">Make it your focus</button>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <p v-if="!f && !error" class="muted loading" role="status">Loading…</p>
    <template v-else-if="f && t">
      <div v-if="t.now || t.baseline" class="stats">
        <div v-if="t.now" class="stat">
          <span class="v">{{ t.now.rate.toFixed(1) }}</span>
          <span class="l">per 100 words now<br><span class="muted">your last {{ words(t.now.words) }} words, after the fortnight</span></span>
        </div>
        <div v-if="t.baseline" class="stat">
          <span class="v quiet">{{ t.baseline.rate.toFixed(1) }}</span>
          <span class="l">your first fortnight<br><span class="muted">{{ words(t.baseline.words) }} words</span></span>
        </div>
        <p v-if="changeText" class="change small">{{ changeText }}</p>
        <p v-else-if="t.baseline && !t.now" class="change small muted">Your first fortnight (to {{ day(t.baseline_until ?? 0) }}) is the
          reference; the comparison starts once about 500 of your words come after it ({{ words(t.words_after) }} so far).</p>
      </div>
      <FillerTrend v-if="t.points.length" :points="t.points" :line="t.line" :baseline="t.baseline" :window-words="f.window_words" @open="open" />
      <p v-else class="muted">Nothing on the line yet: it needs an English conversation, transcribed by Qwen, with at least
        {{ f.min_words }} of your words and you confirmed in it.</p>

      <details v-if="t.points.length" class="more small">
        <summary>Show as a table</summary>
        <div class="scroll">
          <table>
            <thead><tr><th>When</th><th>With</th><th class="r">Your words</th><th class="r">Fillers</th><th class="r">Per 100</th><th class="r">Line</th></tr></thead>
            <tbody>
              <tr v-for="(p, i) in t.points" :key="p.session_id">
                <td><a :href="href.conversation(p.session_id)">{{ when(p.at) }}</a></td><td>{{ p.with.join(', ') || '—' }}</td>
                <td class="r">{{ words(p.words) }}</td><td class="r">{{ p.fillers }}</td><td class="r">{{ p.rate?.toFixed(1) }}</td>
                <td class="r">{{ t.line[i]?.rate.toFixed(1) }} <span class="muted">({{ t.line[i]?.lo.toFixed(1) }}–{{ t.line[i]?.hi.toFixed(1) }})</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>

      <p class="note small">
        <template v-if="f.checks.checks">Counts only what the transcription kept: by ear you heard {{ f.checks.by_ear }}, and the
          app counted {{ f.checks.by_app }} in the same stretches<template v-if="f.checks.share !== null && f.checks.share < 0.7">, so the line runs
          low: compare it with itself</template><template v-else-if="f.checks.share !== null && f.checks.share > 1.3">: if it counts words you meant,
          take them off your list</template>.</template>
        <template v-else>Counts only what the transcription kept, and that's not checked yet:
          <template v-if="onMac"><a :href="href.fillerCheck">count your fillers by ear</a> on three minutes of one conversation
          (about 5 minutes) to see how many it hears.</template><template v-else>count your fillers by ear, on your Mac, to see
          how many it hears.</template></template>
        The line pools about {{ words(f.window_words) }} of your words; the band is ordinary variation, so a move inside it isn't a change.
      </p>
      <details v-if="f.off_line.length" class="more small">
        <summary>{{ f.off_line.length }} conversation{{ f.off_line.length === 1 ? '' : 's' }} off the line, and why</summary>
        <ul class="off">
          <li v-for="o in f.off_line" :key="o.session_id"><a :href="href.conversation(o.session_id)">{{ when(o.at) }}</a> · {{ o.reason }}</li>
        </ul>
      </details>

      <p class="small list">Your list: {{ f.phrases.join(', ') || 'empty' }}<template v-if="f.common.length"> · most often
        {{ f.common.slice(0, 3).map((c) => `${c.phrase} ×${c.count}`).join(', ') }}</template></p>
      <a class="work" :href="href.fillers">Work on your fillers →</a>

      <template v-if="f.groups.people.length || f.groups.sizes.length">
        <div class="sub"><h3>Where they happen</h3><span class="muted small">Each with at least {{ f.groups.min_words }} of your words</span></div>
        <div class="scroll">
          <table class="where">
            <tbody>
              <tr v-for="g in f.groups.people" :key="`p-${g.name}`"><td>With {{ g.name }}</td><td class="r strong">{{ g.rate.toFixed(1) }}</td>
                <td class="muted small">{{ g.lo.toFixed(1) }}–{{ g.hi.toFixed(1) }}</td><td class="r muted small">{{ words(g.words) }} words · {{ g.conversations }}</td></tr>
              <tr v-if="f.groups.sizes.length" class="group-h"><td colspan="4">By how many were talking</td></tr>
              <tr v-for="g in f.groups.sizes" :key="`s-${g.size}`" class="size"><td>{{ g.label[0].toUpperCase() + g.label.slice(1) }}</td><td class="r strong">{{ g.rate.toFixed(1) }}</td>
                <td class="muted small">{{ g.lo.toFixed(1) }}–{{ g.hi.toFixed(1) }}</td><td class="r muted small">{{ words(g.words) }} words · {{ g.conversations }}</td></tr>
            </tbody>
          </table>
        </div>
      </template>
      <p v-if="t.points.length" class="small muted">Since {{ day(t.points[0].at ?? 0) }} · {{ t.points.length }} conversation{{ t.points.length === 1 ? '' : 's' }} on the line</p>
    </template>
    <FocusEditor :open="editing" :initial="prefill" :replaces="null" :saving="saving" :error="focusError" measure="fillers"
                 @save="saveFocus" @cancel="editing = false; focusError = ''" />
  </section>
</template>

<style scoped>
.pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 19px; font-weight: 600; flex-grow: 1; }
h3 { margin: 0; font-size: 15px; font-weight: 600; }
.sub { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-top: 6px; }
p { margin: 0; }
.stats { display: flex; align-items: flex-end; gap: 28px; flex-wrap: wrap; }
.stat { display: flex; align-items: flex-end; gap: 10px; }
.v { font-size: 34px; font-weight: 600; line-height: 1; }
.v.quiet { color: var(--ink-2); font-weight: 500; }
.l { font-size: 13px; line-height: 1.35; }
.change { flex-basis: 100%; color: var(--ink-2); }
.note { color: var(--ink-2); background: var(--quote); border-radius: 10px; padding: 10px 12px; }
.note a, .more a, td a { color: var(--accent); }
.more summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; gap: 8px; color: var(--accent); list-style: none; }
.more summary::-webkit-details-marker { display: none; }
.more summary::before { content: ''; width: 6px; height: 6px; border-right: 1.6px solid currentColor; border-bottom: 1.6px solid currentColor;
  transform: rotate(-45deg); transition: transform 0.15s; margin: 0 2px; }
.more[open] summary::before { transform: rotate(45deg); }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 34px; border-radius: 10px; cursor: pointer; }
.off { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
.list { color: var(--ink-2); }
.work, .head .link { color: var(--accent); text-decoration: none; display: inline-flex; align-items: center; min-height: 32px; align-self: flex-start; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 14px; width: 100%; }
th { text-align: left; font-size: 12px; font-weight: 500; color: var(--muted); padding: 6px 8px 6px 0; }
td { padding: 7px 8px 7px 0; border-top: 1px solid var(--line); white-space: nowrap; font-variant-numeric: tabular-nums; }
.where td:first-child { white-space: normal; }
.r { text-align: right; }
.strong { font-weight: 600; }
.size td:first-child { color: var(--ink-2); }
.group-h td { font-size: 12px; color: var(--muted); padding-top: 14px; border-top: 0; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 760px) {
  .pad { padding: 16px; }
  .practise, .work, .head .link { min-height: 44px; }
}
</style>
