<script setup lang="ts">
// Home: what you're practising, how it's going, and a way to change it.
import { onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { day, MEASURE_LABEL, measureValue, tallyText } from '../lab/format'
import type { FocusSummary, Measure } from '../lab/types'
import FocusEditor from './FocusEditor.vue'
import FoldToggle from './FoldToggle.vue'

const f = ref<FocusSummary | null>(null)
const editing = ref(false)
const open = ref(true)
const saving = ref(false)
const error = ref('')
onMounted(async () => { try { f.value = await lab.focus() } catch { /* the list still works */ } })

async function save(text: string, measure: Measure | null) {
  saving.value = true
  try { f.value = await lab.startFocus(text, undefined, measure); editing.value = false; error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { saving.value = false }
}
async function stop() {
  try { f.value = await lab.stopFocus() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
</script>

<template>
  <section v-if="f" class="card focus" aria-labelledby="focus-now">
    <template v-if="f.active">
      <div class="grow">
        <div class="eyebrow" id="focus-now">Your focus · since {{ day(f.active.started_at) }}<template v-if="tallyText(f.active.tally)"> · {{ tallyText(f.active.tally) }}</template></div>
        <div class="text">{{ f.active.text }}</div>
        <div v-if="open && f.active.measure" class="num small">{{ MEASURE_LABEL[f.active.measure] }}:
          <template v-if="f.active.numbers?.since"><b>{{ measureValue(f.active.measure, f.active.numbers.since.value) }}</b> since then
            ({{ f.active.numbers.since.conversations }} conversation{{ f.active.numbers.since.conversations === 1 ? '' : 's' }})</template>
          <template v-else>no conversation {{ !['questions', 'talk_share', 'talked_over'].includes(f.active.measure) ? 'on the line ' : '' }}since then yet</template><template
            v-if="f.active.numbers?.before"> · {{ measureValue(f.active.measure, f.active.numbers.before.value) }} before</template></div>
      </div>
      <template v-if="open">
        <button type="button" class="link small" @click="editing = true">Change</button>
        <button type="button" class="link small" @click="stop">Stop</button>
      </template>
    </template>
    <template v-else>
      <div class="grow">
        <div class="eyebrow" id="focus-now">No focus yet</div>
        <div v-if="open" class="muted small">In a review, choose <b>Practise this</b> on the one thing you'd not have seen yourself. Each conversation after it asks whether it came up.</div>
      </div>
      <button v-if="open" type="button" class="link small" @click="editing = true">Write one</button>
    </template>
    <FoldToggle v-model="open" name="focus" label="your focus" />
    <p v-if="error && !editing" class="error small" role="alert">{{ error }}</p>
    <FocusEditor :open="editing" initial="" :replaces="f.active?.text ?? null" :saving="saving" :error="error"
                 @save="save" @cancel="editing = false; error = ''" />
  </section>
</template>

<style scoped>
.focus { padding: 14px 18px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; border-left: 3px solid var(--accent); }
.grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.eyebrow { font-size: 13px; color: var(--accent); font-weight: 500; }
.text { font-size: 16px; font-weight: 600; }
.num { color: var(--ink-2); }
.link { border: 0; background: none; padding: 8px 4px; color: var(--accent); cursor: pointer; font: inherit; min-height: 40px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); margin: 0; flex-basis: 100%; }
</style>
