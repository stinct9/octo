<script setup lang="ts">
// Held / partly / missed / depends on a principle: a shape and a word, never colour alone, and no
// red or green (plan A4: the owner's conduct isn't scored like a test).
import { HELD_LABEL } from '../lab/review'
import type { Held } from '../lab/types'

defineProps<{ held: Held; bare?: boolean; icon?: boolean }>()
</script>

<template>
  <span class="held" :class="{ bare }" :title="`${HELD_LABEL[held]}, against the principle's own words`">
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle v-if="held === 'held'" cx="6" cy="6" r="5" fill="currentColor" />
      <template v-else-if="held === 'partly'">
        <circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.3" />
        <path d="M6 1.6a4.4 4.4 0 0 1 0 8.8z" fill="currentColor" />
      </template>
      <circle v-else-if="held === 'missed'" cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.3" />
      <circle v-else cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-dasharray="2 1.6" />
    </svg><template v-if="!icon">{{ HELD_LABEL[held] }}</template>
  </span>
</template>

<style scoped>
.held { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 500; color: var(--ink-2);
  border: 1px solid var(--line-strong); border-radius: 999px; padding: 2px 9px 2px 7px; white-space: nowrap; }
.held.bare { border: 0; padding: 0; }
</style>
