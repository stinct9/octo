<script setup lang="ts">
// Set up Octo's iPhone step (octo/iphone.py): Xcode, an Apple ID, the phone, Developer Mode, the install, trusting it.
// The lab checks each part on this Mac and shows the next only when it's done; nothing here is ticked by hand.
import { computed, onMounted, onUnmounted, ref } from 'vue'

interface Part { id: string; title: string; do: string; done: boolean }
interface Job { running: boolean; lines: string[]; error: string | null; done_at: number | null }
interface State { steps: Part[]; job: Job; installed: { phone: string; expires: number | null } | null; store: string }

const emit = defineEmits<{ changed: [] }>()
const st = ref<State | null>(null)
const checking = ref(false)
const trustNote = ref('')
const next = computed(() => st.value?.steps.find((p) => !p.done) ?? null)
const until = computed(() => st.value?.installed?.expires
  ? new Date(st.value.installed.expires * 1000).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) : '')

let timer: number | undefined
async function load() {
  checking.value = true
  try {
    const r = await fetch('/api/octo/settings/iphone')
    if (r.ok) st.value = await r.json()
  } catch { /* checked again on the next round */ }
  checking.value = false
  if (!next.value) emit('changed')
  window.clearTimeout(timer)
  timer = window.setTimeout(load, st.value?.job.running ? 2000 : 6000)  // the phone and Xcode change outside this page
}
async function post(path: string) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  return r.ok ? r.json() : null
}
async function install() { await post('/api/octo/settings/iphone/install'); load() }
async function checkTrust() {
  trustNote.value = ''
  const r = await post('/api/octo/settings/iphone/check')
  if (r && !r.ok) trustNote.value = `Not yet: ${r.why}.`
  load()
}
onMounted(load)
onUnmounted(() => window.clearTimeout(timer))
</script>

<template>
  <div class="iphone">
    <p class="muted">The Octo app records on your iPhone and shows your lab there. Octo puts it on your phone from this Mac,
      signed with your own Apple ID, free. Each part below is checked here; the next appears once it's done.</p>
    <ol v-if="st" class="parts">
      <li v-for="p in st.steps" :key="p.id" :class="{ done: p.done, now: p.id === next?.id }">
        <span class="mark" aria-hidden="true">{{ p.done ? '✓' : p.id === next?.id ? '→' : '·' }}</span>
        <div>
          <b>{{ p.title }}</b>
          <template v-if="p.id === next?.id">
            <p>{{ p.do }}</p>
            <div class="line">
              <a v-if="p.id === 'xcode'" class="btn btn-primary" :href="st.store">Open Xcode in the App Store</a>
              <button v-if="p.id === 'install'" type="button" class="btn btn-primary" :disabled="st.job.running" @click="install">
                {{ st.job.running ? 'Putting Octo on your iPhone…' : 'Put Octo on my iPhone' }}</button>
              <button v-else-if="p.id === 'trust'" type="button" class="btn btn-primary" @click="checkTrust">Check again</button>
              <span v-else-if="checking" class="muted small" aria-live="polite">Checking…</span>
              <span v-else class="muted small">This page checks again by itself.</span>
            </div>
            <ul v-if="p.id === 'install' && (st.job.running || st.job.lines.length)" class="log small" aria-live="polite">
              <li v-for="(l, i) in st.job.lines" :key="i">{{ l }}</li></ul>
            <p v-if="p.id === 'install' && st.job.error" class="error small" role="alert">{{ st.job.error }}</p>
            <p v-if="trustNote" class="error small" role="alert">{{ trustNote }}</p>
          </template>
        </div>
      </li>
    </ol>
    <p v-else class="muted small">Checking this Mac…</p>
    <p v-if="st && !next" class="ok">Octo is on your iPhone. It renews itself every week while your iPhone is near this Mac
      (plugged in or on the same Wi-Fi)<template v-if="until">; this install opens until {{ until }}</template>.</p>
  </div>
</template>

<style scoped>
.iphone { display: flex; flex-direction: column; gap: 10px; }
p { margin: 0; }
.parts { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.parts > li { display: flex; gap: 10px; align-items: baseline; }
.parts > li > div { display: flex; flex-direction: column; gap: 6px; }
.parts .mark { width: 16px; flex-shrink: 0; text-align: center; font-weight: 600; }
.parts .done { color: var(--muted); }
.parts .done .mark { color: var(--pass); }
.parts .now .mark { color: var(--accent); }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.log { margin: 0; padding-left: 18px; color: var(--muted); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
</style>
