<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { lab, LET_IN, working, type LetIn } from './lab/api'
import { href, route } from './lab/router'
import { onMac } from './lab/where'
import DeviceChip from './components/DeviceChip.vue'
import BenchView from './views/BenchView.vue'
import CoachView from './views/CoachView.vue'
import AskView from './views/AskView.vue'
import ConversationView from './views/ConversationView.vue'
import SortView from './views/SortView.vue'
import QuestionCheckView from './views/QuestionCheckView.vue'
import CommitmentCheckView from './views/CommitmentCheckView.vue'
import ConversationsView from './views/ConversationsView.vue'
import FillerCheckView from './views/FillerCheckView.vue'
import FillersView from './views/FillersView.vue'
import PeopleView from './views/PeopleView.vue'
import PersonView from './views/PersonView.vue'
import PhoneView from './views/PhoneView.vue'
import PipelineView from './views/PipelineView.vue'
import PracticeView from './views/PracticeView.vue'
import ProgressView from './views/ProgressView.vue'
import RehearseView from './views/RehearseView.vue'
import CalendarView from './views/CalendarView.vue'
import PrepareView from './views/PrepareView.vue'
import SettingsView from './views/SettingsView.vue'
import SetupView from './views/SetupView.vue'
import ThisMacView from './views/ThisMacView.vue'

// On the phone (over the Wi-Fi listener or Tailscale) the pipeline, the bench and the phone settings don't exist.
// A browser on the Mac without the Mac's key (octo/mackey.py) is shown how to let it in, instead of empty pages.
const letIn = ref<LetIn | null>(null)
const onLetIn = (e: Event) => { letIn.value = (e as CustomEvent<LetIn | undefined>).detail ?? { command: 'octo open', app: false } }
if (typeof window !== 'undefined') window.addEventListener(LET_IN, onLetIn)
onUnmounted(() => window.removeEventListener(LET_IN, onLetIn))
// Inside the Octo app (macapp/Octo.swift registers this handler): About Octo is in its Octo menu instead
const inApp = !!(window as unknown as { webkit?: { messageHandlers?: { octo?: unknown } } }).webkit?.messageHandlers?.octo
const tabs = [
  { name: 'conversations', label: 'Conversations', href: href.conversations },
  { name: 'people', label: 'People', href: href.people },
  { name: 'practice', label: 'Practice', href: href.practice },
  { name: 'progress', label: 'Progress', href: href.progress },
]
// The Mac-only tools, used rarely, sit under one menu so the bar holds what's used daily; the builder's tools under a
// Hardware line, and About Octo last (Jay, 22 Oct). On a phone there's no menu: About Octo is at the foot of Conversations.
interface MenuItem { name: string; label: string; hint: string; href: string; external?: boolean; line?: string }
const setup: MenuItem[] = onMac ? [
  { name: 'setup', label: 'Set up Octo', hint: 'Who the reviews are about, your principles, the model', href: href.setup },
  { name: 'mac', label: 'This Mac', hint: 'Its checks, a test notification, calls', href: href.mac },
  { name: 'pipeline', label: 'Pipeline', hint: 'Each step\'s model, and why', href: href.pipeline },
  { name: 'settings', label: 'Settings', hint: 'Models and keys, the recorder, TickTick', href: href.settings },
  { name: 'phone', label: 'Phone', hint: 'Review on the iPhone at home', href: href.phone },
  { name: 'bench', label: 'Bench', hint: 'Test the recorder board', href: href.bench, line: 'Hardware' },
  { name: 'case', label: 'Recorder case', hint: 'The table case\'s design', href: '/case', external: true },
  ...(inApp ? [] : [{ name: 'about', label: 'About Octo', hint: 'What Octo is, for people who ask', href: '/about', external: true, line: '' }]),
] : []

const active = computed(() => (route.current.name === 'conversation' || route.current.name === 'coach' || route.current.name === 'sort' || route.current.name === 'ask' || route.current.name === 'calendar' ? 'conversations'
  : route.current.name === 'fillers' || route.current.name === 'fillercheck' || route.current.name === 'questioncheck' || route.current.name === 'commitmentcheck' ? 'progress' : route.current.name === 'person' || route.current.name === 'prepare' ? 'people'
    : route.current.name === 'rehearse' ? 'practice' : route.current.name))
const inSetup = computed(() => setup.some((t) => t.name === active.value))
const setupOpen = ref(false)
const setupEl = ref<HTMLElement | null>(null)
function closeOutside(e: Event) { if (setupOpen.value && !setupEl.value?.contains(e.target as Node)) setupOpen.value = false }
function closeOnEscape(e: KeyboardEvent) { if (e.key === 'Escape') setupOpen.value = false }
onMounted(() => { document.addEventListener('click', closeOutside); document.addEventListener('keydown', closeOnEscape) })
onUnmounted(() => { document.removeEventListener('click', closeOutside); document.removeEventListener('keydown', closeOnEscape) })
watch(() => route.current, () => { setupOpen.value = false })

// The server keeps the code it started with; after a change, say so until it's restarted (Mac only).
const restart = ref(false)
const cantSave = ref<string | null>(null)  // the lab can't save to its folder (octo/watchdog.py, 13 Oct)
const freeGb = ref<number | null>(null)
// A page opens at the top, not at the last page's scroll position; the list keeps its place when
// you come back to it (review 4 Oct, M33).
const listScroll = ref(0)
watch(() => route.current, (to, from) => {
  if (from?.name === 'conversations') listScroll.value = window.scrollY
  void nextTick(() => window.scrollTo(0, to.name === 'conversations' ? listScroll.value : 0))
})

// The page keeps the UI it loaded with: after a rebuild, say so (M35). The build writes
// dist/.src-hash; the first one seen is this page's.
const builtAs = ref<string | null>(null)
const rebuilt = ref(false)
function checkBuild() {
  fetch(`/.src-hash?t=${Date.now()}`, { cache: 'no-store' }).then((r) => (r.ok ? r.text() : null)).then((h) => {
    if (!h) return
    if (builtAs.value === null) builtAs.value = h
    else rebuilt.value = h !== builtAs.value
  }).catch(() => {})
}
function checkStatus() {
  if (onMac) lab.status().then((s) => { restart.value = !!s.code_changed; freeGb.value = s.free_gb ?? null; cantSave.value = s.cant_save ?? null }).catch(() => {})
  checkBuild()
}
const statusTimer = setInterval(checkStatus, 60_000)
onUnmounted(() => clearInterval(statusTimer))

const reload = () => window.location.reload()

watch(() => route.current, checkStatus, { immediate: true })
</script>

<template>
  <div class="shell">
    <div v-if="working > 0" class="busy-bar" role="progressbar" aria-label="Saving"></div>
    <header class="nav">
      <a class="brand" :href="href.conversations">Octo</a>
      <nav aria-label="Main">
        <a v-for="t in tabs" :key="t.name" :href="t.href" :aria-current="active === t.name ? 'page' : undefined"
           :class="{ on: active === t.name }">{{ t.label }}</a>
      </nav>
      <div v-if="setup.length" ref="setupEl" class="setup">
        <button type="button" class="setup-btn" :class="{ on: inSetup }" aria-haspopup="true" :aria-expanded="setupOpen"
                @click="setupOpen = !setupOpen">{{ inSetup ? setup.find((t) => t.name === active)?.label : 'Setup' }}
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
        </button>
        <div v-if="setupOpen" class="setup-menu" role="menu">
          <template v-for="t in setup" :key="t.name">
            <div v-if="t.line !== undefined" class="menu-line" role="separator">{{ t.line }}</div>
            <a role="menuitem" :href="t.href" :aria-current="active === t.name ? 'page' : undefined"
               :class="{ on: active === t.name }" :target="t.external ? '_blank' : undefined" :rel="t.external ? 'noopener' : undefined"
               :title="t.external ? 'Opens in a new tab' : undefined"><span class="label">{{ t.label }}<svg v-if="t.external" width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M4 3h5v5M9 3 3.5 8.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></span><span class="hint">{{ t.hint }}</span></a>
          </template>
        </div>
      </div>
      <div class="right">
        <button v-if="rebuilt" type="button" class="restart reload" @click="reload">Reload the page</button>
        <span v-if="cantSave" class="restart bad" role="status" :title="cantSave === 'denied'
          ? 'Allow Python in System Settings → Privacy & Security → Files and Folders'
          : 'Look for a macOS prompt about files in Downloads and allow it'">Can't save · nothing is processed</span>
        <span v-if="restart" class="restart" title="The code changed since the server started: launchctl kickstart -k gui/$(id -u)/com.octo.lab">Restart the server</span>
        <span v-if="freeGb !== null && freeGb < 20" class="restart" :class="{ bad: freeGb < 5 }" :title="freeGb < 5 ? 'Under 5 GB: nothing more is pulled from the recorder until there is room' : 'Under 20 GB free'">
          {{ freeGb < 5 ? `Disk full · ${freeGb} GB · recorder not pulled` : `Disk: ${freeGb} GB free` }}</span>
        <DeviceChip />
      </div>
    </header>
    <main v-if="letIn" class="let-in">
      <section class="card pad">
        <h1>Let this browser in</h1>
        <p>Octo answers only the browsers on this Mac that it has let in, so other programs, and other people's accounts
          on this Mac, can't read your conversations.</p>
        <p v-if="letIn.window">Open <b>Octo</b> from your Applications folder (or Spotlight): the lab opens in its own
          window. For this browser instead, choose <b>Open in Browser</b> from the octopus in the menu bar.</p>
        <p v-else-if="letIn.app">Open <b>Octo</b> from your Applications folder (or Spotlight). It opens the lab in your default
          browser and lets it in. Once per browser.</p>
        <p :class="{ muted: letIn.app }">{{ letIn.app ? 'Or in' : 'In' }} Terminal, run <code>{{ letIn.command }}</code>.
          <template v-if="!letIn.app">It opens Octo in your default browser and lets it in. Once per browser.</template></p>
      </section>
    </main>
    <ConversationsView v-else-if="route.current.name === 'conversations'" />
    <ConversationView v-else-if="route.current.name === 'conversation'" :id="route.current.id" :key="route.current.id"
                      :line="route.current.line" />
    <PeopleView v-else-if="route.current.name === 'people'" />
    <PersonView v-else-if="route.current.name === 'person'" :id="route.current.id" :key="route.current.id" />
    <CalendarView v-else-if="route.current.name === 'calendar'" :month="route.current.month" :day="route.current.day" />
    <PrepareView v-else-if="route.current.name === 'prepare'" :id="route.current.id" :key="`prep-${route.current.id}`" />
    <ProgressView v-else-if="route.current.name === 'progress'" />
    <FillersView v-else-if="route.current.name === 'fillers'" />
    <FillerCheckView v-else-if="route.current.name === 'fillercheck' && onMac" />
    <CoachView v-else-if="route.current.name === 'coach'" :note-id="route.current.note" />
    <SortView v-else-if="route.current.name === 'sort'" />
    <AskView v-else-if="route.current.name === 'ask'" />
    <QuestionCheckView v-else-if="route.current.name === 'questioncheck'" />
    <CommitmentCheckView v-else-if="route.current.name === 'commitmentcheck'" />
    <PracticeView v-else-if="route.current.name === 'practice'" :take="route.current.take" />
    <RehearseView v-else-if="route.current.name === 'rehearse'" :id="route.current.id" :person="route.current.person"
                  :key="route.current.id ?? `new-${route.current.person ?? ''}`" />
    <PipelineView v-else-if="route.current.name === 'pipeline' && onMac" />
    <PhoneView v-else-if="route.current.name === 'phone' && onMac" />
    <BenchView v-else-if="route.current.name === 'bench' && onMac" />
    <SettingsView v-else-if="route.current.name === 'settings' && onMac" />
    <SetupView v-else-if="route.current.name === 'setup' && onMac" />
    <ThisMacView v-else-if="route.current.name === 'mac' && onMac" />
    <ConversationsView v-else />
  </div>
</template>

<style scoped>
.shell { min-height: 100%; display: flex; flex-direction: column; }
.let-in { max-width: 560px; margin: 48px auto; padding: 0 16px; }
.let-in h1 { margin: 0 0 12px; font-size: 22px; }
.let-in p { margin: 0 0 10px; line-height: 1.5; }
.let-in code { padding: 2px 6px; border-radius: 6px; background: var(--quote); }
.let-in .muted { color: var(--muted); }
.nav {
  height: 56px; padding: 0 28px; display: flex; align-items: center; gap: 16px; flex-shrink: 0;
  background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { font-weight: 600; color: var(--ink); text-decoration: none; white-space: nowrap; }
/* The Conversations tab goes to the same place; below this width the tabs need the room. */
@media (max-width: 1100px) { .brand { display: none; } }
nav { display: flex; gap: 4px; min-width: 0; overflow-x: auto; scrollbar-width: none; }  /* narrow: the tabs scroll, not the page */
nav a {
  flex-shrink: 0; white-space: nowrap;
  padding: 8px 14px; border-radius: 8px; color: var(--muted); text-decoration: none; min-height: 44px;
  display: inline-flex; align-items: center;
}
nav a:hover { color: var(--ink); }
.right { margin-left: auto; display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.restart { border: 0; font: inherit; cursor: default; padding: 5px 12px; border-radius: 999px; background: var(--warn-soft); color: var(--warn); font-size: 13px; white-space: nowrap; }
.restart.reload { cursor: pointer; }
.restart.bad { background: var(--fail-soft); color: var(--fail); }  /* nothing is being saved, or nothing more pulled */
nav a.on { background: var(--accent-soft); color: var(--accent); font-weight: 500; }
.setup { position: relative; flex-shrink: 0; }
.setup-btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 12px; min-height: 44px; border: 0; border-radius: 8px;
  background: none; color: var(--muted); font: inherit; cursor: pointer; }
.setup-btn:hover { color: var(--ink); }
.setup-btn.on { background: var(--accent-soft); color: var(--accent); font-weight: 500; }
.setup-menu { position: absolute; top: 46px; left: 0; z-index: 10; width: 260px; padding: 6px; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18); }
.setup-menu a { display: flex; flex-direction: column; gap: 1px; padding: 8px 10px; border-radius: 8px; color: var(--ink); text-decoration: none; }
.setup-menu a:hover { background: var(--quote); }
.setup-menu a.on { background: var(--accent-soft); color: var(--accent); }
.setup-menu .hint { font-size: 12px; color: var(--muted); }
.setup-menu .label { display: inline-flex; align-items: center; gap: 5px; }
.menu-line { margin: 6px 10px 2px; padding-top: 8px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); min-height: 1px; }
@media (max-width: 640px) {
  .setup-menu { left: 0; max-width: calc(100vw - 24px); }
  .nav { padding: 6px 12px; gap: 4px 12px; height: auto; min-height: 56px; flex-wrap: wrap; }
  nav { flex: 1 1 100%; }  /* a row of its own; past the screen's width the tabs scroll, not the page */
  .right { margin-left: 0; }
  .brand { display: none; }
  nav a { padding: 8px 10px; }
}
</style>
