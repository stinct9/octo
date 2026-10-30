<script setup lang="ts">
import { computed } from 'vue'
import { clock, delta, duration, formatValue } from '../lib/format'
import { actions, busy, state } from '../store'
import type { StepView } from '../types'
import StatusIcon from './StatusIcon.vue'

// Rows come from the current/last run when there is one, else the step catalog.
const rows = computed<StepView[]>(() => {
  const byId = new Map(state.run?.steps.map((s) => [s.id, s]) ?? [])
  return state.catalog.map((c) => byId.get(c.id) ?? state.lastKnown[c.id] ?? {
    id: c.id, title: c.title, needsUser: c.needs_user, state: 'queued', metrics: [], logs: [],
    summary: c.skip_reason ?? 'Not run yet',
  })
})

// The headline number per step, and how it moved vs the previous run.
const HEADLINE: Record<string, string> = {
  board: 'psram_mb', mic: 'snr_db', sd: 'write_kbs', wifi: 'rssi_mean', transcribe: 'mean_logprob',
}
function change(s: StepView): string {
  const m = s.metrics.find((x) => x.key === HEADLINE[s.id])
  return m ? delta(m) : ''
}

function note(s: StepView): string {
  if (s.state === 'running') {
    if (state.run?.state === 'waiting_for_user' && state.run.prompt?.step === s.id) return 'Waiting for you to press Ready'
    const p = s.progress
    if (!p) return 'Running…'
    return p.phase === 'upload' && p.pct != null ? `Uploading firmware ${formatValue(Math.round(p.pct))}%` : (p.detail ?? `${p.phase}…`)
  }
  if (s.state === 'queued' && busy.value) return s.progress?.phase === 'compile' ? 'Compiling firmware…' : 'Queued'
  return s.summary ?? ''
}

const recent = computed(() => state.history.slice(0, 4))
const RECENT_CLASS: Record<string, string> = { pass: 'pass', warn: 'warn', fail: 'fail' }
</script>

<template>
  <nav class="rail" aria-label="Steps">
    <div class="eyebrow pad">Pipeline<template v-if="state.run && busy"> · run {{ clock(state.run.startedAt) }}</template></div>

    <div
      v-for="s in rows"
      :key="s.id"
      class="row"
      :class="{ selected: state.selected === s.id, queued: s.state === 'queued' && busy }"
    >
      <button type="button" class="pick" :aria-pressed="state.selected === s.id" @click="actions.select(s.id)">
        <StatusIcon :state="s.state" />
        <span class="text">
          <span class="title">
            {{ s.title }}
            <span v-if="s.needsUser" class="tag">needs you</span>
          </span>
          <span class="note" :class="{ warn: s.state === 'running' && state.run?.state === 'waiting_for_user', [s.state]: true }">
            {{ note(s) }} <span v-if="change(s) && !busy" class="delta">{{ change(s) }}</span>
          </span>
        </span>
        <span class="mono time">{{ s.state === 'running' ? '' : duration(s.duration) }}</span>
      </button>
      <button type="button" class="btn btn-small" :disabled="busy || !state.port" :aria-label="`Run ${s.title} only`" @click="actions.runOne(s.id)">Run</button>
    </div>

    <div class="spacer" />
    <template v-if="recent.length">
      <div class="eyebrow pad">Recent runs</div>
      <div class="mono recent">
        <div v-for="r in recent" :key="r.run_id">
          {{ clock(r.started_at) }}
          <span :class="RECENT_CLASS[r.status] ?? ''" class="status">{{ r.status.toUpperCase() }}</span>
          {{ Object.keys(r.steps).join(' ') }}
        </div>
      </div>
    </template>
  </nav>
</template>

<style scoped>
.rail {
  width: 400px; flex-shrink: 0; padding: 20px 16px 20px 20px; display: flex; flex-direction: column; gap: 8px;
  border-right: 1px solid var(--line); overflow-y: auto;
}
.pad { padding: 0 8px 4px; }
@media (max-width: 960px) {
  .rail { width: auto; border-right: 0; border-bottom: 1px solid var(--line); overflow: visible; }
  .spacer { display: none; }
}
.row {
  display: flex; align-items: center; gap: 8px; padding: 4px 10px 4px 4px; border-radius: 12px;
  border: 1px solid var(--line); background: var(--surface);
}
.row.selected { border: 2px solid var(--accent); padding: 3px 9px 3px 3px; }
.row.queued { background: transparent; border-style: dashed; border-color: var(--line-strong); }
.pick {
  flex-grow: 1; min-width: 0; display: flex; align-items: center; gap: 12px; padding: 8px;
  background: none; border: 0; border-radius: 10px; text-align: left; cursor: pointer;
}
.text { flex-grow: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.tag { white-space: nowrap; font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 6px; background: var(--accent-soft); color: var(--accent); }
.note { font-size: 13px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.note.fail { color: var(--fail); }
.note.warn { color: var(--warn); font-weight: 500; }
.delta { color: var(--pass); }
.time { font-size: 12px; color: var(--muted); white-space: nowrap; }
.spacer { flex-grow: 1; min-height: 16px; }
.recent { font-size: 12px; line-height: 1.9; padding: 0 8px; color: var(--muted); }
.status { font-weight: 500; }
</style>
