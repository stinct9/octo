<script setup lang="ts">
// One step of Set up Octo (beta, 31 Oct): open while it's the one to do; done ones fold to a line, later ones wait.
// Done is the lab's check, never a box ticked here; optional steps can be skipped and taken back.
import { ref, watch } from 'vue'

// note: done, but what showed it went stale; shown folded too, so the tick never goes silently
const props = defineProps<{ n: number; title: string; state: 'done' | 'now' | 'later' | 'skipped'; optional?: boolean; note?: string | null }>()
const emit = defineEmits<{ skip: [boolean] }>()
const open = ref(props.state === 'now')
watch(() => props.state, (s) => { open.value = s === 'now' })
const label = { done: 'Done', now: 'Now', later: 'Later', skipped: 'Skipped' }
</script>

<template>
  <section class="card pad step" :class="`is-${state}`" :aria-labelledby="`step-${n}`">
    <div class="row-head">
      <h2 :id="`step-${n}`"><span class="n" aria-hidden="true">{{ state === 'done' ? '✓' : n }}</span>{{ title }}</h2>
      <span class="small tag">{{ label[state] }}<template v-if="optional && state !== 'done'"> · optional</template>
        <button v-if="state !== 'now'" type="button" class="btn btn-small link" :aria-expanded="open" @click="open = !open">
          {{ open ? 'Hide' : 'Show' }}</button></span>
    </div>
    <p v-if="note" class="small note">{{ note }}</p>
    <template v-if="open">
      <slot />
      <div v-if="optional && state === 'now'" class="line"><button type="button" class="btn btn-small" @click="emit('skip', true)">Skip for now</button></div>
      <div v-if="state === 'skipped'" class="line"><button type="button" class="btn btn-small" @click="emit('skip', false)">Do this step</button></div>
    </template>
  </section>
</template>

<style scoped>
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h2 { margin: 0; font-size: 17px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
.n { width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--line-strong); font-size: 13px; color: var(--ink-2); flex-shrink: 0; }
.small { font-size: 13px; }
.tag { color: var(--muted); display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.link { background: none; border: none; color: var(--accent); padding: 0 4px; }
.note { margin: 0; color: var(--warn); }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.is-now { border-color: var(--accent); }
.is-done .n { color: var(--pass); border-color: var(--pass); }
.is-done h2, .is-later h2, .is-skipped h2 { color: var(--muted); }
</style>
