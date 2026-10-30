<script setup lang="ts">
// The beta's results file (octo/beta.py, product plan 4.8): counts only. Shown here first, so the tester
// sees exactly what they'd send; saving it is theirs, and so is sending it.
import { ref } from 'vue'

const results = ref<Record<string, unknown> | null>(null)
const error = ref('')
async function show() {
  try {
    const r = await fetch('/api/octo/beta')
    results.value = await r.json()
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
function save() {
  const blob = new Blob([JSON.stringify(results.value, null, 2) + '\n'], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `octo-beta-results-${results.value?.made_on ?? 'today'}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <section class="card pad" aria-labelledby="beta-h">
    <h2 id="beta-h">For the beta</h2>
    <p class="muted">If you're trying Octo before it's sold: the results file is counts only (how many conversations, how many
      reviews told you something new, steps you acted on). No names, no words from any conversation. Look at it, then send it.</p>
    <div v-if="!results"><button type="button" class="btn btn-small" @click="show">Show the results file</button></div>
    <template v-else>
      <pre class="file">{{ JSON.stringify(results, null, 2) }}</pre>
      <div><button type="button" class="btn btn-small" @click="save">Save as a file</button></div>
    </template>
    <p v-if="error" class="error small">{{ error }}</p>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); }
.file { margin: 0; padding: 12px; border-radius: 10px; background: var(--ground); font-size: 12px; overflow-x: auto; max-height: 320px; }
</style>
