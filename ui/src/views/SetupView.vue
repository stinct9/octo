<script setup lang="ts">
// Getting started, and editing who you are and what you measure against (product plan 4.2).
// Nothing on this page writes principles: the review measures you against your own words. A new lab is shown two
// examples (31 Oct) that go into the box only on Use this, and are the owner's own text from then on.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import IphoneSetup from '../components/IphoneSetup.vue'
import ModelsKeys from '../components/ModelsKeys.vue'
import SetupStep from '../components/SetupStep.vue'
import { lab } from '../lab/api'
import { href } from '../lab/router'
import type { PipelineStep } from '../lab/types'

interface Profile { name?: string; pronoun?: string; about?: string; principles_from?: string; never?: string }
interface Sections { principles: string; rules: string; parsed: { n: number; title: string }[]; file: string; examples?: { text: string; why: string }[] }
interface Step { id: string; done: boolean; skipped?: boolean; optional?: boolean; why?: Check[]; do?: string; link?: string | null; note?: string | null }
interface Setup { steps: Step[]; complete: boolean; empty?: boolean }
interface Check { mark: '✓' | '!' | '✗'; what: string; detail: string; fix: string }
interface Model { repo: string; state: 'ready' | 'downloading' | 'waiting' | 'needs_access' | 'failed'; gated: boolean; error: string | null; size_gb: number | null; have_gb: number | null }
interface Models { running: boolean; models: Model[] }
interface HfAccount { name: string | null; role?: string | null; error?: string | null; access?: Record<string, string> }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}

const setup = ref<Setup | null>(null)
// Inside the Octo app the page can hand an import to it (macapp/Octo.swift); in a browser it says where to go
const octoApp = (window as unknown as { webkit?: { messageHandlers?: { octo?: { postMessage: (m: string) => void } } } })
  .webkit?.messageHandlers?.octo
const done = (id: string) => !!setup.value?.steps.find((s) => s.id === id)?.done
const doneCount = computed(() => setup.value?.steps.filter((s) => s.done).length ?? 0)
const step = (id: string) => setup.value?.steps.find((s) => s.id === id)
// One step at a time (beta, 31 Oct): the first not done or skipped is the one to do now; the lab checks each.
const now = computed(() => setup.value?.steps.find((s) => !s.done && !s.skipped)?.id)
const stateOf = (id: string) => step(id)?.done ? 'done' : step(id)?.skipped ? 'skipped' : now.value === id ? 'now' : 'later'
const nOf = (id: string) => (setup.value?.steps.findIndex((s) => s.id === id) ?? -1) + 1
let setupTimer: number | undefined
const loadSetup = () => call<Setup>('GET', '/api/octo/settings/setup').then((s) => { setup.value = s }).catch(() => {})
  .finally(() => {  // steps done outside this page (Xcode, the phone, Tailscale, a first conversation) show by themselves
    window.clearTimeout(setupTimer)
    if (!setup.value?.complete) setupTimer = window.setTimeout(loadSetup, 8000)
  })
async function skip(id: string, on: boolean) {
  setup.value = await call<Setup>('POST', '/api/octo/settings/setup/skip', { id, skip: on })
}


// Speech models: downloaded in the background; two need a Hugging Face sign-in (open-source plan I3, I4)
const models = ref<Models | null>(null)
const modelsReady = computed(() => !!models.value && models.value.models.every((m) => m.state === 'ready'))
const gatedMissing = computed(() => (models.value?.models ?? []).filter((m) => m.gated && m.state !== 'ready'))
const totalGb = computed(() => (models.value?.models ?? []).reduce((n, m) => n + (m.size_gb ?? 0), 0))
const short = (repo: string) => repo.split('/')[1]
const stateText = (m: Model) => m.state === 'downloading'
  ? `downloading${m.have_gb != null && m.size_gb ? `, ${Math.min(99, Math.round((m.have_gb / m.size_gb) * 100))}%` : ''}`
  : { ready: 'ready', waiting: 'waiting', needs_access: 'needs your Hugging Face sign-in', failed: 'failed' }[m.state]
let poll: number | undefined
async function loadModels() {
  try { models.value = await call<Models>('GET', '/api/octo/settings/models') } catch { /* the page still works */ }
  window.clearTimeout(poll)
  if (models.value?.running) poll = window.setTimeout(loadModels, 3000)
  if (gatedMissing.value.length && !hf.value && !hfChecking.value) checkHf()
}
async function downloadModels() {
  models.value = await call<Models>('POST', '/api/octo/settings/models/download', {})
  loadModels()
}
const hf = ref<HfAccount | null>(null)
const hfChecking = ref(false)
const hfToken = ref('')
const hfError = ref('')
const accepted = (repo: string) => hf.value?.access?.[repo] === 'ok'
const toAccept = computed(() => gatedMissing.value.filter((m) => !accepted(m.repo)))
let hfTimer: number | undefined
async function checkHf() {
  hfChecking.value = true
  try { hf.value = await call<HfAccount>('GET', '/api/octo/settings/huggingface') } catch { hf.value = { name: null } }
  hfChecking.value = false
  // Conditions are accepted on Hugging Face's site, not here: ask again until they are (the lab then downloads them).
  window.clearTimeout(hfTimer)
  if (hf.value?.name && toAccept.value.length) hfTimer = window.setTimeout(checkHf, 8000)
  else if (hf.value?.name && gatedMissing.value.length) loadModels()
}
async function signInHf() {
  hfError.value = ''
  hfChecking.value = true
  try {
    hf.value = await call<HfAccount>('POST', '/api/octo/settings/huggingface', { token: hfToken.value.trim() })
    hfToken.value = ''
    loadModels()
    checkHf()
  } catch (e) { hfError.value = e instanceof Error ? e.message : String(e) }
  hfChecking.value = false
}
onUnmounted(() => { window.clearTimeout(poll); window.clearTimeout(setupTimer); window.clearTimeout(hfTimer) })

// Try it: a review of a made-up conversation, on your model and principles (octo/sample.py)
interface SampleReview {
  at: number; names: Record<string, string>; lines: [string, string][]
  review: { skipped: string | null; summary: string; one_thing: { headline: string; why: string } | null
    conduct: { principle: number; title: string; held: string | null; observation: string }[]; next: { title: string; why: string }[]; model: string }
}
const sample = ref<SampleReview | null>(null)
const sampleBusy = ref(false)
const sampleError = ref('')
async function runSample() {
  sampleError.value = ''
  sampleBusy.value = true
  try { sample.value = await call<SampleReview>('POST', '/api/octo/settings/sample', {}) } catch (e) { sampleError.value = e instanceof Error ? e.message : String(e) }
  sampleBusy.value = false
}
const heldText: Record<string, string> = { held: 'held', partly: 'partly', missed: 'missed', depends: 'depends' }

// 1. You
const profile = ref<Profile>({ pronoun: 'they' })
const youNote = ref('')
const youError = ref('')
async function saveYou() {
  youError.value = ''
  youNote.value = ''
  try {
    const r = await call<{ profile: Profile }>('POST', '/api/octo/settings/owner', profile.value)
    profile.value = { pronoun: 'they', ...r.profile }
    youNote.value = 'Saved.'
    loadSetup()
  } catch (e) { youError.value = e instanceof Error ? e.message : String(e) }
}

// 2. Your principles
const sections = ref<Sections | null>(null)
const principles = ref('')
const rules = ref('')
const prNote = ref('')
const prError = ref('')
const prDirty = computed(() => !!sections.value && (principles.value !== sections.value.principles || rules.value !== sections.value.rules))
// How the review will read them, from the server's own parser (so the two can't disagree), a moment after typing stops.
const preview = ref<{ n: number; text: string }[]>([])
let previewTimer: ReturnType<typeof setTimeout> | undefined
watch(principles, (text) => {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(async () => {
    try { preview.value = text.trim() ? await call<{ n: number; text: string }[]>('POST', '/api/octo/settings/rubric/preview', { principles: text }) : [] } catch { /* the preview waits for the next keystroke */ }
  }, 300)
})
const unused = computed(() => (sections.value?.examples ?? []).filter((e) => !principles.value.includes(e.text)))
function useExample(text: string) {
  const t = principles.value.trimEnd()
  const n = t.split('\n').filter((l) => /^\s*(\d+[.)]|[-•])\s/.test(l)).length  // not the preview: it lags 300 ms
  principles.value = `${t ? `${t}\n` : ''}${n + 1}. ${text}\n`
}
async function savePrinciples() {
  prError.value = ''
  prNote.value = ''
  try {
    sections.value = await call<Sections>('POST', '/api/octo/settings/rubric', { principles: principles.value, rules: rules.value })
    principles.value = sections.value.principles
    rules.value = sections.value.rules
    prNote.value = `Saved to ${sections.value.file}. Reviews you haven't answered are made again with these; answered ones are kept.`
    loadSetup()
  } catch (e) { prError.value = e instanceof Error ? e.message : String(e) }
}

// 3. A model for reviews
const review = ref<PipelineStep | null>(null)
const pick = ref('')
const modelNote = ref('')
const modelError = ref('')
// Usable choices, the owner's own connections first: having just connected one, that's what they mean.
const usable = computed(() => (review.value?.choices ?? []).filter((c) => !c.unavailable)
  .sort((a, b) => Number(!a.provider) - Number(!b.provider)))
const loadReview = () => lab.pipeline().then((steps) => {
  review.value = steps.find((s) => s.stage === 'insight') ?? null
  pick.value = review.value?.selected ?? usable.value[0]?.id ?? ''
}).catch(() => {})
const current = computed(() => review.value?.choices.find((c) => c.id === review.value?.selected))
const picked = computed(() => review.value?.choices.find((c) => c.id === pick.value))
async function useModel() {
  modelError.value = ''
  try {
    const r = await lab.choose('insight', pick.value)
    review.value = r.steps.find((s) => s.stage === 'insight') ?? null
    modelNote.value = `Reviews use ${review.value?.choices.find((c) => c.id === r.selected)?.label ?? r.selected}.`
    loadSetup()
  } catch (e) { modelError.value = e instanceof Error ? e.message : String(e) }
}

onMounted(async () => {
  loadSetup()
  loadModels()
  call<SampleReview | Record<string, never>>('GET', '/api/octo/settings/sample').then((r) => { if ('review' in r) sample.value = r as SampleReview }).catch(() => {})
  call<{ profile: Profile }>('GET', '/api/octo/settings/owner').then((r) => { profile.value = { pronoun: 'they', ...r.profile } }).catch(() => {})
  call<Sections>('GET', '/api/octo/settings/rubric').then((s) => { sections.value = s; principles.value = s.principles; rules.value = s.rules }).catch(() => {})
  loadReview()
})
</script>

<template>
  <main class="page">
    <div>
      <h1>Set up Octo</h1>
      <p class="muted">One step at a time: each is checked here, and the next opens once it's done. This page is only on the
        Mac.<span v-if="setup && !setup.complete">&nbsp;{{ doneCount }} of {{ setup.steps.length }} done.</span>
        <span v-else-if="setup">&nbsp;All done.</span></p>
    </div>

    <section v-if="setup?.empty" class="card pad" aria-labelledby="s-import">
      <h2 id="s-import">Moving from another Mac?</h2>
      <p>Bring your lab in before filling in this page: it replaces what's here. You'll need the export made on the
        other Mac (File → Export Lab… in the Octo app, or <code>octo export</code>) and its password.</p>
      <div v-if="octoApp"><button type="button" class="btn" @click="octoApp.postMessage('import')">Import…</button></div>
      <p v-else class="small">Use <b>File → Import Lab…</b> in the Octo app, or in Terminal
        <code>octo import "path to the export.dmg"</code>.</p>
    </section>

    <template v-if="setup">
    <SetupStep :n="nOf('mac')" title="This Mac" :state="stateOf('mac')">
      <p v-if="done('mac')" class="muted">This Mac has what the lab needs. More checks, a test notification and calls:
        <a :href="href.mac">This Mac</a>.</p>
      <p v-else>The installer couldn't finish everything. Each line says what to do:</p>
      <template v-if="step('mac')?.why?.length">
        <ul class="checks"><li v-for="c in step('mac')?.why" :key="c.what" :class="c.mark === '✗' ? 'is-bad' : 'is-warn'"><span class="mark" aria-hidden="true">{{ c.mark }}</span>
          <span><b>{{ c.what }}</b>: {{ c.detail }}<span v-if="c.fix" class="fix">{{ c.fix }}</span></span></li></ul>
      </template>
    </SetupStep>

    <SetupStep :n="nOf('models')" title="Speech models" :state="stateOf('models')">

      <p class="muted">Writing down words, telling voices apart and knowing whose voice is whose all run on this Mac, with
        these models (about {{ Math.round(totalGb) }} GB, once).<template v-if="!modelsReady"> The rest of this page works while
        they come down.</template></p>
      <ul v-if="models && !modelsReady" class="checks">
        <li v-for="m in models.models" :key="m.repo" :class="{ 'is-bad': m.state === 'failed', 'is-warn': m.state === 'needs_access' }">
          <span class="mark" aria-hidden="true">{{ m.state === 'ready' ? '✓' : m.state === 'downloading' ? '↓' : m.state === 'failed' ? '✗' : '·' }}</span>
          <span><b>{{ short(m.repo) }}</b>: {{ stateText(m) }}<span v-if="m.error && m.state === 'failed'" class="fix">{{ m.error }}</span></span>
        </li>
      </ul>
      <div v-if="models && !modelsReady && !models.running" class="line">
        <button type="button" class="btn" @click="downloadModels">Download what's missing</button>
      </div>

      <div v-if="gatedMissing.length" class="hf">
        <p><b>Two of them need a free Hugging Face account.</b> Hugging Face is where researchers share models like these.
          The makers of these two (pyannote: they tell voices apart) share them free, on condition that you accept their
          terms and say where you work or study. It's approved at once. Octo respects that rather than shipping the files itself.</p>
        <ol class="hf-steps">
          <li :class="{ done: hf?.name }"><b>An account.</b> Make one at
            <a href="https://huggingface.co/join" target="_blank" rel="noopener">huggingface.co/join</a> (an email and a
            password, then click the link in the email they send), or sign in if you have one.</li>
          <li :class="{ done: hf?.name }"><b>A token for this Mac.</b> A long code, like a password, that lets this Mac download the
            two models for you. Open <a href="https://huggingface.co/settings/tokens/new?tokenType=read" target="_blank" rel="noopener">Access Tokens</a>,
            choose <b>Read</b>, type any name (say "Octo"), click <b>Create token</b>, then <b>Copy</b>, and paste it here.
            <form v-if="!hf?.name" class="line" @submit.prevent="signInHf">
              <label class="field grow">Read token<input v-model="hfToken" type="password" autocomplete="off" spellcheck="false" placeholder="hf_…"></label>
              <button type="submit" class="btn" :disabled="hfChecking || hfToken.trim().length < 8">{{ hfChecking ? 'Checking…' : 'Sign in' }}</button>
            </form>
            <p v-else class="small">Signed in as <b>{{ hf.name }}</b>.</p>
            <p v-if="hfError" class="error small" role="alert">{{ hfError }}</p>
          </li>
          <li :class="{ done: hf?.name && !toAccept.length }"><b>Accept each model's conditions,</b> in the browser where you're signed in to Hugging Face:
            <ol class="how small">
              <li>Open the model's page (below). If the page asks you to log in, do that first.</li>
              <li>Near the top is a box: <i>You need to agree to share your contact information to access this model.</i></li>
              <li>Fill in <b>Company/university</b> (where you work or study, or "Personal") and <b>Website</b> (your
                company's site, or any page about you).</li>
              <li>Click <b>Agree and access repository</b>. The box then says you've been granted access.</li>
            </ol>
            <ul class="gated">
              <li v-for="m in gatedMissing" :key="m.repo">
                <a :href="`https://huggingface.co/${m.repo}`" target="_blank" rel="noopener">{{ short(m.repo) }}</a>
                <span v-if="!hf?.name" class="muted small">Octo can tell once your token is in.</span>
                <span v-else-if="accepted(m.repo)" class="ok small">✓ Accepted</span>
                <span v-else class="warn small">Not accepted yet</span>
              </li>
            </ul>
            <p class="muted small">pyannote's form says they may email you now and then about their paid services.</p>
            <p v-if="hf?.name && toAccept.length" class="muted small" aria-live="polite">This page checks again every few
              seconds{{ hfChecking ? '…' : '.' }} Once both say Accepted, they download by themselves.</p>
            <p v-else-if="hf?.name && !models?.running" class="small"><button type="button" class="btn btn-small" @click="downloadModels">Download them</button></p>
          </li>
        </ol>
        <p v-if="hf?.name && hf.role === 'write'" class="warn small">This is a Write token. It works, but a Read token is
          safer: it can't change anything on your Hugging Face account. You can make a Read one and sign in with it instead.</p>
        <p v-if="hf?.name && hf.role === 'fineGrained' && toAccept.length" class="warn small">
          This token is a limited ("fine-grained") one and may not be allowed to download these models. If accepting the
          conditions doesn't help, make a token of type <b>Read</b> and sign in with it.</p>
        <p class="muted small">The token stays on this Mac (where Hugging Face's own tools keep it) and is only used to
          download models.</p>
      </div>
        </SetupStep>

    <SetupStep :n="nOf('you')" title="You" :state="stateOf('you')">

      <p class="muted">The reviews and the coach write to you as "you"; these lines tell them who that is.</p>
      <form class="grid" @submit.prevent="saveYou">
        <label class="field">Your name<input v-model="profile.name" type="text" autocomplete="name"></label>
        <label class="field">Refer to you as
          <select v-model="profile.pronoun"><option value="they">they</option><option value="she">she</option><option value="he">he</option></select>
        </label>
        <label class="field wide">Who you are, in a line (optional)
          <input v-model="profile.about" type="text" placeholder="e.g. a nurse who leads a ward team"></label>
        <label class="field wide">Where your principles come from (optional)
          <input v-model="profile.principles_from" type="text" placeholder="a teaching, a tradition, your own experience…"></label>
        <label class="field wide">Advice you never want, whatever it would gain you (optional)
          <input v-model="profile.never" type="text"></label>
        <div class="line wide"><button type="submit" class="btn" :disabled="!profile.name?.trim()">Save</button>
          <span v-if="youError" class="error small" role="alert">{{ youError }}</span>
          <span v-else-if="youNote" class="ok small" aria-live="polite">{{ youNote }}</span></div>
      </form>
        </SetupStep>

    <SetupStep :n="nOf('principles')" title="Your principles" :state="stateOf('principles')">

      <p class="muted">The review measures how you showed up against these, in your own words. Start each one on its own
        line with a number ("1.") or a dash; a line right under it belongs to it, and headings between them are fine.</p>
      <label class="field">Principles
        <textarea v-model="principles" rows="10" spellcheck="true" placeholder="1. …"></textarea></label>
      <div v-if="unused.length && !done('principles')" class="examples">
        <p class="small"><b>Not sure where to start?</b> <span class="muted">Writing your own is best. These are examples: one
          goes into the box only if you tap Use this, and then it's yours to change.</span></p>
        <div class="example-cards">
          <div v-for="e in unused" :key="e.text" class="example">
            <p>{{ e.text }}</p>
            <p class="muted small">{{ e.why }}</p>
            <button type="button" class="btn btn-small" @click="useExample(e.text)">Use this</button>
          </div>
        </div>
      </div>
      <div v-if="preview.length" class="small">
        <span class="muted">The review will read {{ preview.length }} principle{{ preview.length === 1 ? '' : 's' }}:</span>
        <ol class="preview"><li v-for="p in preview" :key="p.n" :value="p.n">{{ p.text }}</li></ol>
      </div>
      <label class="field">How you want the review done, in your own words (optional)
        <textarea v-model="rules" rows="4" spellcheck="true"></textarea></label>
      <div class="line"><button type="button" class="btn" :disabled="!prDirty || !preview.length" @click="savePrinciples">Save</button>
        <span v-if="prError" class="error small" role="alert">{{ prError }}</span>
        <span v-else-if="prNote" class="ok small" aria-live="polite">{{ prNote }}</span>
        <span v-else-if="principles.trim() && !preview.length" class="muted small">No principles found yet: start each line
          with a number or a dash.</span></div>
        </SetupStep>

    <SetupStep :n="nOf('model')" title="A model for reviews" :state="stateOf('model')">

      <p class="muted">The review reads the conversation's text; you choose which model: Claude Code on this Mac, on your own
        Claude plan, or a model on an API key, which its maker bills per use.</p>
      <p v-if="current">Reviews use <b>{{ current.label }}</b>. <a :href="href.pipeline">Change it on the Pipeline page</a>; keys are in <a :href="href.settings">Settings</a>.</p>
      <template v-else>
        <ModelsKeys @changed="loadReview" />
        <div v-if="usable.length" class="line">
          <label class="field grow">Review with
            <select v-model="pick" aria-describedby="pick-why"><option v-for="c in usable" :key="c.id" :value="c.id">{{ c.label }}</option></select>
          </label>
          <button type="button" class="btn" :disabled="!pick" @click="useModel">Use it</button>
        </div>
        <p v-if="picked" id="pick-why" class="muted small">{{ picked.why }}</p>
        <p v-if="modelError" class="error small" role="alert">{{ modelError }}</p>
      </template>
      <p v-if="modelNote" class="ok small" aria-live="polite">{{ modelNote }}</p>
          <div class="try">
        <h3>Try it on a made-up conversation</h3>

      <p class="muted">Before you record anyone: a short invented 1:1 between you and a team lead, reviewed against your
        principles by your model. It sends that made-up text (about 300 words) to your model once, on your key, and counts
        toward nothing.</p>
      <p v-if="!done('principles') || !done('model')" class="muted small">Write your principles and choose a model first.</p>
      <div v-else class="line">
        <button type="button" class="btn" :disabled="sampleBusy" @click="runSample">{{ sampleBusy ? 'Reviewing… (up to a minute)' : sample ? 'Review it again' : 'Review the sample' }}</button>
        <span v-if="sampleError" class="error small" role="alert">{{ sampleError }}</span>
      </div>
      <div v-if="sample" class="sample">
        <p v-if="sample.review.skipped" class="muted">{{ sample.review.skipped }}</p>
        <template v-else>
          <p>{{ sample.review.summary }}</p>
          <div v-if="sample.review.one_thing" class="one"><span class="k">The one thing</span>
            <b>{{ sample.review.one_thing.headline }}</b><span class="muted small">{{ sample.review.one_thing.why }}</span></div>
          <ul v-if="sample.review.conduct.length" class="checks">
            <li v-for="c in sample.review.conduct" :key="c.principle"><span class="mark" aria-hidden="true">{{ c.principle }}</span>
              <span><b>{{ c.title }}</b><template v-if="c.held"> · {{ heldText[c.held] ?? c.held }}</template><span class="fix">{{ c.observation }}</span></span></li>
          </ul>
          <p v-if="sample.review.next.length" class="small"><b>Next time:</b> {{ sample.review.next.map((n) => n.title).join(' · ') }}</p>
          <p class="muted small">Reviewed by {{ sample.review.model }}. A real review also reads the others, what went unsaid
            and your numbers, with every quote checked against the transcript.</p>
        </template>
        <details class="small"><summary class="muted">The made-up conversation</summary>
          <p v-for="([who, text], i) in sample.lines" :key="i" class="line-text"><b>{{ sample.names[who] }}:</b> {{ text }}</p>
        </details>
      </div>
          </div>
    </SetupStep>

    <SetupStep :n="nOf('iphone')" title="The Octo app on your iPhone" :state="stateOf('iphone')" :note="step('iphone')?.note" optional @skip="(on) => skip('iphone', on)">
      <IphoneSetup @changed="loadSetup" />
    </SetupStep>

    <SetupStep :n="nOf('pair')" title="Connect the app to this Mac" :state="stateOf('pair')" optional @skip="(on) => skip('pair', on)">
      <p>On this Mac, open the <a :href="href.phone">Phone page</a> and press <b>Show the code</b>. On your iPhone, open the
        Octo app, tap <b>Lab</b>, and scan the code. Both need to be on the same Wi-Fi. This step is done once the app has
        connected.</p>
      <ul v-if="step('pair')?.why?.length" class="checks"><li v-for="c in step('pair')?.why" :key="c.what" class="is-bad">
        <span class="mark" aria-hidden="true">✗</span><span><b>{{ c.what }}</b>: {{ c.detail }}<span class="fix">{{ c.fix }}</span></span></li></ul>
    </SetupStep>

    <SetupStep :n="nOf('away')" title="Your lab away from home" :state="stateOf('away')" :note="step('away')?.note" optional @skip="(on) => skip('away', on)">
      <p class="muted">Tailscale is a free app that links your iPhone to this Mac privately when you're not on the same Wi-Fi.
        Only needed to see your lab when you're out.</p>
      <p v-if="step('away')?.do"><b>Next:</b> {{ step('away')?.do }}
        <a v-if="step('away')?.link" :href="step('away')?.link ?? ''" target="_blank" rel="noopener">Get it</a></p>
      <p class="small">More on the <a :href="href.phone">Phone page</a> (Away from home).</p>
    </SetupStep>

    <SetupStep :n="nOf('voice')" title="Your voice" :state="stateOf('voice')">

      <p class="muted">Record a conversation you're in on your iPhone's Voice Memos, and tell the others first. Send it to this Mac
        (the memo → Share → AirDrop → this Mac), then <a :href="href.conversations">Conversations</a> → Add recordings, or drop it
        there. Or let the recorder bring one in. When it has been through, open it and answer "Is this you?" on your speaker: from
        then on the lab knows your voice, and reviews start.</p>
        </SetupStep>
    </template>

  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
p { margin: 0; }
.n { width: 24px; height: 24px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--line-strong); font-size: 13px; color: var(--ink-2); }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.warn { color: var(--warn); }
.error { color: var(--fail); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.wide { grid-column: 1 / -1; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.field input, .field textarea, .field select { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
.field textarea { line-height: 1.5; resize: vertical; }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.grow { flex: 1; min-width: 220px; }
.checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.checks li { display: flex; gap: 10px; align-items: baseline; }
.checks .mark { width: 16px; flex-shrink: 0; text-align: center; font-weight: 600; }
.checks .is-bad .mark { color: var(--fail); }
.checks .is-warn .mark { color: var(--warn); }
.checks .fix { display: block; color: var(--muted); font-size: 13px; margin-top: 2px; overflow-wrap: anywhere; }
details summary { cursor: pointer; }
details .checks { margin-top: 8px; }
.examples { display: flex; flex-direction: column; gap: 8px; }
.examples p { margin: 0; }
.example-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.example { border: 1px dashed var(--line-strong); border-radius: 8px; padding: 12px; display: flex; flex-direction: column;
  gap: 6px; align-items: flex-start; }
.example .btn { margin-top: auto; }
.hf { display: flex; flex-direction: column; gap: 10px; padding-top: 4px; border-top: 1px solid var(--line); }
.hf-steps { margin: 0; padding-left: 22px; display: flex; flex-direction: column; gap: 10px; }
.hf-steps > li > * + * { margin-top: 6px; }
.hf-steps > li.done::marker { color: var(--pass); }
.how { margin: 6px 0 0; padding-left: 20px; display: flex; flex-direction: column; gap: 2px; }
.gated { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.gated li { display: flex; gap: 12px; align-items: baseline; }
.sample { display: flex; flex-direction: column; gap: 10px; padding-top: 8px; border-top: 1px solid var(--line); }
.one { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 8px; background: var(--quote); }
.one .k { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.line-text { margin-top: 4px; }
.try { display: flex; flex-direction: column; gap: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
h3 { margin: 0; font-size: 15px; font-weight: 600; }
.preview { margin: 6px 0 0; padding-left: 22px; display: flex; flex-direction: column; gap: 3px; color: var(--ink-2); }
@media (max-width: 640px) { .page { padding: 20px 16px 40px; } }
</style>
