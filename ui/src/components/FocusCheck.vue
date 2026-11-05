<script setup lang="ts">
// On a conversation that happened while a focus was active: did it come up here?
import { day, MEASURE_UNIT, measureValue, tallyText } from '../lab/format'
import type { FocusAnswer, SessionFocus } from '../lab/types'

/** focus.here: this conversation's own number, when the focus carries one. */
defineProps<{ focus: SessionFocus; saving: boolean }>()
const emit = defineEmits<{ answer: [a: FocusAnswer | null] }>()
// Short labels in one row, on every screen (review 5 Nov, items 6 and 10); a screen reader hears the whole answer.
const OPTIONS: [FocusAnswer, string, string][] = [['did', 'Yes, I did it', 'Did it'], ['missed', 'It came up, I missed it', 'Missed it'],
  ['na', "Didn't come up", "Didn't come up"]]
</script>

<template>
  <section class="fc" aria-labelledby="fc-h">
    <div class="eyebrow">Your focus · since {{ day(focus.started_at) }}<template v-if="tallyText(focus.tally)"> · {{ tallyText(focus.tally) }}</template></div>
    <h3 id="fc-h">{{ focus.text }}</h3>
    <p v-if="focus.measure && focus.here != null" class="small num">Here: <b>{{ measureValue(focus.measure, focus.here) }}</b>
      {{ MEASURE_UNIT[focus.measure] }}<template v-if="focus.numbers?.before"> · {{ measureValue(focus.measure, focus.numbers.before.value) }}
      before the focus</template>. Your answer still decides.</p>
    <div role="radiogroup" aria-label="Did it come up in this conversation?" class="opts">
      <span class="muted small q">In this conversation?</span>
      <button v-for="[a, label, short] in OPTIONS" :key="a" type="button" role="radio" :aria-checked="focus.answer === a" :disabled="saving"
              :aria-label="label" @click="emit('answer', focus.answer === a ? null : a)">{{ short }}</button>
    </div>
    <p v-if="focus.answer === 'did' && focus.item" class="muted small">The review item it came from is marked acted on.</p>
  </section>
</template>

<style scoped>
.fc { border: 1.5px solid var(--accent); border-radius: 12px; padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; background: var(--accent-soft); }
.eyebrow { font-size: 13px; color: var(--accent); font-weight: 500; }
h3 { margin: 0; font-size: 16px; font-weight: 600; }
p { margin: 0; }
.opts { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.opts button { white-space: nowrap; }
.q { margin-right: 4px; }
.opts button { font: inherit; font-size: 14px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 6px 14px; min-height: 40px; border-radius: 10px; cursor: pointer; }
.opts button[aria-checked='true'] { border-color: var(--accent); color: var(--accent); font-weight: 500; }
.small { font-size: 13px; }
.num { color: var(--ink-2); }
.muted { color: var(--muted); }
@media (max-width: 640px) {
  .opts button { flex: 1 1 auto; padding: 6px 8px; min-height: 44px; font-size: 13px; }
  .q { flex-basis: 100%; }
}
</style>
