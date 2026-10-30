<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { deviceLabel, when } from '../lab/format'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { Device } from '../lab/types'

// The recorder in the header (plan §8: a chip, not a panel). Hidden until one is set up.
const d = ref<Device | null>(null)
const open = ref(false)
const busy = ref(false)
const error = ref('')
const label = computed(() => (d.value ? deviceLabel(d.value) : null))

async function load() {
  try { d.value = await lab.device() } catch { /* the chip just stays as it was */ }
}
async function setOn(box: HTMLInputElement, on: boolean) {
  busy.value = true
  error.value = ''
  try { d.value = await lab.setRecording(on) } catch (e) {
    box.checked = !on  // not changed: the switch shows what the recorder is doing (M20)
    error.value = `${on ? 'Still off' : 'Still recording'}: ${e instanceof Error ? e.message : e}`
    void load()
  } finally { busy.value = false }
}
const timer = setInterval(load, 30_000)
onMounted(load)
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div v-if="d && label" class="wrap">
    <button type="button" class="chip" :class="label.tone" :aria-expanded="open" @click="open = !open">
      <span class="dot" aria-hidden="true" /><span class="who">Recorder</span>{{ label.text }}
    </button>
    <div v-if="open" class="pop card" role="dialog" aria-label="Recorder">
      <label class="switch">
        <span>Recording</span>
        <input type="checkbox" role="switch" :checked="!!d.want_on" :disabled="busy || !label.home"
               @change="setOn($event.target as HTMLInputElement, ($event.target as HTMLInputElement).checked)">
      </label>
      <p v-if="!label.home" class="muted small">Only while it's on the home Wi-Fi. Away from home, use the
        recorder's button (the expansion board's, or B beside USB-C): hold 1 s for recording on/off. To power it off, unplug it.</p>
      <dl class="small">
        <dt>Last seen</dt><dd>{{ when(d.seen_at) }}</dd>
        <template v-if="d.card_hours_left !== null"><dt>Card</dt><dd>{{ Math.floor(d.card_hours_left) }} h of recording left</dd></template>
        <template v-if="d.battery_mv"><dt>Battery</dt><dd>{{ (d.battery_mv / 1000).toFixed(2) }} V</dd></template>
        <dt>On this Mac</dt><dd>{{ d.syncing ? 'pulling audio now…' : d.waiting_on_mac
          ? `${Math.round(d.waiting_on_mac)} min ${label.home ? 'waiting for the conversation to end' : 'waiting; joined after an hour without the recorder'}`
          : 'up to date' }}</dd>
        <template v-if="d.sd_failures"><dt>Card errors</dt><dd>{{ d.sd_failures }} since it started (recovered)</dd></template>
        <template v-if="d.dropped_samples"><dt>Audio lost</dt><dd>{{ (d.dropped_samples / 16000).toFixed(1) }} s</dd></template>
      </dl>
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
      <a v-if="onMac" class="small" :href="href.settings" @click="open = false">Recorder settings</a>
    </div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.chip { display: inline-flex; align-items: center; gap: 8px; padding: 5px 12px; border: 1px solid var(--line); border-radius: 999px;
  background: var(--surface); color: var(--ink-2); font: inherit; font-size: 13px; white-space: nowrap; cursor: pointer; min-height: 32px; }
.who { color: var(--muted); }
@media (max-width: 600px) { .who { display: none; } }  /* the header's width at phone size (review 4 Oct) */
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); }
.chip.rec .dot { background: var(--fail); }
.chip.ok .dot { background: var(--pass); }
.chip.fail { border-color: var(--fail); color: var(--fail); }
.chip.warn { border-color: var(--warn); color: var(--warn); }
.chip.warn .dot { background: var(--warn); }
.chip.fail .dot { background: var(--fail); }
.pop { position: absolute; right: 0; top: calc(100% + 8px); z-index: 10; width: 300px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.switch { display: flex; align-items: center; justify-content: space-between; font-weight: 500; min-height: 40px; }
.switch input { width: 44px; height: 24px; accent-color: var(--accent); }
dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
dt { color: var(--muted); }
dd { margin: 0; }
p { margin: 0; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
</style>
