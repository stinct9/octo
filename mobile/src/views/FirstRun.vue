<script setup lang="ts">
// Finding the recorder the first time (or a different one): hello, looking, found, the code, and what went
// wrong in plain words. Words: "set up with your Mac" (USB, once) and "connect your phone" (the code).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Panel from '../components/Panel.vue'
import Sheet from '../components/Sheet.vue'
import type { Connection } from '../connection'
import type { Found } from '../recorder'
import * as S from '../scenes'
import { haptic } from '../haptics'
import { madeUp, offerMadeUp, setMadeUp } from '../madeup'

const props = defineProps<{ c: Connection; asked: boolean }>()
const emit = defineEmits<{ (e: 'asked'): void }>()
const s = props.c.s
const setupOpen = ref(false)

// The hint while looking fades in after 6 s.
const lookingFor = ref(0)
let timer: ReturnType<typeof setInterval> | undefined
watch(() => s.phase, (p) => {
  clearInterval(timer)
  lookingFor.value = 0
  if (p === 'searching') timer = setInterval(() => lookingFor.value++, 1000)
  if (p === 'found') haptic('light')
}, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))

const scene = computed(() => {
  switch (s.phase) {
    case 'searching': return S.search
    case 'found': return S.found
    case 'connecting': return S.waking
    case 'code': return S.code
    case 'pair_failed': case 'forgotten': case 'not_allowed': case 'unsupported': return S.alert
    default: return S.asleep
  }
})

function find() {
  emit('asked')
  void props.c.search()
}
const choose = (f: Found) => props.c.choose(f)

const failure = computed(() => {
  if (s.failure === 'timeout') return { h: 'The code box closed', p: 'It waits a minute. Try again, and type the code when it appears.' }
  if (s.failure === 'auth' || s.failure === 'cancelled') return { h: 'Not connected', p: 'The code didn\'t go through. Your recorder shows a new code each time: try again and type the one on its screen now.' }
  return { h: 'Couldn\'t connect', p: 'Keep your phone close to the recorder and try again.' }
})
const name = computed(() => s.recorder?.name ?? s.found[0]?.name ?? 'your recorder')
</script>

<template>
  <div class="first">
    <Panel :scene="scene" :caption="s.phase === 'code' ? 'Your recorder shows:' : undefined" />

    <template v-if="s.phase === 'hello' || s.phase === 'loading'">
      <h1 class="title rise">Let's find your recorder</h1>
      <p class="sub rise d1">Keep it close and switched on.</p>
      <button type="button" class="btn primary rise d2" @click="find">Find my recorder</button>
      <p v-if="!asked" class="hint rise d3">Your iPhone will ask to use Bluetooth. Octo uses it only to talk to your recorder.</p>
      <button type="button" class="link rise d3" @click="setupOpen = true">Not set up with your Mac yet?</button>
      <button v-if="offerMadeUp()" type="button" class="link rise d3" @click="setMadeUp(true)">Just looking? Try a made-up recorder</button>
    </template>

    <template v-else-if="s.phase === 'searching'">
      <h1 class="title" aria-live="polite">Looking for your recorder…</h1>
      <Transition name="fade">
        <p v-if="lookingFor >= 6" class="sub">Tap the button on your recorder once. Its screen lights up and your phone can see it.</p>
      </Transition>
      <button type="button" class="btn quiet" @click="c.cancelSearch()">Cancel</button>
    </template>

    <template v-else-if="s.phase === 'cant_find'">
      <h1 class="title">Can't find it</h1>
      <ul class="tips">
        <li>Is it switched on? The switch is on its side.</li>
        <li>Tap its button once, then look again.</li>
        <li>Its screen says "Set me up"? It needs your Mac first. <button type="button" class="link" @click="setupOpen = true">How</button></li>
      </ul>
      <button type="button" class="btn primary" @click="find">Look again</button>
      <button v-if="s.replacing" type="button" class="btn quiet" @click="c.cancelSearch()">Cancel</button>
    </template>

    <template v-else-if="s.phase === 'found' && s.found.length === 1">
      <h1 class="title rise" aria-live="polite">Found your recorder</h1>
      <p class="rise d1"><span class="chip">{{ s.found[0].name }}</span></p>
      <p class="sub rise d1">When you connect, look at its screen: the first time, it shows a code for your phone.</p>
      <button type="button" class="btn primary rise d2" @click="choose(s.found[0])">Connect</button>
    </template>

    <template v-else-if="s.phase === 'found'">
      <h1 class="title rise" aria-live="polite">Found {{ s.found.length }} recorders</h1>
      <p class="sub rise d1">Not sure which? Tap the button on yours: it's the one that lights up.</p>
      <ul class="rows rise d2">
        <li v-for="(f, i) in s.found" :key="f.id">
          <button type="button" class="row" @click="choose(f)">
            <span>{{ f.name }}<span v-if="i === 0" class="hint"> · Closest</span></span><span class="value chev">Connect</span>
          </button>
        </li>
      </ul>
    </template>

    <template v-else-if="s.phase === 'connecting'">
      <h1 class="title" aria-live="polite">Connecting…</h1>
      <p class="sub">To {{ name }}.</p>
    </template>

    <template v-else-if="s.phase === 'code'">
      <h1 class="title" aria-live="polite">Type the code from your recorder</h1>
      <p class="sub">Your iPhone is asking for six digits. They're on your recorder's screen.</p>
      <p class="hint">This happens once. It makes sure only your phone can control your recorder.</p>
    </template>

    <template v-else-if="s.phase === 'pair_failed'">
      <h1 class="title" role="alert">{{ failure.h }}</h1>
      <p class="sub">{{ failure.p }}</p>
      <button type="button" class="btn primary" @click="c.search()">Try again</button>
    </template>

    <template v-else-if="s.phase === 'forgotten'">
      <h1 class="title" role="alert">Your recorder doesn't know this phone any more</h1>
      <p class="sub">On your iPhone, open Settings, then Bluetooth. Tap ⓘ next to {{ name }} and choose Forget This Device. Then tap the recorder's button once and find it again.</p>
      <button type="button" class="btn primary" @click="c.retry()">Find it again</button>
    </template>

    <template v-else-if="s.phase === 'bluetooth_off'">
      <h1 class="title" role="alert">Bluetooth is off</h1>
      <p class="sub">Turn it on in Control Centre, then come back. Octo carries on by itself.</p>
    </template>

    <template v-else-if="s.phase === 'not_allowed'">
      <h1 class="title" role="alert">Bluetooth isn't allowed</h1>
      <p class="sub">Octo can't see your recorder without it. Allow Bluetooth for Octo in Settings.</p>
      <button type="button" class="btn primary" @click="c.openSettings()">Open Settings</button>
    </template>

    <template v-else-if="s.phase === 'unsupported'">
      <h1 class="title" role="alert">No Bluetooth here</h1>
      <p class="sub">Octo talks to your recorder over Bluetooth, and this device has none it can use.</p>
    </template>

    <Sheet :open="setupOpen" title="Set it up with your Mac first" @close="setupOpen = false">
      <p>Your recorder needs your Mac once before your phone can find it. Plug it into your Mac with a USB cable, then in
        the lab open Settings, then Recorder, and follow the steps.</p>
      <p class="sub">Until then its screen says "Set me up":</p>
      <Panel :scene="S.setup" />
    </Sheet>
  </div>
</template>

<style scoped>
.first { display: flex; flex-direction: column; gap: 16px; padding-top: 8px; }
.tips { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; color: var(--ink-2); }
</style>
