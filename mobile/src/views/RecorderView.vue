<script setup lang="ts">
// The recorder the phone knows (docs/phone/design-review.md §5.7): its screen, one plain line of status, one
// action, and rows. It reconnects by itself; an update shows as what it is (Updating, Restarting, Checking,
// Updated), mirroring the recorder's own screen.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import HoldButton from '../components/HoldButton.vue'
import Sheet from '../components/Sheet.vue'
import WifiSheet from './WifiSheet.vue'
import AboutSheet from './AboutSheet.vue'
import { appVersion, versionText } from '../version'
import type { Connection } from '../connection'
import type { Lab } from '../lab'
import type { Memos } from '../memos'
import MarkButton from '../components/MarkButton.vue'
import { NEEDS_YOU, STATE_TEXT, duration, roomText, signal } from '../recorder'
import * as S from '../scenes'
import { haptic } from '../haptics'
import type { Scene } from '../scenes'

const props = defineProps<{ c: Connection; memos: Memos; lab: Lab }>()
const s = props.c.s

const now = ref(Date.now())
const clock = setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(clock))

const connected = computed(() => s.phase === 'connected')
const st = computed(() => s.status)
const since = computed(() => now.value - s.since)
/** After 8 s without an answer (3 minutes after an update's restart) it isn't nearby, as far as the phone can tell. */
const away = computed(() => s.phase === 'reconnecting' && since.value > (s.why === 'update' ? 180000 : 8000))

// ---- moments: the first connection, start and stop, an update's end ----
const moment = ref<{ kind: string; at: number; fw?: string; from?: string } | null>(null)
const firstBanner = ref(false)
const said = ref('')  // the polite live region
watch(() => s.event?.n, () => {
  const e = s.event
  if (!e) return
  moment.value = { kind: e.kind, at: Date.now(), fw: e.fw, from: e.from }
  if (e.kind === 'connected_first') {
    setTimeout(() => haptic('success'), 200)
    firstBanner.value = true
    setTimeout(() => { firstBanner.value = false }, 6000)
    said.value = 'Connected'
  } else if (e.kind === 'updated') { setTimeout(() => haptic('success'), 500); said.value = `Updated to ${e.fw}` }
  else if (e.kind === 'rolled_back') { haptic('warning'); said.value = `Went back to ${e.fw}` }
  else if (e.kind === 'started') { haptic('success'); said.value = 'Recording started' }
  else if (e.kind === 'stopped') { haptic('light'); said.value = 'Recording stopped' }
  else if (e.kind === 'reconnected' && wasAway) { haptic('light'); said.value = 'Connected' }
})
let wasAway = false
watch(away, (a) => { if (a) wasAway = true })
watch(connected, (on) => { if (on) setTimeout(() => { wasAway = false }, 0) })
const momentAge = computed(() => (moment.value ? now.value - moment.value.at : Infinity))
const rolledBack = computed(() => moment.value?.kind === 'rolled_back')
const justUpdated = computed(() => moment.value?.kind === 'updated' && momentAge.value < 5000)
function dismiss() { moment.value = null }

// ---- what the panel, the headline and the line under it say ----
interface View { scene: Scene; caption?: string; head: string; sub: string; action: boolean; dim?: boolean }

const view = computed<View>(() => {
  const x = st.value
  const fw = x?.fw ?? ''
  if (s.phase === 'code') return { scene: S.code, caption: 'Your recorder shows:', head: 'Type the code from your recorder', sub: 'Your iPhone is asking again for the six digits on its screen. No code there? Tap its button once: it shows one for 10 minutes after a tap.', action: false }
  if (s.phase === 'reconnecting') {
    if (s.why === 'update' && !away.value) return { scene: S.updating(100), caption: 'Your recorder shows:', head: 'Restarting…', sub: 'It\'s starting the new version. This takes about half a minute.', action: false }
    const last = x ? `${STATE_TEXT[x.state] ?? 'Your recorder'}` : 'Your recorder'
    const when = s.recorder?.seen ? new Date(s.recorder.seen).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
    if (away.value) return { scene: sceneFor(x?.state), head: 'Can\'t see your recorder', sub: `If it's close, tap its button once. Octo connects as soon as it can see it.${when ? ` Last seen at ${when}: ${last.toLowerCase()}.` : ''}`, action: false, dim: true }
    return { scene: sceneFor(x?.state), head: last, sub: s.why === 'lost' ? 'Lost the connection. Reconnecting…' : when ? `As of ${when}. Connecting…` : 'Connecting…', action: false, dim: true }
  }
  if (s.phase === 'bluetooth_off') return { scene: sceneFor(x?.state), head: 'Bluetooth is off', sub: 'Turn it on in Control Centre, then come back. Octo reconnects by itself.', action: false, dim: true }
  if (s.phase === 'not_allowed') return { scene: S.alert, head: 'Bluetooth isn\'t allowed', sub: 'Octo can\'t see your recorder without it. Allow Bluetooth for Octo in Settings.', action: false, dim: true }
  if (s.phase === 'forgotten') return { scene: S.alert, head: 'Your recorder doesn\'t know this phone any more', sub: `On your iPhone, open Settings, then Bluetooth. Tap ⓘ next to ${s.recorder?.name ?? 'the recorder'} and choose Forget This Device. Then tap the recorder's button once and find it again here.`, action: false, dim: true }
  if (!x) return { scene: S.waking, head: 'Connecting…', sub: '', action: false }

  if (rolledBack.value) return { scene: S.result('undone', fw), caption: 'Your recorder shows:', head: `Went back to ${fw}`, sub: 'The new version didn\'t start, so it went back. Nothing was lost.', action: false }
  if ((x.upd ?? -1) >= 0 && (x.upd ?? 0) < 100 && !x.restart) return { scene: S.updating(x.upd ?? 0), caption: 'Your recorder shows:', head: 'Updating its software', sub: `${x.upd}% · Your Mac is sending it. Keep the recorder switched on.`, action: false }
  if (x.restart || x.upd === 100) return { scene: S.updating(100), caption: 'Your recorder shows:', head: 'Restarting…', sub: 'It\'s starting the new version. This takes about half a minute.', action: false }
  if (x.pending) return { scene: S.result('checking', fw), caption: 'Your recorder shows:', head: 'Checking in with your Mac…', sub: 'The new version waits until your Mac confirms it.', action: false }
  if (justUpdated.value) return { scene: S.result('done', fw), caption: 'Your recorder shows:', head: `Updated to ${fw}`, sub: 'All good.', action: true }

  const anim = momentAge.value < 1800 ? moment.value?.kind : undefined
  const scene = anim === 'connected_first' || anim === 'reconnected' ? (x.state === 'recording' ? S.start : S.hello)
    : anim === 'started' ? S.start : anim === 'stopped' ? S.stop : sceneFor(x.state)
  const head = STATE_TEXT[x.state] ?? 'Your recorder'
  let sub = ''
  switch (x.state) {
    case 'recording': {
      const where = x.wifi ? `sending over ${x.ssid || 'Wi-Fi'}` : 'saving to its memory card'
      sub = x.rec_s !== undefined ? `For ${duration(x.rec_s)} · ${where}` : (x.wifi ? `Sending recordings to your Mac over ${x.ssid || 'Wi-Fi'}` : 'Saving to its memory card')
      break
    }
    case 'off': sub = 'It isn\'t recording.'; break
    case 'starting': sub = 'Getting its memory card ready.'; break
    case 'no_card': sub = 'Put the memory card back in. It starts recording again by itself.'; break
    case 'card_error': sub = 'Take the memory card out and put it back in. If it happens again, the card may need replacing.'; break
    case 'card_full': sub = 'It has stopped recording. It makes room once it sends its recordings to your Mac over Wi-Fi.'; break
    case 'mic_error': sub = 'Switch the recorder off and on again.'; break
    case 'not_paired': sub = 'It needs your Mac once. Plug it into your Mac and use Settings, then Recorder, in the lab.'; break
    default: sub = 'This app is older than your recorder. Update Octo to see what it\'s doing.'
  }
  if (x.want_on && NEEDS_YOU.includes(x.state)) sub += ' It\'s set to record and starts when it can.'
  return { scene, head, sub, action: !NEEDS_YOU.includes(x.state) }
})

function sceneFor(state?: string): Scene {
  if (state === 'recording') return S.listening
  if (state === 'starting') return S.waking
  if (state === 'not_paired') return S.setup
  if (state && NEEDS_YOU.includes(state as never)) return S.alert
  return S.asleep
}

// ---- recording on and off ----
const recording = computed(() => !!st.value?.want_on)
const busy = ref(false)
const actionError = ref('')
const confirm = ref(false)
async function toggle() {
  confirm.value = false
  busy.value = true
  actionError.value = ''
  const on = !recording.value
  try {
    await props.c.setRecording(on)
  } catch {
    haptic('error')
    actionError.value = `Couldn't reach the recorder. It's still ${on ? 'stopped' : 'recording'}.`
  } finally { busy.value = false }
}

// ---- the filler cue (rec-24, B20) ----
const fillerBusy = ref(false)
const fillerError = ref('')
async function toggleFiller() {
  fillerBusy.value = true
  fillerError.value = ''
  const on = !st.value?.filler_on
  try { await props.c.setFiller(on) } catch {
    haptic('error')
    fillerError.value = `Couldn't reach the recorder. The cue is still ${on ? 'off' : 'on'}.`
  } finally { fillerBusy.value = false }
}

// ---- rows and sheets ----
const pill = computed(() => {
  if (connected.value) return { cls: 'on', text: 'Connected' }
  if (s.phase === 'bluetooth_off') return { cls: '', text: 'Bluetooth off' }
  if (away.value) return { cls: '', text: 'Not nearby' }
  return { cls: 'trying', text: 'Connecting…' }
})
const showPillWord = ref(true)
let pillTimer: ReturnType<typeof setTimeout> | undefined
watch(() => pill.value.text, () => { showPillWord.value = true; clearTimeout(pillTimer); if (connected.value) pillTimer = setTimeout(() => { showPillWord.value = false }, 2000) }, { immediate: true })

const wifiOpen = ref(false)
const aboutOpen = ref(false)
// Which software each side runs (8 Oct): the recorder's from its status, the app's from its build.
const fw = computed(() => s.status?.fw ?? s.recorder?.fw ?? '')
const app = ref('')
onMounted(async () => { app.value = versionText(await appVersion()) })
const wifiText = computed(() => {
  const x = st.value
  if (!x) return ''
  if (x.wifi) return `${x.ssid || 'Connected'} · ${signal(x.rssi)} signal`
  return 'Not on Wi-Fi · saving to its card'
})
</script>

<template>
  <div class="recorder">
    <header class="top">
      <h1 class="title">Recorder</h1>
      <span class="pill" :class="pill.cls" role="status" :aria-label="pill.text">
        <span class="dot" aria-hidden="true"></span><span v-if="showPillWord || !connected">{{ pill.text }}</span>
      </span>
    </header>

    <Panel :scene="view.scene" :caption="view.caption" :dim="view.dim" />

    <Transition name="fade">
      <p v-if="firstBanner" class="banner">Next time, Octo connects by itself when you open it.</p>
    </Transition>

    <section class="state">
      <h2 class="headline">{{ view.head }}</h2>
      <p class="sub">{{ view.sub }}</p>
    </section>

    <template v-if="rolledBack">
      <button type="button" class="btn" @click="dismiss">OK</button>
    </template>
    <template v-else-if="view.action && connected">
      <HoldButton :recording="recording" :disabled="busy" @done="toggle" @tap="confirm = true" />
      <p v-if="actionError" class="error small" role="alert">{{ actionError }}</p>
      <!-- B16: the recorder's double tap, from the phone; the Mac's clock places it -->
      <MarkButton v-if="st?.state === 'recording' && lab.s.cfg" :memos="memos" />
    </template>
    <template v-else-if="s.phase === 'not_allowed'">
      <button type="button" class="btn primary" @click="c.openSettings()">Open Settings</button>
    </template>
    <template v-else-if="s.phase === 'forgotten'">
      <button type="button" class="btn primary" @click="c.retry()">Find it again</button>
    </template>

    <ul v-if="st" class="rows">
      <li v-if="st.card_total_mb"><div class="row"><span>Time left to record</span><span class="value">{{ roomText(st.card_free_mb) }}</span></div></li>
      <li v-if="st.filler_model"><button type="button" class="row" role="switch" :aria-checked="!!st.filler_on"
              :disabled="!connected || fillerBusy" @click="toggleFiller"><span>Show the filler cue on its screen</span>
        <span class="value">{{ st.filler_on ? `On${st.fillers ? ` · ${st.fillers} this recording` : ''}` : 'Off' }}</span></button></li>
      <li><button type="button" class="row" :disabled="!connected" @click="wifiOpen = true"><span>Wi-Fi</span><span class="value chev">{{ wifiText }}</span></button></li>
      <li><button type="button" class="row" @click="aboutOpen = true"><span>About this recorder</span><span class="value chev">{{ s.recorder?.name }}</span></button></li>
    </ul>
    <p v-if="fillerError" class="error small" role="alert">{{ fillerError }}</p>
    <p class="versions hint">Recorder {{ fw || '—' }} · App {{ app || '—' }}</p>

    <p class="visually-hidden" aria-live="polite">{{ said }}</p>

    <Sheet :open="confirm" :title="recording ? 'Stop recording?' : 'Start recording?'" @close="confirm = false">
      <p v-if="recording">It stops listening until you start it again.</p>
      <p v-else>It records everyone nearby. Its light pulses while it records.</p>
      <button type="button" class="btn" :class="recording ? 'danger' : 'primary'" :disabled="busy" @click="toggle">{{ recording ? 'Stop recording' : 'Start recording' }}</button>
      <button type="button" class="btn quiet" @click="confirm = false">{{ recording ? 'Keep recording' : 'Not now' }}</button>
    </Sheet>
    <WifiSheet :open="wifiOpen" :c="c" @close="wifiOpen = false" />
    <AboutSheet :open="aboutOpen" :c="c" @close="aboutOpen = false" />
  </div>
</template>

<style scoped>
.recorder { display: flex; flex-direction: column; gap: 16px; }
.top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.state { display: flex; flex-direction: column; gap: 4px; min-height: 4.5rem; }
.row:disabled { opacity: 0.6; }
.versions { text-align: center; margin: 4px 0 0; font-variant-numeric: tabular-nums; }
</style>
