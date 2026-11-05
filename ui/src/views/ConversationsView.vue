<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import FromReviews from '../components/FromReviews.vue'
import AgendaCard from '../components/AgendaCard.vue'
import WorkingOn from '../components/WorkingOn.vue'
import WeekCard from '../components/WeekCard.vue'
import SearchBox from '../components/SearchBox.vue'
import SetupBanner from '../components/SetupBanner.vue'
import { lab } from '../lab/api'
import { kept } from '../lab/kept'
import { ASIDE, etaText, inProgress, mss, needsYou, sourceName, speakerColors, todoLabel, when } from '../lab/format'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { AsideRow, Incoming, SessionSummary, States, Status } from '../lab/types'

const sessions = ref<SessionSummary[] | null>(kept.list?.sessions ?? null)
const refreshing = ref(!!kept.list)  // showing the list as last seen until it's asked again: rows may still move
const incoming = ref<Incoming[]>(kept.list?.incoming ?? [])
const error = ref('')
const show = ref<'needs' | 'all'>('needs')
const blur = ref(false)

const needs = computed(() => (sessions.value ?? []).filter((s) => needsYou(s.todo)))
const working = computed(() => (sessions.value ?? []).filter((s) => inProgress(s.todo)).length)
const shown = computed(() => (show.value === 'needs' ? needs.value : sessions.value ?? []))

// ---- adding recordings -----------------------------------------------------------------------
interface Upload { key: number; name: string; pct: number; error: string; done: boolean }
const uploads = reactive<Upload[]>([])
const picker = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
// In the Octo app (Lab.swift sets the user agent), Voice Memos can't be picked: iOS lets no app browse them. Point
// at the way that works, Share → Octo from Voice Memos (the app's Memos tab), instead of drag and drop.
const inApp = navigator.userAgent.includes('OctoApp/')
let key = 0

async function add(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    const u = reactive<Upload>({ key: key++, name: file.name, pct: 0, error: '', done: false })
    uploads.unshift(u)
    try {
      await lab.upload(file, 'phone', (f) => { u.pct = f })
      u.done = true
      setTimeout(() => { const i = uploads.indexOf(u); if (i >= 0) uploads.splice(i, 1) }, 1500)
    } catch (e) {
      u.error = e instanceof Error ? e.message : String(e)
    }
    void load()
  }
}
function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void add(input.files)
  input.value = ''
}
function onDrop(e: DragEvent) {
  dragging.value = false
  if (e.dataTransfer?.files.length) void add(e.dataTransfer.files)
}
const restoreError = ref<Record<string, string>>({})
async function restore(id: string) {
  try { await lab.keep(id); restoreError.value = {}; await load() }
  catch (e) { restoreError.value = { [id]: `Couldn't restore it: ${e instanceof Error ? e.message : e}` } }  // on its row (M20)
}

// ---- refreshing ------------------------------------------------------------------------------
const busy = computed(() => uploads.some((u) => !u.done && !u.error)
  || incoming.value.some((i) => i.state === 'processing')
  || (sessions.value ?? []).some((s) => s.todo.kind === 'processing'))

// The last two weeks, then older ones on request: every row works out its state (M35).
const RECENT_DAYS = 14
const withOlder = ref(false)
const older = ref(kept.list?.older ?? 0)
function showOlder() { withOlder.value = true; void load() }
// Set aside as not conversations (B5, 13 Oct): a count at the end, the rows on request.
const asideCount = ref(kept.list?.aside ?? 0)
// When the last one being processed will be ready (B3).
const allDone = computed(() => {
  const ts = (sessions.value ?? []).map((s) => (s.todo.kind === 'processing' ? s.todo.eta ?? 0 : 0))
  const last = Math.max(0, ...ts)
  return last ? etaText(last) : null
})
// Ones that may not be conversations (a guess, octo/aside.py `maybe`): sorted in a few taps on #/sort.
const maybeAside = computed(() => (sessions.value ?? []).filter((s) => s.maybe_aside).length)
const asideRows = ref<AsideRow[] | null>(null)
const asideError = ref('')
async function showAside() {
  try { asideRows.value = await lab.asideList(); asideError.value = '' }
  catch (e) { asideError.value = `Couldn't load them: ${e instanceof Error ? e.message : e}` }
}
async function putBack(id: string) {
  try {
    await lab.aside(id, null)
    asideRows.value = (asideRows.value ?? []).filter((r) => r.id !== id)
    void load()
  } catch (e) { asideError.value = `Couldn't put it back: ${e instanceof Error ? e.message : e}` }
}
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true  // a load in flight when the page closes mustn't keep polling (M35)
let unseen = false  // a load skipped while the window was out of sight
let token = ''  // /sessions/changed when the list was last fetched ('' if it couldn't say)
let fetchedAt = 0
async function load() {
  clearTimeout(timer)
  if (document.hidden) { unseen = true; return }  // nobody's looking: no polling, and it loads again when seen
  try {
    const changed = await lab.sessionsChanged().catch(() => null)  // first, so a step ending meanwhile still moves it
    const [s, inc] = await Promise.all([withOlder.value ? lab.recentSessions(36500) : lab.recentSessions(RECENT_DAYS), lab.incoming()])
    if (!alive) return
    sessions.value = s.sessions
    older.value = s.older
    asideCount.value = s.aside
    incoming.value = inc
    token = changed?.token ?? ''
    fetchedAt = Date.now()
    if (!withOlder.value) kept.list = { sessions: s.sessions, older: s.older, aside: s.aside, incoming: inc }
    error.value = ''
  } catch (e) {
    if (!alive) return
    error.value = `Couldn't load conversations: ${e instanceof Error ? e.message : e}`
  }
  refreshing.value = false
  timer = setTimeout(poll, busy.value ? 3000 : 30000)  // rows move through their steps while busy
}
// While busy, ask what changed (a few ms) and fetch the list (seconds with a long queue) only when it moved, or after
// 30 s for what the token doesn't see, such as an answer given on another device (review 28 Oct, item 3).
async function poll() {
  if (document.hidden) { unseen = true; return }  // nothing at all while out of sight; onVisible loads
  if (busy.value && token && Date.now() - fetchedAt < 30000) {
    const now = await lab.sessionsChanged().catch(() => null)
    if (!alive) return
    if (now?.token === token) { clearTimeout(timer); timer = setTimeout(poll, 3000); return }  // one chain, even if a load ran meanwhile
  }
  void load()
}
onMounted(load)

// The privacy line names what really reads the transcript in this lab (22 Oct): the review's model, and Hindi
// only where something merges its drafts. Without an answer it says what's true of every lab.
const privacy = ref<Status['privacy'] | null>(null)
onMounted(() => { lab.status().then((s) => { privacy.value = s.privacy ?? null }).catch(() => {}) })
// Where every conversation is, all of them (Progress's line until 5 Nov, review item 8): asked once a visit.
const states = ref<States | null>(null)
onMounted(() => { lab.states().then((s) => { states.value = s }).catch(() => {}) })
const leaves = computed(() => {
  const p = privacy.value
  if (!p) return 'Transcript text goes only to the model you chose for reviews, and only once you\'re confirmed in the conversation.'
  if (!p.review) return 'No transcript leaves this Mac until you choose a model for reviews.'
  const hindi = p.hindi ? (p.hindi === p.review ? ' (and, in Hindi, to merge the two drafts)' : '') : ''
  const merged = p.hindi && p.hindi !== p.review ? ` Hindi drafts go to ${p.hindi} to be merged, on the same condition.` : ''
  return p.local
    ? `The transcript stays here too: reviews run on this Mac, with ${p.review}.${merged}`
    : `Transcript text goes to ${p.review} for the review${hindi}, only once you're confirmed in the conversation.${merged}`
})
function onVisible() { if (!document.hidden && unseen) { unseen = false; void load() } }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onUnmounted(() => { alive = false; clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible) })

// Recordings in which nothing was found are one muted line, unfolded on request (M19).
const empty = computed(() => incoming.value.filter((i) => i.state === 'nothing'))
const live = computed(() => incoming.value.filter((i) => i.state !== 'nothing'))
const showEmpty = ref(false)
const emptyLine = computed(() => {
  const n = empty.value.length
  return `${n} recording${n === 1 ? '' : 's'} · ${mss(empty.value.reduce((a, i) => a + i.duration_s, 0))} · no conversation found`
})

const incomingText: Record<Incoming['state'], string> = {
  processing: 'Finding conversations…',
  failed: "Couldn't read it",
  nothing: 'No conversation found in it',
  dropped: "Not kept: your voice wasn't heard",
}
</script>

<template>
  <main class="page" @dragover.prevent="dragging = true" @dragleave.self="dragging = false" @drop.prevent="onDrop">
    <div class="top">
      <h1>Conversations</h1>
      <div class="seg" role="group" aria-label="Show">
        <button type="button" :aria-pressed="show === 'needs'" @click="show = 'needs'">Needs something · {{ needs.length }}</button>
        <button type="button" :aria-pressed="show === 'all'" @click="show = 'all'">All · {{ sessions?.length ?? 0 }}</button>
      </div>
      <a class="btn" :href="href.calendar">Calendar</a>
      <button type="button" class="btn btn-primary" @click="picker?.click()">
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
        Add recordings</button>
      <input ref="picker" type="file" accept="audio/*,.m4a,.mp3,.wav,.aac,.caf,.flac,.ogg,.opus" multiple hidden @change="onPick">
    </div>

    <SearchBox :blur="blur" />

    <!-- The list comes first, the cards after it (on a phone since 22 Oct, the Mac too since 5 Nov, review item 3):
         .home-card's order, below. Only setup stays above while it isn't done. -->
    <SetupBanner />
    <AgendaCard class="home-card" />
    <WeekCard class="home-card" />
    <WorkingOn class="home-card" />
    <FromReviews class="home-card" />

    <div v-if="inApp" class="drop muted"><span>From Voice Memos: open a memo, tap Share, then <b>Octo</b>. Add recordings is for files saved in Files.</span></div>
    <div v-else class="drop muted" :class="{ on: dragging }">
      <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 12V3M5.5 6.5 9 3l3.5 3.5M3 12v2.5h12V12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
      {{ dragging ? 'Drop to add' : 'Drop voice memos anywhere on this page. From an iPhone: Voice Memos → the memo → Share → AirDrop → this Mac.' }}
    </div>

    <section v-if="uploads.length || incoming.length" class="card incoming" aria-label="Incoming" aria-live="polite">
      <div v-for="u in uploads" :key="`u${u.key}`" class="in-row">
        <span class="name">{{ u.name }}</span>
        <span v-if="u.error" class="fail small">{{ u.error === 'already added' ? 'Already added' : u.error }}</span>
        <span v-else-if="u.done" class="muted small">Added</span>
        <span v-else class="up"><span class="up-bar"><span :style="{ width: `${Math.round(u.pct * 100)}%` }" /></span>
          <span class="muted small">{{ u.pct < 1 ? `Uploading ${Math.round(u.pct * 100)}%` : 'Converting…' }}</span></span>
        <button v-if="u.error" type="button" class="link small" @click="uploads.splice(uploads.indexOf(u), 1)">Dismiss</button>
      </div>
      <div v-for="i in [...live, ...(showEmpty ? empty : [])]" :key="i.id" class="in-row">
        <span class="name">{{ i.name }} <span class="muted small">· {{ when(i.started_at) }} · {{ mss(i.duration_s) }}</span></span>
        <span class="small" :class="{ muted: i.state === 'processing', fail: i.state === 'failed' }">{{ incomingText[i.state] }}<template v-if="i.error">: {{ i.error }}</template></span>
        <span v-if="i.state === 'dropped'" class="restore">
          <button v-for="d in i.dropped" :key="d.id" type="button" class="btn btn-small" @click="restore(d.id)">Restore {{ mss(d.duration_s) }}</button>
          <span v-for="d in i.dropped.filter((x) => restoreError[x.id])" :key="`e${d.id}`" class="fail small" role="alert">{{ restoreError[d.id] }}</span>
        </span>
      </div>
      <div v-if="empty.length" class="in-row">
        <span class="name muted small">{{ emptyLine }}</span>
        <button type="button" class="link small" :aria-expanded="showEmpty" @click="showEmpty = !showEmpty">{{ showEmpty ? 'Hide' : 'Show' }}</button>
      </div>
    </section>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="sessions === null" class="muted loading" role="status">Loading…</p>

    <section v-else-if="sessions.length === 0" class="card empty">
      <h2>No conversations yet</h2>
      <p>Add a voice memo of a conversation you're in (tell the others you're recording): Add recordings above, or drop it
        on this page. From an iPhone: Voice Memos → the memo → Share → AirDrop → this Mac. It's transcribed
        and speakers are separated on this Mac; then {{ privacy?.review ?? 'the model you choose' }} reviews the transcript against your principles.</p>
    </section>

    <section v-if="maybeAside >= 2" class="card sort-card" aria-label="May not be conversations">
      <span><strong>{{ maybeAside }} recordings may not be conversations.</strong>
        <span class="muted"> Nobody in them is you: the TV, music, singing?</span></span>
      <a class="btn btn-small" :href="href.sort">Sort them</a>
    </section>

    <p v-if="working && show === 'needs'" class="muted small working">{{ working }} conversation{{ working === 1 ? '' : 's' }} still being processed<template
      v-if="allDone"> · all ready {{ allDone }}</template></p>

    <p v-if="!error && sessions?.length && shown.length === 0" class="muted">Nothing needs you right now. <button type="button" class="link" @click="show = 'all'">Show all</button></p>

    <p v-if="refreshing" class="muted small working" role="status">Updating…</p>
    <section v-if="!error && sessions?.length && shown.length" class="card list" aria-label="Conversations"
             :class="{ blur, refreshing }" :aria-busy="refreshing">
      <a v-for="s in shown" :key="s.id" class="row" :href="href.conversation(s.id)">
        <div class="when">
          <div class="strong">{{ when(s.started_at) }}</div>
          <div class="muted small">{{ mss(s.duration_s) }} · {{ sourceName(s.source) }}<template v-if="(s.part_count ?? 1) > 1"> · {{ s.part_count }} parts</template></div>
        </div>
        <div class="who">
          <div class="chips">
            <span v-for="sp in s.speakers" :key="sp.letter" class="chip" :class="speakerColors(s.speakers)[sp.letter]">{{ sp.name }}</span>
          </div>
          <div v-if="s.preview" class="preview muted">“{{ s.preview }}”</div>
        </div>
        <div class="todo" :class="todoLabel(s.todo).tone">
          <span class="dot" aria-hidden="true" />{{ todoLabel(s.todo).text }}
        </div>
        <svg class="chev" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
      </a>
    </section>

    <p v-if="older && !withOlder" class="muted small">{{ older }} conversation{{ older === 1 ? '' : 's' }} older than two weeks ·
      <button type="button" class="link" @click="showOlder">Show them</button></p>

    <section v-if="asideCount" class="aside" aria-label="Not conversations">
      <p class="muted small">{{ asideCount }} recording{{ asideCount === 1 ? '' : 's' }} set aside as not conversations ·
        <button v-if="asideRows === null" type="button" class="link" @click="showAside">Show them</button>
        <button v-else type="button" class="link" @click="asideRows = null">Hide them</button></p>
      <div v-if="asideRows?.length" class="card list">
        <div v-for="r in asideRows" :key="r.id" class="row aside-row">
          <a class="when" :href="href.conversation(r.id)">
            <div class="strong">{{ when(r.started_at) }}</div>
            <div class="muted small">{{ mss(r.duration_s) }} · {{ sourceName(r.source) }} · {{ ASIDE[r.aside] }}</div>
          </a>
          <button type="button" class="btn btn-small" @click="putBack(r.id)">It's a conversation</button>
        </div>
      </div>
      <p v-if="asideError" class="error small" role="alert">{{ asideError }}</p>
    </section>

    <p v-if="states" class="muted small states">All of them: {{ states.reviewed }} reviewed · {{ states.to_review }} to review ·
      {{ states.waiting_on_you }} waiting on you · {{ states.failed }} failed · {{ states.processing }} processing ·
      {{ states.cant_count }} can't count (too short, without you, or not transcribed) · {{ states.not_counted }} you left out</p>
    <div class="foot muted small">
      <label class="check"><input v-model="blur" type="checkbox"> Blur previews</label>
      <span class="spacer" />
      <span>Audio and voiceprints stay on this Mac. {{ leaves }} A task goes to TickTick only when you press its button.</span>
      <!-- On a phone there's no Setup menu: About Octo is here (22 Oct) -->
      <a v-if="!onMac" class="about" href="/about" target="_blank" rel="noopener">About Octo</a>
    </div>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 1040px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
.top { display: flex; align-items: center; gap: 16px; }
.drop { border: 1.5px dashed var(--line); border-radius: 12px; padding: 12px 16px; font-size: 14px; display: flex; align-items: center; gap: 10px; }
.drop.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.incoming { padding: 6px 0; }
.aside { display: flex; flex-direction: column; gap: 8px; }
.sort-card { display: flex; align-items: center; justify-content: space-between; gap: 8px 16px; flex-wrap: wrap; padding: 14px 18px; }
.sort-card .btn-small { min-height: 44px; display: inline-flex; align-items: center; text-decoration: none; }
.aside .muted.small { margin: 0; }
.aside-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.aside-row .when { color: inherit; text-decoration: none; flex: 1; min-height: 44px; display: flex; flex-direction: column; justify-content: center; }
.aside-row .btn-small { min-height: 44px; }
.in-row { display: flex; align-items: center; gap: 16px; padding: 10px 22px; flex-wrap: wrap; }
.in-row + .in-row { border-top: 1px solid var(--line); }
.in-row .name { font-weight: 500; flex-grow: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.up { display: flex; align-items: center; gap: 10px; }
.up-bar { width: 160px; height: 6px; border-radius: 3px; background: var(--chip); overflow: hidden; }
.up-bar span { display: block; height: 6px; background: var(--accent); }
.fail { color: var(--fail); }
.restore { display: flex; gap: 8px; }
.working { margin: -8px 0 0; }
h1 { margin: 0; font-size: 26px; font-weight: 600; flex-grow: 1; }
.seg { display: flex; padding: 3px; background: var(--chip); border-radius: 10px; }
.seg button { border: 0; background: transparent; color: var(--muted); padding: 8px 14px; min-height: 40px; border-radius: 8px; font-size: 14px; cursor: pointer; }
.seg button[aria-pressed='true'] { background: var(--surface); color: var(--ink); font-weight: 500; }
.list { overflow: hidden; }
.list.refreshing { opacity: 0.55; transition: opacity 0.2s; }
.row {
  display: grid; grid-template-columns: 170px minmax(0, 1fr) 250px 16px; gap: 20px; align-items: center;
  padding: 16px 22px; border-bottom: 1px solid var(--line); color: inherit; text-decoration: none;
}
.row:last-child { border-bottom: 0; }
.row:hover { background: var(--quote); }
.strong { font-weight: 500; }
.small { font-size: 13px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { padding: 1px 10px; border-radius: 999px; font-size: 13px; font-weight: 500; }
.chip { background: var(--c-soft); color: var(--c); }
.preview { font-size: 14px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.blur .preview { filter: blur(5px); }
.todo { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 500; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.todo.muted { color: var(--muted); font-weight: 400; }
.todo.warn { color: var(--warn); }
.todo.accent { color: var(--accent); }
.todo.done { color: var(--pass); font-weight: 400; }
.todo.fail { color: var(--fail); }
.chev { color: var(--muted); }
.muted { color: var(--muted); }
.empty { padding: 24px; }
.empty h2 { margin: 0 0 6px; font-size: 18px; }
.error { color: var(--fail); }
.link { border: 0; background: none; color: var(--accent); cursor: pointer; padding: 0; text-decoration: underline; }
.foot { display: flex; align-items: center; gap: 12px; }
.states { margin: 0; }
.check { display: flex; align-items: center; gap: 8px; min-height: 44px; flex-shrink: 0; white-space: nowrap; }
.check input { width: 18px; height: 18px; accent-color: var(--accent); }
.spacer { flex-grow: 1; }
.home-card { order: 1; }
.drop, .states, .foot { order: 2; }
@media (max-width: 640px) {
  .foot { flex-wrap: wrap; }
}
@media (max-width: 760px) {
  .page { padding: 16px 16px 40px; }
  .top { flex-wrap: wrap; }
  .row { grid-template-columns: minmax(0, 1fr) 16px; gap: 8px 12px; padding: 14px 16px; }
  .row .when { grid-column: 1; }
  .row .who, .row .todo { grid-column: 1; }
  .chev { grid-row: 1 / span 3; grid-column: 2; }
}
</style>
