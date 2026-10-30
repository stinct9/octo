<script setup lang="ts">
// The recorder in your pocket (docs/phone-plan.md): find it once, then it connects by itself whenever the app
// opens. Nothing here leaves the phone: it talks only to the recorder.
import { computed, onMounted, ref, watch } from 'vue'
import { App as CapApp } from '@capacitor/app'
import { Capacitor } from '@capacitor/core'
import { BleLink } from './ble'
import { FakeLink } from './fake'
import { Connection, MemoryStore } from './connection'
import { PrefsStore } from './store'
import FirstRun from './views/FirstRun.vue'
import RecorderView from './views/RecorderView.vue'
import LabTab from './views/LabTab.vue'
import MemosTab from './views/MemosTab.vue'
import { FakeLabNative, Lab, nativeLab } from './lab'
import { FakeMemosNative, Memos, isFile, nativeMemos } from './memos'
import { madeUp, setMadeUp } from './madeup'

const params = new URLSearchParams(location.search)
// In the app, "Try a made-up recorder" (madeup.ts): the browser's ?demo, from the first run, labelled throughout.
const madeUpHere = Capacitor.isNativePlatform() && madeUp()
const demo = (!Capacitor.isNativePlatform() && params.has('demo')) || madeUpHere
const fake = demo ? new FakeLink() : null
if (fake && params.has('slow')) fake.times.code = 8000
// ?demo starts as a phone that already knows its recorder; ?demo&new is the first run (and the app's made-up one).
const firstRun = params.has('new') || madeUpHere
const store = demo
  ? new MemoryStore(firstRun ? null : { id: 'fake', name: 'Octo 1A2B', fw: 'rec-15', boot: 45, seen: Date.now() - 3600000 })
  : new PrefsStore()
if (fake && !firstRun) fake.bonded = true
const c = new Connection(fake ?? new BleLink(), store)
const s = c.s

// Whether iOS has asked for Bluetooth yet (so the first-run line about it shows once).
const asked = ref(localStorage.getItem('octo.asked') === '1')
function markAsked() { asked.value = true; try { localStorage.setItem('octo.asked', '1') } catch { /* private mode */ } }

const known = computed(() => !!s.recorder && !s.replacing)

// ---- the Lab tab: the lab's own pages from the Mac ----
const native = Capacitor.isNativePlatform() && !demo
const labNative = demo ? new FakeLabNative() : nativeLab()
const lab = new Lab(labNative)
// Memos: recorded on this phone or shared from Voice Memos, sent to the Mac whenever the lab answers.
const memos = new Memos(demo ? new FakeMemosNative() : nativeMemos(), lab)
watch(() => lab.s.phase, (p) => {
  if (p !== 'ready') return
  void memos.send()
  if (memos.s.cue.on) void memos.fetchPhrases()  // B21: the list kept fresh for recording away from home
})
type Tab = 'recorder' | 'memos' | 'lab'
const saved = (() => { try { return localStorage.getItem('octo.tab') as Tab | null } catch { return null } })()
const tab = ref<Tab>(saved ?? 'recorder')
function go(t: Tab) {
  tab.value = t
  try { localStorage.setItem('octo.tab', t) } catch { /* private mode */ }
  if (t === 'lab' && (lab.s.phase === 'unreachable' || lab.s.phase === 'ready')) void lab.reach()
}
/** octo://pair?... from the iPhone's Camera app (or a scan). */
function opened(url: string | undefined) {
  if (url?.startsWith('octo://pair')) { go('lab'); void lab.pairWith(url) }
  else if (isFile(url)) { go('memos'); void memos.take(url) }  // Share → Octo from Voice Memos, or a call from Notes (B25)
}

function front() {
  void c.foreground()
  if (lab.s.cfg) void lab.reach()  // the Mac may have slept, or the phone changed networks
}

onMounted(async () => {
  void c.start()
  void memos.load()
  await lab.load()
  if (!saved && !s.recorder && lab.s.cfg) tab.value = 'lab'  // a lab and no recorder: open on the lab
  void lab.listen()
  void lab.onOpened(() => go('lab'))  // a tapped "review is ready" notification
  if (native) {
    void CapApp.addListener('appStateChange', ({ isActive }) => (isActive ? front() : c.background()))
    void CapApp.addListener('appUrlOpen', ({ url }) => opened(url))
    opened((await CapApp.getLaunchUrl())?.url)
  } else {
    document.addEventListener('visibilitychange', () => (document.hidden ? c.background() : front()))
  }
})

// ---- the demo's recorder, acted out ----
const sim = ref(false)
const act = (fn: () => unknown) => { sim.value = false; void fn() }
</script>

<template>
  <main class="app" :class="{ fill: tab === 'lab' }">
    <div v-if="madeUpHere" class="madeup" role="note">
      <span><b>Made-up</b> recorder and lab: nothing here is real.</span>
      <button type="button" class="link" @click="setMadeUp(false)">Leave</button>
    </div>
    <template v-if="tab === 'recorder'">
      <RecorderView v-if="known" :c="c" :memos="memos" :lab="lab" />
      <FirstRun v-else :c="c" :asked="asked" @asked="markAsked" />
    </template>
    <MemosTab v-if="tab === 'memos'" :memos="memos" :lab="lab" />
    <LabTab v-show="tab === 'lab'" :lab="lab" :active="tab === 'lab'" :native="native" />

    <div v-if="fake && tab === 'recorder'" class="demo">
      <button type="button" class="link" :aria-expanded="sim" @click="sim = !sim">Demo: act out the recorder</button>
      <div v-if="sim" class="demo-acts">
        <button type="button" class="btn" @click="act(() => fake!.setNearby(!fake!.nearby))">{{ fake.nearby ? 'Walk out of range' : 'Come back in range' }}</button>
        <button type="button" class="btn" @click="act(() => fake!.restart())">Reset it</button>
        <button type="button" class="btn" @click="act(() => fake!.update(`rec-${Number(fake!.fw.slice(4)) + 1}`))">Update from the Mac</button>
        <button type="button" class="btn" @click="act(() => fake!.update('rec-99', 'rolled_back'))">An update that goes back</button>
        <button type="button" class="btn" @click="act(() => fake!.setBluetooth(!fake!.btOn))">Phone's Bluetooth {{ fake.btOn ? 'off' : 'on' }}</button>
        <button type="button" class="btn" @click="act(() => { fake!.state = fake!.state ? null : 'card_full' })">{{ fake.state ? 'Clear the problem' : 'Card full' }}</button>
        <button type="button" class="btn" @click="act(() => { fake!.forgotPhone = true; fake!.setNearby(false); fake!.setNearby(true) })">It forgets this phone</button>
      </div>
      <p v-if="madeUpHere" class="hint">A made-up recorder: act it out with these.</p>
      <p v-else class="hint">A made-up recorder in the browser. ?demo&amp;new for the first run.</p>
    </div>
    <div v-if="fake && tab === 'lab'" class="demo">
      <div class="demo-acts">
        <button type="button" class="btn" @click="(labNative as FakeLabNative).reachable = !(labNative as FakeLabNative).reachable; lab.reach()">Mac {{ (labNative as FakeLabNative).reachable ? 'asleep' : 'awake' }}</button>
        <button type="button" class="btn" @click="(labNative as FakeLabNative).paired = false; lab.reach()">Unpaired on the Mac</button>
      </div>
    </div>
  </main>

  <nav class="tabs" aria-label="Octo">
    <button type="button" :aria-current="tab === 'recorder' ? 'page' : undefined" @click="go('recorder')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="12" rx="3" /><circle cx="12" cy="12" r="2.5" class="solid" /></svg>
      <span>Recorder</span>
    </button>
    <button type="button" :aria-current="tab === 'memos' ? 'page' : undefined" @click="go('memos')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
      <span>Memos</span>
    </button>
    <button type="button" :aria-current="tab === 'lab' ? 'page' : undefined" @click="go('lab')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3v-3H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" /><path d="M8 9h8M8 12h5" /></svg>
      <span>Lab</span>
    </button>
  </nav>
</template>

<style scoped>
.madeup { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; margin-bottom: 12px;
  border-radius: 12px; background: var(--warn-soft, var(--surface)); color: var(--ink); font-size: 0.88rem; }
.app { padding-bottom: calc(76px + env(safe-area-inset-bottom)); }
.app.fill { height: 100vh; height: 100dvh; min-height: 0; overflow: hidden; padding-bottom: calc(56px + env(safe-area-inset-bottom)); }
.tabs { position: fixed; left: 0; right: 0; bottom: 0; z-index: 10; display: flex; justify-content: center; gap: 0;
  padding: 4px 16px env(safe-area-inset-bottom); background: color-mix(in srgb, var(--ground) 88%, transparent);
  -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px); border-top: 1px solid var(--line); }
.tabs button { flex: 1; max-width: 200px; min-height: 49px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
  background: none; border: 0; color: var(--muted); font-size: 0.7rem; font-weight: 600; cursor: pointer; }
.tabs button[aria-current='page'] { color: var(--accent); }
.tabs svg { width: 26px; height: 26px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.tabs svg .solid { fill: currentColor; stroke: none; }
.demo { margin-top: auto; padding-top: 24px; border-top: 1px dashed var(--line-strong); display: flex; flex-direction: column; gap: 8px; }
.demo-acts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.demo-acts .btn { min-height: 44px; font-size: 0.8rem; padding: 0 10px; }
</style>
