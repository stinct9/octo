<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { etaText, impactText } from '../lab/format'
import type { ClaudeUsage, PipelineImpact, PipelineStep } from '../lab/types'

const steps = ref<PipelineStep[] | null>(null)
const error = ref('')
async function load() {
  try { steps.value = await lab.pipeline(); error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  lab.claudeUsage().then((u) => { usage.value = u }).catch(() => {})
  lab.status().then((s) => { queue.value = s.queue_steps ? { steps: s.queue_steps, done: etaText(s.queue_empty_at) } : null }).catch(() => {})
}
// The counts move while work runs in the background.
let timer: ReturnType<typeof setInterval> | undefined
const queue = ref<{ steps: number; done: string | null } | null>(null)  // B3
const usage = ref<(Record<'last_5h' | 'today', ClaudeUsage> & { claude_code?: boolean }) | null>(null)
const usageText = (u: ClaudeUsage) => `${u.calls} call${u.calls === 1 ? '' : 's'}, ${u.minutes} min, ${Math.round(u.tokens / 1000)}k tokens`
  + (u.unrecorded_calls ? ` (+${u.unrecorded_calls} merge calls from before it was recorded)` : '')
onMounted(() => { load(); timer = setInterval(load, 15000) })
onUnmounted(() => clearInterval(timer))

// Changing a step's model: pick → see what it redoes → switch.
const open = ref<string | null>(null)
const picked = ref<string | null>(null)
const impact = ref<PipelineImpact | null>(null)
const busy = ref(false)
const changeError = ref('')
const done = ref<Record<string, string>>({})

function startChange(s: PipelineStep) {
  open.value = s.stage
  picked.value = s.selected
  impact.value = null
  changeError.value = ''
}
async function pick(s: PipelineStep, id: string) {
  picked.value = id
  impact.value = null
  changeError.value = ''
  if (id === s.selected) return
  try { impact.value = await lab.pipelineImpact(s.stage) } catch (e) { changeError.value = e instanceof Error ? e.message : String(e) }
}
async function confirm(s: PipelineStep) {
  if (!picked.value || picked.value === s.selected) return
  busy.value = true
  try {
    const r = await lab.choose(s.stage, picked.value)
    steps.value = r.steps
    const label = s.choices.find((c) => c.id === r.selected)?.label ?? r.selected
    done.value = { ...done.value, [s.stage]: `Switched to ${label}. ${r.impact.conversations ? 'Redoing in the background.' : ''}` }
    open.value = null
  } catch (e) {
    changeError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
const selectedChoice = (s: PipelineStep) => s.choices.find((c) => c.id === s.selected)
// The review's model is chosen in one place, Models and keys' Review with (review 5 Nov, item 9): not again here.
const elsewhere = (s: PipelineStep) => s.stage === 'insight'
</script>

<template>
  <!-- Settings → Models → Each step (advanced) since 5 Nov (review item 9); #/pipeline opens it there -->
  <div class="page">
    <div>
      <p class="muted">What happens to every recording, in order, and the model each step uses. Everything runs on this Mac except the
        review (with the Hindi merge and the coach), which sends the transcript's text to the model you choose.</p>
      <p v-if="usage" class="muted small">Model calls for reviews, Hindi merges and the coach<template v-if="usage.claude_code"> (Claude Code:
        your Claude plan's limits, shared with your own Claude use)</template>: last 5 h {{ usageText(usage.last_5h) }} · today {{ usageText(usage.today) }}</p>
    </div>
    <p v-if="queue" class="queue">Queue: {{ queue.steps }} step{{ queue.steps === 1 ? '' : 's' }}<template v-if="queue.done"> ·
      empty {{ queue.done }}</template></p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!steps" class="muted loading" role="status">Loading…</p>
    <ol v-else class="steps">
      <li v-for="s in steps" :key="s.stage" class="step">
        <span class="num" aria-hidden="true">{{ s.n }}</span>
        <section class="card body" :aria-labelledby="`st-${s.stage}`">
          <div class="top">
            <h2 :id="`st-${s.stage}`">{{ s.name }}</h2>
            <div class="chips">
              <span v-if="s.counts.running" class="chip run">{{ s.counts.running }} running</span>
              <span v-if="s.counts.queued" class="chip">{{ s.counts.queued }} queued</span>
              <span v-if="s.counts.failed" class="chip fail">{{ s.counts.failed }} failed</span>
              <span v-if="s.counts.paused" class="chip">{{ s.counts.paused }} not transcribed (language)</span>
              <span class="chip ok">{{ s.counts.done }} done</span>
            </div>
          </div>
          <p class="does">{{ s.does }}</p>

          <div class="model">
            <span class="label">Model</span>
            <div>
              <div v-if="s.model && s.choices.length" class="muted small">{{ s.model }}</div>
              <div class="name">{{ selectedChoice(s)?.label ?? (s.blocked ? 'Not chosen yet' : s.model) }}<span v-if="s.choices.length" class="muted small"> · {{ s.choices.length }} options</span></div>
              <p v-if="selectedChoice(s)" class="why">{{ selectedChoice(s)?.why }}</p>
              <p class="why">{{ s.why }}</p>
              <p v-if="s.fixed" class="muted small">Not switchable here: {{ s.fixed }}</p>
            </div>
          </div>

          <p v-if="s.blocked" class="warn" role="status">{{ s.blocked }}:
            <template v-if="elsewhere(s)">choose one in <b>Review with</b>, under Models and keys above.</template>
            <template v-else><template v-if="s.choices.length">choose one with <b>Change model…</b> below, or </template>
              connect one in Models and keys above.</template></p>
          <p v-else-if="elsewhere(s)" class="muted small">Chosen in <b>Review with</b>, under Models and keys above.</p>
          <p v-if="done[s.stage]" class="note small" role="status">{{ done[s.stage] }}</p>

          <div v-if="s.choices.length && !elsewhere(s) && open !== s.stage" class="actions">
            <button type="button" class="btn btn-small" @click="startChange(s)">Change model…</button>
          </div>

          <div v-if="open === s.stage" class="change">
            <fieldset>
              <legend class="small muted">Choose a model for {{ s.name.toLowerCase() }}</legend>
              <label v-for="c in s.choices" :key="c.id" class="option" :class="{ off: !!c.unavailable, on: picked === c.id }">
                <input type="radio" :name="`ch-${s.stage}`" :value="c.id" :checked="picked === c.id"
                       :disabled="!!c.unavailable" @change="pick(s, c.id)">
                <span>
                  <b>{{ c.label }}</b>
                  <span v-if="c.id === s.selected" class="tag">in use</span>
                  <span class="why block">{{ c.why }}</span>
                  <span v-if="c.unavailable" class="fail-text small block">Can't run here: {{ c.unavailable }}</span>
                </span>
              </label>
            </fieldset>
            <div v-if="picked && picked !== s.selected" class="impact">
              <p v-if="!impact && !changeError" class="muted small">Working out what this redoes…</p>
              <template v-if="impact">
                <p>{{ impactText(impact) }}</p>
                <p class="muted small">{{ impact.keeps }} Switching back works the same way.</p>
              </template>
            </div>
            <p v-if="changeError" class="error small" role="alert">{{ changeError }}</p>
            <div class="buttons">
              <button type="button" class="btn btn-small" @click="open = null">Cancel</button>
              <button type="button" class="btn btn-small btn-primary" :disabled="busy || !impact || picked === s.selected"
                      @click="confirm(s)">
                Switch to {{ s.choices.find((c) => c.id === picked)?.label }}
              </button>
            </div>
          </div>

          <details v-if="s.tried.length" class="tried">
            <summary class="small muted">Tried and turned down ({{ s.tried.length }})</summary>
            <ul><li v-for="t in s.tried" :key="t" class="small">{{ t }}</li></ul>
          </details>
        </section>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 18px; }
p { margin: 4px 0 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.queue { margin: 0; font-weight: 500; }
.error, .fail-text { color: var(--fail); }
.block { display: block; }

.steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.step { position: relative; display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 14px; }
/* the thread joining the steps */
.step:not(:last-child)::before { content: ''; position: absolute; left: 15px; top: 34px; bottom: -14px; width: 2px; background: var(--line); }
.num { width: 32px; height: 32px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  background: var(--surface); border: 1px solid var(--line-strong); font-weight: 600; font-size: 14px; color: var(--ink-2); z-index: 1; }
.body { padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; }
.top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 18px; font-weight: 600; }
.does { margin: 0; color: var(--ink-2); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--chip); color: var(--ink-2); white-space: nowrap; }
.chip.ok { background: var(--pass-soft); color: var(--pass); }
.chip.run { background: var(--accent-soft); color: var(--accent); }
.chip.fail { background: var(--fail-soft); color: var(--fail); }

.model { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; padding-top: 10px; border-top: 1px solid var(--line); }
.label { font-size: 12px; color: var(--muted); padding-top: 2px; }
.name { font-weight: 600; font-size: 16px; }
.why { color: var(--muted); font-size: 14px; margin-top: 4px; }

.note { color: var(--pass); }
.warn { margin: 0; padding: 10px 12px; border-radius: 10px; background: var(--accent-soft); color: var(--ink); }
.actions { display: flex; }

.change { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: 10px; background: var(--ground); }
fieldset { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
legend { padding: 0 0 6px; }
.option { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 10px; padding: 10px 12px; border: 1px solid var(--line);
  border-radius: 10px; background: var(--surface); cursor: pointer; }
.option.on { border-color: var(--accent); }
.option.off { opacity: 0.6; cursor: not-allowed; }
.option input { margin-top: 3px; accent-color: var(--accent); }
.tag { margin-left: 8px; font-size: 11px; padding: 2px 7px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
.impact p { color: var(--ink-2); }
.buttons { display: flex; justify-content: flex-end; gap: 8px; }

.tried summary { cursor: pointer; }
.tried ul { margin: 6px 0 0; padding-left: 18px; color: var(--ink-2); }
.tried li { margin: 3px 0; }

@media (max-width: 640px) {
  .step { grid-template-columns: 24px minmax(0, 1fr); gap: 10px; }
  .num { width: 24px; height: 24px; font-size: 12px; }
  .step:not(:last-child)::before { left: 11px; top: 26px; }
  .model { grid-template-columns: 1fr; gap: 4px; }
}
</style>
