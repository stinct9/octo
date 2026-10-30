<script setup lang="ts">
// The Lab tab (docs/phone/design-review.md §5.9; 8 Oct: "our dashboard in the mobile app"): the lab's own
// pages from the Mac, in a native web view placed over this tab's frame. Reached on the home Wi-Fi, or through
// Tailscale anywhere else. The phone can't tell an asleep Mac from one on another network, so it doesn't guess.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Lab } from '../lab'
import { madeUp, offerMadeUp, setMadeUp } from '../madeup'

const props = defineProps<{ lab: Lab; active: boolean; native: boolean }>()
const s = props.lab.s
const frame = ref<HTMLElement | null>(null)
let shown = false

function rect() {
  const r = frame.value?.getBoundingClientRect()
  return r ? { x: r.left, y: r.top, width: r.width, height: r.height } : null
}

async function sync() {
  await nextTick()
  const r = rect()
  if (props.active && s.phase === 'ready' && r && r.width > 0) {
    await props.lab.show(r).catch(() => props.lab.failed())
    shown = true
  } else if (shown) {
    await props.lab.hide().catch(() => {})
    shown = false
  }
}
watch(() => [props.active, s.phase, s.host], sync)
let ro: ResizeObserver | undefined
onMounted(() => {
  ro = new ResizeObserver(() => {
    const r = rect()
    if (shown && r) void props.lab.place(r)
  })
  if (frame.value) ro.observe(frame.value)
  void sync()
})
onBeforeUnmount(() => ro?.disconnect())

// Once the lab answers, its own header is the only one (Jay, 22 Oct): this one, and its pill, show only until then
// or when something's wrong. Pull down on the lab's page to reload it.
const pill = computed(() => {
  if (s.phase === 'reaching') return { cls: 'trying', text: 'Reaching your Mac…' }
  return null
})
const lastReached = computed(() => (s.cfg?.reachedAt ? new Date(s.cfg.reachedAt).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : ''))
</script>

<template>
  <div class="lab">
    <header v-if="s.phase !== 'ready'" class="top">
      <h1 class="title">Lab</h1>
      <span v-if="pill" class="pill" :class="pill.cls" role="status"><span class="dot" aria-hidden="true"></span>{{ pill.text }}</span>
    </header>

    <div v-if="s.phase === 'ready' && s.notify?.state === 'ask'" class="ask" role="group" aria-label="Notifications">
      <p class="ask-text">Get a nudge on this phone when a review is ready? Counts only, never names.</p>
      <div class="ask-buttons">
        <button type="button" class="btn small" @click="lab.notNow()">Not now</button>
        <button type="button" class="btn primary small" @click="lab.allowNotifications()">Yes</button>
      </div>
    </div>
    <p v-else-if="s.phase === 'ready' && s.notify?.state === 'on' && s.notify.refresh === false" class="hint ask-off">
      Background App Refresh is off for Octo, so the nudges come only when you open it: Settings → Octo.</p>

    <div v-if="s.phase === 'ready'" ref="frame" class="frame">
      <p v-if="!native && madeUp()" class="placeholder">Here your lab's own pages open, from your Mac: what each conversation
        was, the one thing to work on, your steps, and the one question. This made-up lab has no pages to show.</p>
      <p v-else-if="!native" class="placeholder">In the iPhone app, your lab's own pages open here, from {{ s.cfg?.mac }}.</p>
    </div>

    <template v-else-if="s.phase === 'none' || s.phase === 'loading'">
      <div class="intro rise">
        <h2 class="headline">See your lab here</h2>
        <p class="sub">The lab is Octo on your Mac: your conversations, reviews and steps. Connect this phone to it once.</p>
      </div>
      <ol class="steps rise d1">
        <li>On your Mac, open the lab and go to <b>Setup → Phone</b>.</li>
        <li>Choose <b>Show the code</b>.</li>
        <li>Scan it here. Or point your iPhone's Camera at it.</li>
      </ol>
      <button type="button" class="btn primary rise d2" @click="lab.scan()">Scan the code</button>
      <button type="button" class="link rise d2" @click="lab.paste()">Or paste the link from your Mac</button>
      <p v-if="s.failure" class="error small" role="alert">{{ s.failure }}</p>
      <button v-if="offerMadeUp()" type="button" class="link rise d3" @click="setMadeUp(true)">Just looking? Try a made-up lab</button>
      <p class="hint rise d3">At home it connects over your Wi-Fi. Away from home it needs Tailscale on your Mac and this
        iPhone: the Mac's Phone page says how.</p>
    </template>

    <template v-else-if="s.phase === 'pairing'">
      <h2 class="headline" aria-live="polite">Connecting to your Mac…</h2>
      <p class="sub">Checking it's your Mac, then saving the connection on this phone.</p>
    </template>

    <template v-else-if="s.phase === 'pair_failed'">
      <h2 class="headline" role="alert">Not connected</h2>
      <p class="sub">{{ s.failure }}</p>
      <button type="button" class="btn primary" @click="lab.scan()">Scan again</button>
      <button type="button" class="link" @click="lab.paste()">Or paste the link from your Mac</button>
    </template>

    <template v-else-if="s.phase === 'reaching'">
      <h2 class="headline" aria-live="polite">Reaching {{ s.cfg?.mac }}…</h2>
    </template>

    <template v-else-if="s.phase === 'unreachable'">
      <h2 class="headline" role="alert">Can't reach your Mac</h2>
      <p class="sub">It may be asleep, switched off, or on another network. Or phone access is off: on your Mac, open the
        lab, then Phone, then Allow my phone. At home, check Local Network is on for Octo: Settings → Octo.</p>
      <p v-if="!lab.hasTailscale" class="sub">Away from home? The app reaches your Mac only with Tailscale on both: the
        Mac's Phone page says how.</p>
      <p class="hint">Your recorder keeps recording and sends everything later.<span v-if="lastReached"> Last reached {{ lastReached }}.</span></p>
      <button type="button" class="btn primary" @click="lab.reach()">Try again</button>
    </template>

    <template v-else-if="s.phase === 'removed'">
      <h2 class="headline" role="alert">This phone was removed from your lab</h2>
      <p class="sub">Connect it again to see your lab here: on your Mac, Phone, Show the code.</p>
      <button type="button" class="btn primary" @click="lab.forget().then(() => lab.scan())">Scan a new code</button>
    </template>
  </div>
</template>

<style scoped>
.lab { display: flex; flex-direction: column; gap: 16px; flex: 1; min-height: 0; }
.top { display: flex; align-items: center; gap: 10px; }
.top .title { flex: 1; }
.frame { flex: 1; min-height: 320px; margin: 0 -16px; background: var(--ground); display: flex; align-items: center; justify-content: center; }
.placeholder { color: var(--muted); padding: 24px; text-align: center; }
.ask { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line); }
.ask-text { margin: 0; color: var(--ink-2); }
.ask-buttons { display: flex; gap: 10px; justify-content: flex-end; }
.ask-off { margin: 0; }
.steps { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; color: var(--ink-2); }
.intro { display: flex; flex-direction: column; gap: 6px; }
</style>
