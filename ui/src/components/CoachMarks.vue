<script setup lang="ts">
// The owner's marks on one part of a coach note: is it right, and was it new to them. Not the counts on Progress: they
// say whether the coach earns its place (docs/coach-plan.md §4.3). Pressing a chosen mark clears it.
import type { CoachMark } from '../lab/types'

const props = defineProps<{ mark?: CoachMark; label: string; saving?: boolean }>()
const emit = defineEmits<{ change: [m: { verdict?: CoachMark['verdict'] | ''; novelty?: CoachMark['novelty'] | '' }] }>()
const VERDICTS: [NonNullable<CoachMark['verdict']>, string][] = [['right', 'Right'], ['partly', 'Partly'], ['wrong', 'Not right']]
const NOVELTY: [NonNullable<CoachMark['novelty']>, string][] = [['new', 'New to me'], ['knew', 'I knew it']]
const verdict = (v: NonNullable<CoachMark['verdict']>) => emit('change', { verdict: props.mark?.verdict === v ? '' : v })
const novelty = (v: NonNullable<CoachMark['novelty']>) => emit('change', { novelty: props.mark?.novelty === v ? '' : v })
</script>

<template>
  <div class="marks">
    <div role="radiogroup" :aria-label="`Is it right: ${label}`" class="group">
      <button v-for="[v, text] in VERDICTS" :key="v" type="button" role="radio" :aria-checked="mark?.verdict === v"
              :disabled="saving" @click="verdict(v)">{{ text }}</button>
    </div>
    <div role="radiogroup" :aria-label="`New to you: ${label}`" class="group">
      <button v-for="[v, text] in NOVELTY" :key="v" type="button" role="radio" :aria-checked="mark?.novelty === v"
              :disabled="saving" @click="novelty(v)">{{ text }}</button>
    </div>
  </div>
</template>

<style scoped>
.marks { display: flex; flex-wrap: wrap; gap: 8px 14px; }
.group { display: inline-flex; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.group button { font: inherit; font-size: 13px; border: 0; border-right: 1px solid var(--line); background: var(--surface); color: var(--ink-2);
  padding: 6px 12px; min-height: 36px; cursor: pointer; }
.group button:last-child { border-right: 0; }
.group button[aria-checked='true'] { background: var(--accent-soft); color: var(--accent); font-weight: 500; }
.group button:disabled { cursor: wait; }
@media (max-width: 640px) {
  .marks { flex-direction: column; align-items: stretch; }
  .group { display: flex; }
  .group button { flex: 1; min-height: 44px; }
}
</style>
