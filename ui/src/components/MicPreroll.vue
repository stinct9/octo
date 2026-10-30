<script setup lang="ts">
import { computed } from 'vue'
import { formatValue } from '../lib/format'
import { actions, state } from '../store'

const METER_MIN = -80
const METER_MAX = 0
const level = computed(() => state.run?.level ?? null)
const fill = computed(() =>
  level.value === null ? 0 : Math.min(100, Math.max(0, ((level.value - METER_MIN) / (METER_MAX - METER_MIN)) * 100)),
)
const selfTest = computed(() => state.run?.steps.find((s) => s.id === 'mic')?.logs.find((l) => l.line.startsWith('self-test ok')))
</script>

<template>
  <section class="preroll" aria-label="Microphone: your turn">
    <div class="intro">
      <div class="eyebrow accent">Microphone · your turn</div>
      <h2>Sit about a metre from the board</h2>
      <p>Press Ready. Stay silent until you hear the <strong>Glass</strong> sound, then talk normally until the <strong>Hero</strong> sound. Ten seconds in all.</p>
    </div>

    <div class="card meter-card">
      <div class="meter-head">
        <span class="muted">Live level</span>
        <span class="spacer" />
        <span class="mono reading" aria-live="off">{{ level === null ? '—' : formatValue(level) }}</span>
        <span class="muted">dBFS</span>
      </div>
      <div class="meter" role="meter" :aria-valuenow="level ?? undefined" :aria-valuemin="METER_MIN" :aria-valuemax="METER_MAX" aria-label="Microphone level">
        <div class="fill" :style="{ width: `${fill}%` }" />
        <div class="band" :style="{ left: '37.5%', width: '25%' }" />
      </div>
      <div class="mono scale"><span>−80</span><span>−60</span><span class="warn">speech ≈ −50 … −30</span><span>−20</span><span>0</span></div>
      <div v-if="selfTest" class="muted small">Self-test passed · {{ selfTest.line.replace('self-test ok: ', '') }} dBFS</div>
    </div>

    <div class="actions">
      <button type="button" class="btn btn-primary ready" @click="actions.ready">
        <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="2" width="6" height="10" rx="3" fill="none" stroke="currentColor" stroke-width="1.8" /><path d="M4 9.5a6 6 0 0 0 12 0M10 15.5V18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
        Ready — start recording
      </button>
      <span class="muted">The Mac plays the cues — volume up, not on headphones</span>
    </div>
  </section>
</template>

<style scoped>
.preroll { display: flex; flex-direction: column; gap: 24px; }
.intro { display: flex; flex-direction: column; gap: 10px; }
.accent { color: var(--accent); }
h2 { margin: 0; font-size: 32px; font-weight: 600; letter-spacing: -0.5px; }
p { margin: 0; font-size: 17px; line-height: 1.6; color: var(--ink-2); max-width: 640px; }
.meter-card { border-radius: 16px; padding: 22px 24px; display: flex; flex-direction: column; gap: 14px; }
.meter-head { display: flex; align-items: baseline; gap: 10px; }
.spacer { flex-grow: 1; }
.reading { font-size: 30px; font-weight: 500; }
.muted { color: var(--muted); font-size: 14px; }
.small { font-size: 13px; }
.meter { position: relative; height: 18px; border-radius: 9px; background: var(--chart-quiet); overflow: hidden; }
.fill { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 9px; transition: width 90ms linear; }
.band { position: absolute; top: 0; bottom: 0; border-left: 2px solid var(--warn); border-right: 2px solid var(--warn); opacity: 0.6; }
.scale { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); }
.actions { display: flex; align-items: center; gap: 16px; }
.ready { height: 60px; padding: 0 36px; border-radius: 14px; font-size: 19px; }
@media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
</style>
