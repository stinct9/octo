<script setup lang="ts">
import { computed } from 'vue'
import { formatValue } from '../lib/format'
import { actions, busy, state } from '../store'
import type { StepView } from '../types'
import DeviceLog from './DeviceLog.vue'
import LevelChart from './LevelChart.vue'
import MetricGrid from './MetricGrid.vue'
import MicPreroll from './MicPreroll.vue'
import TranscriptPanel from './TranscriptPanel.vue'

const props = defineProps<{ step: StepView }>()

const queuedInRun = computed(() => props.step.state === 'queued' && busy.value && state.run?.steps.some((s) => s.id === props.step.id))
const waitingHere = computed(() => state.run?.state === 'waiting_for_user' && state.run.prompt?.step === props.step.id)
const STATUS_WORD: Record<string, string> = {
  pass: 'Pass', warn: 'Warn', fail: 'Fail', skipped: 'Skipped', cancelled: 'Cancelled', aborted: 'Aborted',
  running: 'Running', queued: 'Not run yet',
}
const PHASE: Record<string, string> = {
  compile: 'Compiling firmware', upload: 'Uploading firmware', connect: 'Connecting to the board',
  model: 'Loading Whisper', transcribe: 'Transcribing',
}
const progressText = computed(() => {
  const p = props.step.progress
  if (!p || props.step.state !== 'running') return ''
  const base = PHASE[p.phase] ?? p.phase
  if (p.pct != null) return `${base} · ${formatValue(Math.round(p.pct))}%`
  return p.detail ? `${base} · ${p.detail}` : `${base}…`
})
// Transcript text is shown in its own panel, not as a metric card.
const metrics = computed(() => props.step.metrics.filter((m) => !(props.step.id === 'transcribe' && m.key === 'text')))
const failed = computed(() => props.step.state === 'fail')
</script>

<template>
  <main class="detail">
    <MicPreroll v-if="waitingHere" />

    <template v-else>
      <div class="title-row">
        <h1>{{ step.title }}</h1>
        <span class="status" :class="step.state">
          {{ queuedInRun ? (step.progress?.phase === 'compile' ? 'Queued · compiling firmware' : 'Queued') : STATUS_WORD[step.state] }}<template v-if="step.summary && step.state !== 'running'"> · {{ step.summary }}</template>
        </span>
      </div>

      <div v-if="progressText" class="progress card" role="status">
        <span>{{ progressText }}</span>
        <div v-if="step.progress?.pct != null" class="bar"><div :style="{ width: `${step.progress.pct}%` }" /></div>
      </div>

      <section v-if="step.hint" class="hint" :class="{ soft: !failed }" aria-label="What to do">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><path d="M11 2.5l9 16H2z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" /><path d="M11 9v4.5M11 16.2v.3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
        <div class="hint-body">
          <div class="hint-title">{{ failed ? 'Likely fix' : 'Worth knowing' }}</div>
          <p>{{ step.hint }}</p>
          <button v-if="failed" type="button" class="btn btn-small btn-danger" :disabled="busy" @click="actions.runOne(step.id)">Run {{ step.title }} again</button>
        </div>
      </section>

      <div v-if="step.levels || step.transcripts" class="media">
        <LevelChart v-if="step.levels" :levels="step.levels" :recording="step.recording" />
        <TranscriptPanel v-if="step.transcripts" :transcripts="step.transcripts" />
      </div>

      <MetricGrid v-if="metrics.length" :metrics="metrics" :columns="step.id === 'mic' ? 4 : 3" />

      <DeviceLog :logs="step.logs" />
    </template>
  </main>
</template>

<style scoped>
.detail { flex-grow: 1; min-width: 0; padding: 24px 28px; display: flex; flex-direction: column; gap: 18px; overflow-y: auto; }
.title-row { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 24px; font-weight: 600; }
.status { font-size: 14px; font-weight: 500; color: var(--muted); }
.status.pass { color: var(--pass); }
.status.warn { color: var(--warn); }
.status.fail { color: var(--fail); font-weight: 600; }
.progress { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: var(--ink-2); }
.bar { height: 6px; border-radius: 3px; background: var(--chart-quiet); overflow: hidden; }
.bar div { height: 100%; background: var(--accent); transition: width 120ms linear; }
.hint { display: flex; gap: 14px; padding: 18px 20px; border-radius: 14px; background: var(--fail-soft); color: var(--fail); }
.hint.soft { background: var(--warn-soft); color: var(--warn); }
.hint svg { flex-shrink: 0; margin-top: 2px; }
.hint-body { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.hint-title { font-size: 16px; font-weight: 600; }
.hint p { margin: 0; font-size: 15px; line-height: 1.6; color: var(--ink); }
.media { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 20px; align-items: start; }
.media > :only-child { grid-column: 1 / -1; }
@media (max-width: 960px) {
  .detail { overflow: visible; padding: 20px 16px; }
  .media { grid-template-columns: minmax(0, 1fr); }
}
</style>
