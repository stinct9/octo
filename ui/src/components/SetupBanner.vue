<script setup lang="ts">
// A new lab's getting-started card on home, until its steps are done or skipped (product plan 4.2). Mac only:
// the steps are set on the Mac's pages.
import { onMounted, ref } from 'vue'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import FoldToggle from './FoldToggle.vue'

interface Setup { steps: { id: string; done: boolean; skipped?: boolean }[]; complete: boolean }
const LABELS: Record<string, string> = {
  mac: 'Finish what this Mac needs', models: 'Get the speech models', you: 'Say who you are', principles: 'Write your principles',
  model: 'Choose a model for reviews', iphone: 'Put the Octo app on your iPhone', pair: 'Connect the app to this Mac',
  away: 'Reach your lab away from home', voice: 'Let the lab learn your voice',
}
const setup = ref<Setup | null>(null)
const open = ref(true)
onMounted(() => {
  if (!onMac) return
  fetch('/api/octo/settings/setup').then((r) => (r.ok ? r.json() : null)).then((s) => { setup.value = s }).catch(() => {})
})
</script>

<template>
  <section v-if="setup && !setup.complete" class="card setup" aria-labelledby="setup-h">
    <div class="head">
      <h2 id="setup-h">Set up Octo</h2>
      <span class="muted small">{{ setup.steps.filter((s) => s.done).length }} of {{ setup.steps.length }} done</span>
      <FoldToggle v-model="open" name="setup" label="Set up Octo" />
    </div>
    <template v-if="open">
    <ol>
      <li v-for="s in setup.steps" :key="s.id" :class="{ done: s.done || s.skipped }">{{ LABELS[s.id] ?? s.id }}</li>
    </ol>
    <div><a class="btn btn-primary btn-small" :href="href.setup">Continue setting up</a></div>
    </template>
  </section>
</template>

<style scoped>
.setup { padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; border-color: var(--accent); }
.head { display: flex; align-items: baseline; gap: 12px; }
h2 { margin: 0; font-size: 17px; font-weight: 600; flex: 1; }
ol { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 3px; font-size: 14px; }
li.done { color: var(--muted); text-decoration: line-through; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
</style>
