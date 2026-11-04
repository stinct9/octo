<script setup lang="ts">
// Folds a home card to its header and one line, and opens it again (the owner, 4 Nov). Remembered per card in the
// lab, so every device folds it alike; everything starts open. The card owns `v-model` and hides its body when it's false.
import { watch } from 'vue'
import { pref } from '../lab/remembered'

const props = defineProps<{ name: string; label: string }>()
const open = defineModel<boolean>({ default: true })
const key = `octo:fold:${props.name}`
const saved = pref(key, true)
open.value = saved.value
watch(saved, (v) => { open.value = v })  // the lab's answer, or another device's choice
watch(open, (v) => { if (v !== saved.value) saved.value = v })
</script>

<template>
  <button type="button" class="fold" :aria-expanded="open" :aria-label="`${open ? 'Fold' : 'Open'} ${label}`"
          :title="open ? 'Fold' : 'Open'" @click="open = !open">
    <svg viewBox="0 0 16 16" aria-hidden="true" :class="{ shut: !open }"><path d="M3.5 6 8 10.5 12.5 6" /></svg>
  </button>
</template>

<style scoped>
.fold { flex: none; align-self: center; display: inline-grid; place-items: center; width: 32px; height: 32px; margin: -6px -8px -6px 0;
  border: 0; border-radius: 8px; background: none; color: var(--muted); cursor: pointer; }
.fold:hover { color: var(--ink); background: var(--line); }
svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform .15s; }
.shut { transform: rotate(-90deg); }
@media (max-width: 640px) { .fold { width: 44px; height: 44px; margin: -12px -12px -12px 0; } }
</style>
