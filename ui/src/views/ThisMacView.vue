<script setup lang="ts">
// This Mac (Jay, 22 Oct: split from Set up Octo): what the lab needs from this Mac, its notifications, and calls on it.
// Mac only, as Set up Octo. Help → Check This Mac in the Octo app opens it.
import { computed, onMounted, ref } from 'vue'
import { href } from '../lab/router'

interface Check { mark: '✓' | '!' | '✗'; what: string; detail: string; fix: string }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}

// The Octo app (macapp/Octo.swift) takes "calls" to ask macOS for the mic and System Audio Recording
const octoApp = (window as unknown as { webkit?: { messageHandlers?: { octo?: { postMessage: (m: string) => void } } } })
  .webkit?.messageHandlers?.octo

// This Mac: `octo doctor`'s checks, in the page (open-source plan §4 step 1)
const checks = ref<Check[] | null>(null)
const needs = computed(() => (checks.value ?? []).filter((c) => c.mark !== '✓').sort((a, b) => Number(b.mark === '✗') - Number(a.mark === '✗')))
const fine = computed(() => (checks.value ?? []).filter((c) => c.mark === '✓'))
const loadChecks = () => call<Check[]>('GET', '/api/octo/settings/mac').then((c) => { checks.value = c }).catch(() => {})
const notified = ref('')  // how it went: 'app' (posted by the Octo app), 'osascript', 'off'
const notifyError = ref('')
async function testNotification() {
  notifyError.value = ''
  try { notified.value = (await call<{ via: string }>('POST', '/api/octo/settings/notify-test', {})).via || 'osascript' } catch (e) { notifyError.value = e instanceof Error ? e.message : String(e) }
}

// B21: the live filler cue on this Mac's calls (the Octo app reads it when a call starts); the phone has its own switch
const liveCue = ref<{ mac: boolean; phrases: string[] } | null>(null)
const cueError = ref('')
async function setLiveCue(mac: boolean) {
  cueError.value = ''
  try { liveCue.value = await call('POST', '/api/octo/fillers/live', { mac }) } catch (e) { cueError.value = e instanceof Error ? e.message : String(e) }
}

onMounted(() => {
  loadChecks()
  call<{ mac: boolean; phrases: string[] }>('GET', '/api/octo/fillers/live').then((c) => { liveCue.value = c }).catch(() => {})
})
</script>

<template>
  <main class="page">
    <div>
      <h1>This Mac</h1>
      <p class="muted">What the lab needs from this Mac, and calls on it. Getting started is on <a :href="href.setup">Set up Octo</a>.</p>
    </div>

    <section class="card pad" aria-labelledby="s-mac">
      <div class="row-head"><h2 id="s-mac">Checks</h2>
        <span v-if="checks && !needs.length" class="ok small">All {{ fine.length }} checks fine</span>
        <button v-else-if="checks" type="button" class="btn btn-small" @click="loadChecks">Check again</button></div>
      <p class="muted">What the lab needs from this Mac, and what macOS may refuse without saying. The lab works while the
        Mac is awake; the recorder and the phone keep what they record until it is.</p>
      <ul v-if="needs.length" class="checks">
        <li v-for="c in needs" :key="c.what" :class="c.mark === '✗' ? 'is-bad' : 'is-warn'">
          <span class="mark" aria-hidden="true">{{ c.mark }}</span>
          <span><b>{{ c.what }}</b>{{ c.detail ? `: ${c.detail}` : '' }}<span v-if="c.fix" class="fix">{{ c.fix }}</span></span>
        </li>
      </ul>
      <details v-if="fine.length" class="small"><summary class="muted">{{ fine.length }} fine</summary>
        <ul class="checks"><li v-for="c in fine" :key="c.what"><span class="mark ok" aria-hidden="true">✓</span>
          <span><b>{{ c.what }}</b>{{ c.detail ? `: ${c.detail}` : '' }}</span></li></ul>
      </details>
      <div class="line">
        <button type="button" class="btn" @click="testNotification">Send a test notification</button>
        <span v-if="notifyError" class="error small" role="alert">{{ notifyError }}</span>
        <span v-else-if="notified === 'off'" class="muted small" aria-live="polite">Octo's notifications are turned off:
          System Settings → Notifications → Octo → Allow notifications.</span>
        <span v-else-if="notified" class="muted small" aria-live="polite">Sent. Didn't see it? System Settings → Notifications →
          {{ notified === 'app' ? 'Octo' : 'Script Editor' }} → Allow notifications. Octo's notifications are counts only,
          never what was said.</span>
      </div>
    </section>

    <!-- B19: calls on this Mac, recorded by the Octo app (macapp/Octo.swift); the rule is the owner's, as with the recorder -->
    <section class="card pad" aria-labelledby="s-calls">
      <div class="row-head"><h2 id="s-calls">Calls on this Mac</h2></div>
      <p><strong>Tell the others on the call that you're recording it.</strong> Octo doesn't announce itself on the call:
        that's yours to do, every time, as with the recorder on the table.</p>
      <p class="muted">When Zoom, Teams, FaceTime or a call in your browser starts using the mic, the Octo app offers to record
        it; it never starts by itself. It records your mic and the call's sound side by side, and the call goes through the
        lab like a memo, with you already known as the one on the mic. Headphones give the cleanest result; on speakers the
        lab takes the others out of your mic after the call. The audio stays on this Mac.</p>
      <div v-if="octoApp" class="line"><button type="button" class="btn" @click="octoApp.postMessage('calls')">Allow call recording</button>
        <span class="muted small">macOS asks once for the microphone and once for System Audio Recording.</span></div>
      <p v-else class="muted small">To allow it, open this page in the Octo app (Help → Check This Mac): it needs your microphone
        and System Audio Recording, asked by macOS once each.</p>
      <label class="line"><input type="checkbox" :checked="!!liveCue?.mac" :disabled="!liveCue"
             @change="setLiveCue(($event.target as HTMLInputElement).checked)"> Show my fillers in the menu bar while a call records</label>
      <p class="muted small">From your mic only, never the others': each filler on your list
        ({{ liveCue?.phrases.length ? liveCue.phrases.join(', ') : 'none yet' }}; <a :href="href.fillers">your fillers →</a>) shows beside the octopus for a
        moment, then the count for the call. macOS 26 or later hears it on this Mac with its own speech recognition, and Octo
        won't run it any other way. What it hears is only counted, never kept or sent; the lab's transcript stays the record.
        On your own pitch memos it caught 53 of the 54 “you know”s the lab's transcript has, and it hears more “uh” and
        “um” than the transcript keeps.</p>
      <p v-if="cueError" class="error small" role="alert">{{ cueError }}</p>
    </section>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.checks li { display: flex; gap: 10px; align-items: baseline; }
.checks .mark { width: 16px; flex-shrink: 0; text-align: center; font-weight: 600; }
.checks .is-bad .mark { color: var(--fail); }
.checks .is-warn .mark { color: var(--warn); }
.checks .fix { display: block; color: var(--muted); font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
details summary { cursor: pointer; }
details .checks { margin-top: 8px; }
@media (max-width: 640px) { .page { padding: 20px 16px 40px; } }
</style>
