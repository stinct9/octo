<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { when } from '../lab/format'

interface PhoneStatus {
  enabled: boolean
  running: boolean
  error: string | null
  port: number
  urls: string[]
  ca_ready: boolean
  devices: { id: string; name: string; paired_at: number; last_used_at: number | null; away_at: number | null }[]
  tailscale: string[]
  listening: string[]
  ca_renewed_at: number | null
  away: Away
}
// octo/phone.py tailscale_status + away_step: what Tailscale on this Mac says, and the next single thing to do.
interface Away {
  installed: boolean
  running: boolean
  state: string | null
  ips: string[]
  name: string | null
  account: string | null
  phones: { name: string; online: boolean; last_seen: number | null }[]
  asked: boolean
  step: string
  do: string
  link: string | null
}

const st = ref<PhoneStatus | null>(null)
const error = ref('')
const busy = ref(false)
const code = ref<{ code: string; until: number } | null>(null)
const now = ref(Date.now())
const tick = setInterval(() => { now.value = Date.now() }, 500)
onUnmounted(() => clearInterval(tick))

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}
async function load() {
  try { st.value = await call<PhoneStatus>('GET', '/api/octo/phone'); error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
async function toggle(box: HTMLInputElement, on: boolean) {
  busy.value = true
  try { await call('POST', '/api/octo/phone', { enabled: on }); await load() } catch (e) {
    box.checked = !on  // not saved: the switch shows what's true (M20)
    error.value = e instanceof Error ? e.message : String(e)
  } finally { busy.value = false }
}
async function newCode() {
  const r = await call<{ code: string; expires_in: number }>('POST', '/api/octo/phone/code', {})
  code.value = { code: r.code, until: Date.now() + r.expires_in * 1000 }
}
async function unpair(id: string) {
  await call('POST', '/api/octo/phone/unpair', { id })
  await load()
}
// The app: a QR code (a one-time secret, 2 minutes). Showing it switches phone access on.
const qr = ref<{ svg: string; until: number; hosts: string[]; link: string } | null>(null)
// The same link as the code, for an iPhone that can't scan it: copied here, pasted in the app (Universal Clipboard).
const copied = ref(false)
async function copyLink() {
  if (!qr.value) return
  try { await navigator.clipboard.writeText(qr.value.link); copied.value = true; setTimeout(() => { copied.value = false }, 3000) } catch { /* no clipboard */ }
}
let watching: ReturnType<typeof setInterval> | undefined
async function showQr() {
  busy.value = true
  try {
    const before = st.value?.devices.length ?? 0
    const r = await call<{ svg: string; expires_in: number; hosts: string[]; link: string }>('POST', '/api/octo/phone/qr', {})
    qr.value = { svg: r.svg, until: Date.now() + r.expires_in * 1000, hosts: r.hosts, link: r.link }
    await load()
    // Until it's used or runs out: a new phone in the list means it worked.
    clearInterval(watching)
    watching = setInterval(async () => {
      await load()
      if ((st.value?.devices.length ?? 0) > before || qrLeft.value <= 0) { clearInterval(watching); qr.value = null }
    }, 2000)
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
onUnmounted(() => clearInterval(watching))
const qrLeft = computed(() => (qr.value ? Math.max(0, Math.round((qr.value.until - now.value) / 1000)) : 0))
const left = computed(() => (code.value ? Math.max(0, Math.round((code.value.until - now.value) / 1000)) : 0))
const shownCode = computed(() => (code.value && left.value > 0 ? `${code.value.code.slice(0, 4)} ${code.value.code.slice(4)}` : ''))
// The away-from-home check is live: Tailscale's state and the app's last contact, every 15 s while the page is open.
const every = setInterval(load, 15000)
onUnmounted(() => clearInterval(every))
const away = computed(() => st.value?.away)
const awayAt = computed(() => Math.max(0, ...(st.value?.devices ?? []).map((d) => d.away_at ?? 0)))
const STEPS = ['install_mac', 'open_mac', 'sign_in', 'approve', 'turn_on', 'starting', 'install_phone', 'pair', 'switch_on', 'scan_again', 'try_away', 'ready']
const past = (step: string) => !!away.value && STEPS.indexOf(away.value.step) > STEPS.indexOf(step)
const checklist = computed(() => [
  { label: 'Tailscale on this Mac', done: past('open_mac') },
  { label: 'Signed in and on', done: past('starting') },
  { label: 'Tailscale on your iPhone, same account', done: past('install_phone') },
  { label: 'The Octo app knows this Mac', done: past('scan_again') },
  { label: 'Reached away from home', done: away.value?.step === 'ready' },
])
const checking = ref(false)
async function checkAgain() { checking.value = true; try { await load() } finally { checking.value = false } }
onMounted(load)
</script>

<template>
  <main class="page">
    <div class="top">
      <div class="grow">
        <h1>Phone</h1>
        <p class="muted">Your lab in the Octo app on your iPhone: at home, and anywhere with Tailscale. This page is only on the Mac.</p>
      </div>
      <label v-if="st" class="switch">
        <span>Allow my phone</span>
        <input type="checkbox" role="switch" :checked="st.enabled" :disabled="busy" @change="toggle($event.target as HTMLInputElement, ($event.target as HTMLInputElement).checked)">
      </label>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!st" class="muted loading" role="status">Loading…</p>
    <template v-else>
      <p v-if="st.enabled && st.error && !st.running" class="error">The phone listener couldn't start: {{ st.error }}</p>
      <p v-else-if="st.enabled && st.running" class="ok">On: your phone can reach this Mac at
        <span class="mono">{{ st.listening.join(' and ') }}</span><span v-if="st.error" class="muted"> ({{ st.error }})</span></p>
      <p v-else class="muted">Off: nothing is reachable from the Wi-Fi or Tailscale.</p>

      <div class="grid">
        <section class="card pad" aria-labelledby="connect">
          <h2 id="connect">Connect the Octo app</h2>
          <p>In the app, open <b>Lab</b> and scan this code, or point the iPhone's Camera at it. Nothing to install on the
            phone and nothing to type: the code tells the app how to find this Mac and how to know it's this Mac.</p>
          <div v-if="qr && qrLeft > 0" class="qr" aria-live="polite">
            <div class="qr-img" role="img" aria-label="QR code for the Octo app" v-html="qr.svg"></div>
            <span class="muted small">Works once · {{ Math.floor(qrLeft / 60) }}:{{ String(qrLeft % 60).padStart(2, '0') }} left ·
              <button type="button" class="link small" @click="copyLink">{{ copied ? 'Copied: paste it in the app' : 'Copy the link' }}</button></span>
          </div>
          <button type="button" class="btn" :disabled="busy" @click="showQr">{{ qr && qrLeft > 0 ? 'New code' : 'Show the code' }}</button>
          <p class="muted small">Showing the code switches on "Allow my phone".</p>
        </section>

        <section v-if="away" class="card pad" aria-labelledby="away">
          <h2 id="away">Away from home</h2>
          <p class="muted small">Optional. With Tailscale, free for personal use, the app reaches this Mac anywhere while
            the Mac is awake. Without it, only on your home Wi-Fi.</p>
          <ol class="checklist">
            <li v-for="c in checklist" :key="c.label" :class="{ done: c.done }">
              <span class="tick" aria-hidden="true">{{ c.done ? '✓' : '○' }}</span>
              {{ c.label }}<span class="sr-only">{{ c.done ? ': done' : ': not yet' }}</span>
            </li>
          </ol>
          <div class="next" :class="{ ready: away.step === 'ready' }" aria-live="polite">
            <p><b v-if="away.step !== 'ready'">Next: </b>{{ away.do }}</p>
            <a v-if="away.link" class="btn" :href="away.link" target="_blank" rel="noopener">{{ away.step === 'install_mac' ? 'Get Tailscale for Mac' : away.step === 'install_phone' ? 'Get Tailscale for iPhone' : 'Open' }}</a>
          </div>
          <dl class="facts small">
            <template v-if="away.ips.length"><dt>This Mac</dt><dd><span class="mono">{{ away.ips[0] }}</span><span v-if="away.name"> · {{ away.name }}</span></dd></template>
            <template v-if="away.account"><dt>Account</dt><dd>{{ away.account }}</dd></template>
            <template v-for="p in away.phones" :key="p.name"><dt>{{ p.name }}</dt>
              <dd>{{ p.online ? 'in Tailscale now' : p.last_seen ? `Tailscale off, last on ${when(p.last_seen)}` : 'Tailscale off' }}</dd></template>
            <template v-if="away.ips.length || awayAt"><dt>The app away</dt><dd>{{ awayAt ? `last got through ${when(awayAt)}` : 'not yet' }}</dd></template>
          </dl>
          <button type="button" class="btn btn-small" :disabled="checking" @click="checkAgain">{{ checking ? 'Checking…' : 'Check again' }}</button>
          <details class="small">
            <summary>What Tailscale sees, and what it can't</summary>
            <p><b>Tailscale's servers see</b> the devices' names (<span v-if="away.name">{{ away.name }}</span><span v-else>this Mac's</span><span v-for="p in away.phones" :key="p.name">, {{ p.name }}</span>),
              the account you signed in with, their internet addresses and when they're online.</p>
            <p><b>Encrypted from end to end</b>, with keys that never leave the devices: everything the app and the lab say
              to each other, so your conversations, reviews, names and memos. Tailscale's relays carry only that encrypted
              traffic, and the lab's own HTTPS runs inside it, checked against this Mac's certificate.</p>
            <p class="muted">No Tailscale key is given to Octo and nothing goes through a server of ours. Octo only reads
              what Tailscale on this Mac says about itself.</p>
          </details>
          <p v-if="st.ca_renewed_at" class="warn small">This Mac's certificate was renewed {{ when(st.ca_renewed_at) }} so it
            can vouch for Tailscale. The app only needs the code again; Safari on a phone needs the new certificate
            (below).</p>
        </section>
      </div>

      <details class="card pad">
        <summary><b>Or use Safari</b> <span class="muted">(at home, with the certificate installed on the phone)</span></summary>
        <ol>
          <li><a href="/api/octo/phone/ca.crt" download>Download the certificate</a>, then AirDrop it from Downloads to your iPhone.</li>
          <li>iPhone: Settings → General → VPN &amp; Device Management → install it.</li>
          <li>iPhone: Settings → General → About → Certificate Trust Settings → turn it on.</li>
          <li>On the iPhone (same Wi-Fi), open <span class="mono">{{ st.urls[0] ?? '…' }}</span>, then type the code.</li>
        </ol>
        <div v-if="shownCode" class="code" aria-live="polite">
          <span class="mono big">{{ shownCode }}</span>
          <span class="muted small">Works once · {{ Math.floor(left / 60) }}:{{ String(left % 60).padStart(2, '0') }} left</span>
        </div>
        <button type="button" class="btn" :disabled="!st.running" @click="newCode">{{ shownCode ? 'New code' : 'Show a pairing code' }}</button>
        <p v-if="!st.running" class="muted small">Switch on "Allow my phone" first.</p>
      </details>

      <section class="card pad" aria-labelledby="dev">
        <h2 id="dev">Paired phones</h2>
        <p v-if="!st.devices.length" class="muted">None yet.</p>
        <div v-for="d in st.devices" :key="d.id" class="dev">
          <b>{{ d.name }}</b>
          <span class="muted small">Paired {{ when(d.paired_at) }}</span>
          <span class="muted small">Last used {{ d.last_used_at ? when(d.last_used_at) : 'never' }}</span>
          <button type="button" class="btn btn-small" @click="unpair(d.id)">Unpair</button>
        </div>
      </section>

      <p class="muted small">On your home Wi-Fi, and through Tailscale if it's on, while this Mac is awake. macOS may ask
        whether Python may accept incoming connections: that's this listener. The bench and settings stay on the Mac.</p>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 1040px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
.top { display: flex; align-items: center; gap: 16px; }
.grow { flex-grow: 1; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
ol { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.switch { display: flex; align-items: center; gap: 10px; font-weight: 500; min-height: 44px; }
.switch input { width: 44px; height: 24px; accent-color: var(--accent); }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.pad > * { max-width: 100%; }
.code { display: flex; flex-direction: column; gap: 4px; }
.qr { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.qr-img { background: #fff; border-radius: 12px; padding: 8px; line-height: 0; }
.qr-img :deep(svg) { width: 240px; height: 240px; }
.warn { color: var(--warn); }
.checklist { list-style: none; padding: 0; gap: 2px; }
.checklist li { color: var(--muted); }
.checklist li.done { color: var(--ink); }
.tick { display: inline-block; width: 1.4em; }
.checklist li.done .tick { color: var(--pass); }
.next { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; border-left: 3px solid var(--accent); padding-left: 12px; }
.next.ready { border-left-color: var(--pass); }
.next a.btn { text-decoration: none; }
.facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px 12px; margin: 0; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
details.small p { margin-top: 6px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
summary { cursor: pointer; min-height: 32px; }
details[open] summary { margin-bottom: 8px; }
.big { font-size: 34px; font-weight: 500; letter-spacing: 2px; }
.dev { align-self: stretch; display: grid; grid-template-columns: minmax(0, 1fr) 190px 190px auto; gap: 12px; align-items: center; border-top: 1px solid var(--line); padding-top: 10px; }
@media (max-width: 760px) { .grid { grid-template-columns: minmax(0, 1fr); } .dev { grid-template-columns: 1fr; } }
</style>
