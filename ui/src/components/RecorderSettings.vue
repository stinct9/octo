<script setup lang="ts">
// The recorder (product plan 4.4): pair it with this Mac over USB, and its Wi-Fi networks (up to five,
// tried newest first). Passwords go in and never come back. The phone app changes networks too.
import { computed, onMounted, ref } from 'vue'
import { href } from '../lab/router'

interface Recorder { paired: boolean; developer_build: boolean; networks: string[] | null; error: string | null }
/** The filler cue (B20, rec-27), as the recorder last said; managed on the Fillers page. */
interface Filler { model: boolean | null; on: boolean | null; name?: string | null; loop?: { target?: string } }

const rec = ref<Recorder | null>(null)
const busy = ref(false)
const error = ref('')
const note = ref('')
const ssid = ref('')
const password = ref('')

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}
async function act(fn: () => Promise<void>, done: string) {
  busy.value = true
  error.value = ''
  note.value = ''
  try { await fn(); note.value = done } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
const load = () => call<Recorder>('GET', '/api/octo/settings/recorder').then((r) => { rec.value = r }).catch((e) => { error.value = String(e) })
const filler = ref<Filler | null>(null)
onMounted(() => { load(); call<Filler>('GET', '/api/octo/settings/recorder/filler').then((f) => { filler.value = f }).catch(() => {}) })
const cue = computed(() => filler.value?.loop?.target ?? filler.value?.name ?? 'you know')

const pair = () => act(async () => {
  const r = await call<{ networks: string[] }>('POST', '/api/octo/settings/recorder/pair', { ssid: ssid.value, password: password.value })
  rec.value = { ...(rec.value ?? { developer_build: false, error: null }), paired: true, networks: r.networks, error: null }
  ssid.value = ''
  password.value = ''
}, 'Paired with this Mac.')
const add = () => act(async () => {
  const r = await call<{ networks: string[] }>('POST', '/api/octo/settings/recorder/networks', { op: 'add', ssid: ssid.value, password: password.value })
  if (rec.value) rec.value.networks = r.networks
  ssid.value = ''
  password.value = ''
}, 'Network saved on the recorder.')
const remove = (name: string) => act(async () => {
  const r = await call<{ networks: string[] }>('POST', '/api/octo/settings/recorder/networks', { op: 'del', ssid: name })
  if (rec.value) rec.value.networks = r.networks
}, `${name} removed from the recorder.`)
</script>

<template>
  <section class="card pad" aria-labelledby="rec-h">
    <div class="row-head">
      <h2 id="rec-h">Recorder</h2>
      <span v-if="rec?.paired" class="ok small">Paired with this Mac</span>
    </div>
    <p class="muted">The recorder keeps up to five Wi-Fi networks and tries the newest first. It records without any network and
      sends what it recorded when it's next on one this Mac is on. You can also change networks from the phone app.</p>
    <p v-if="rec?.developer_build" class="muted small">This Mac builds the recorder with firmware/recorder/secrets.h: a recorder
      built from it also uses that file's network and key.</p>

    <p v-if="!rec && !error" class="muted loading" role="status">Loading…</p>
    <template v-else-if="rec && !rec.paired">
      <p>Plug the recorder into this Mac with a USB data cable, then pair it. It shows "Set me up" until it's paired, and doesn't
        record before then.</p>
    </template>
    <template v-else-if="rec">
      <p v-if="rec.error" class="muted small">{{ rec.error }}</p>
      <ul v-if="rec.networks?.length" class="nets">
        <li v-for="(n, i) in rec.networks" :key="n"><span>{{ n }}<span v-if="i === 0" class="muted small"> · tried first</span></span>
          <button type="button" class="btn btn-small" :disabled="busy" @click="remove(n)">Remove</button></li>
      </ul>
      <p v-else-if="rec.networks" class="muted small">No networks yet.</p>
    </template>

    <form v-if="rec" class="line" @submit.prevent="rec.paired ? add() : pair()">
      <label class="field grow">Wi-Fi network (2.4 GHz)<input v-model="ssid" type="text" autocomplete="off" spellcheck="false" :disabled="busy"></label>
      <label class="field grow">Password<input v-model="password" type="password" autocomplete="off" :disabled="busy" placeholder="blank if open"></label>
      <button type="submit" class="btn" :disabled="busy || (rec.paired && !ssid.trim())">
        {{ busy ? 'Working…' : rec.paired ? 'Add network' : 'Pair the recorder on USB' }}</button>
    </form>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <p v-else-if="note" class="ok small" aria-live="polite">{{ note }}</p>
    <p v-if="rec?.paired && filler?.model" class="cue small">Filler cue: “{{ cue }}”, {{ filler.on ? 'on' : 'off' }} ·
      <a :href="href.fillers">Manage →</a></p>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.nets { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.nets li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 10px; }
.line { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.cue { border-top: 1px solid var(--line); padding-top: 12px; }
.cue a { color: var(--accent); display: inline-flex; align-items: center; min-height: 32px; }
@media (max-width: 640px) { .cue a { min-height: 44px; } }
.grow { flex: 1; min-width: 180px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.field input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
</style>
