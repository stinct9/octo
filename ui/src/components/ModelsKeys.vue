<script setup lang="ts">
// Models and keys (product plan 4.1): the services the review, the Hindi merge and the coach can run on.
// Keys go in and are never shown again: the server returns only their last four characters.
import { computed, onMounted, ref } from 'vue'
import ReviewModel from './ReviewModel.vue'

interface Connection {
  id: string
  kind: 'anthropic' | 'openai'
  label: string
  key: string | null
  base_url?: string
  models: string[]
  daily_calls?: number
}
interface ClaudeModel { id: string; label: string; price: [number, number]; why: string }
// Claude Code on this Mac (31 Oct): the owner's own Claude plan, told apart from an API key billed per use.
interface ClaudeCode {
  path: string | null
  version: string | null
  version_ok: boolean
  logged_in: boolean
  auth: string | null
  plan: string | null
  fix: string | null
  in_use: boolean
}
interface View {
  connections: Connection[]
  claude_code: ClaudeCode
  claude_models: ClaudeModel[]
  review: string | null
  blocked: string | null
  first_lab?: boolean
  added?: Connection
  tested?: { ok: boolean; model?: string; seconds?: number; error?: string }
}

const emit = defineEmits<{ changed: [] }>()
// The review's model is chosen with ReviewModel: on Set up Octo right under this, here inside it (review 5 Nov, item 9)
const props = defineProps<{ inSetup?: boolean }>()
const picker = ref<InstanceType<typeof ReviewModel> | null>(null)
const view = ref<View | null>(null)
const error = ref('')
const note = ref('')
const busy = ref(false)

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}
async function run(fn: () => Promise<unknown>, done = '') {
  busy.value = true
  error.value = ''
  note.value = ''
  try {
    const got = await fn()
    if (got && typeof got === 'object' && 'connections' in got) view.value = got as View
    if (done) { emit('changed'); void picker.value?.load() }
    note.value = done
    return true
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    return false
  } finally {
    busy.value = false
  }
}
const load = () => run(() => call<View>('GET', '/api/octo/settings/providers'))
onMounted(load)

// Adding a service
const kind = ref<'anthropic' | 'openai'>('anthropic')
const label = ref('')
const key = ref('')
const baseUrl = ref('')
const models = ref('')
const modelList = computed(() => models.value.split(/[\n,]/).map((m) => m.trim()).filter(Boolean))
const canAdd = computed(() => kind.value === 'anthropic' ? !!key.value.trim() : !!baseUrl.value.trim() && modelList.value.length > 0)
async function add() {
  let t: View['tested']
  const ok = await run(async () => {
    const got = await call<View>('POST', '/api/octo/settings/providers', {
      kind: kind.value, label: label.value, api_key: key.value, base_url: baseUrl.value, models: modelList.value,
    })
    t = got.tested
    return got
  }, 'Connected.')
  if (!ok) return
  key.value = ''; label.value = ''; baseUrl.value = ''; models.value = ''  // don't keep the key in the page
  // The server tested it before keeping it (a refused key isn't kept): say what it found.
  note.value = t?.ok ? `Connected and tested: ${t.model} answered in ${t.seconds} s. Now choose it as the model for reviews.`
    : t?.error ? `Connected, but the test didn't get through: ${t.error}. Press Test to try again.`
      : 'Connected, but the test answer wasn\'t as asked. Press Test to see.'
}

// One service
const testModel = ref<Record<string, string>>({})
const tested = ref<Record<string, string>>({})
const firstModel = (c: Connection) => c.kind === 'anthropic' ? 'claude-haiku-4-5' : c.models[0]  // the cheapest to ask
async function test(c: Connection) {
  const model = testModel.value[c.id] || firstModel(c)
  tested.value = { ...tested.value, [c.id]: 'Asking…' }
  try {
    const r = await call<{ ok: boolean; model: string; seconds: number }>('POST', `/api/octo/settings/providers/${encodeURIComponent(c.id)}/test`, { model })
    tested.value = { ...tested.value, [c.id]: r.ok ? `Works: ${r.model} answered in ${r.seconds} s.` : `${r.model} answered, but not as asked.` }
  } catch (e) {
    tested.value = { ...tested.value, [c.id]: e instanceof Error ? e.message : String(e) }
  }
}
const newKey = ref<Record<string, string>>({})
async function replaceKey(c: Connection) {
  const ok = await run(() => call<View>('POST', `/api/octo/settings/providers/${encodeURIComponent(c.id)}`, { api_key: newKey.value[c.id] }), 'Key replaced.')
  if (ok) newKey.value = { ...newKey.value, [c.id]: '' }
}
// A budget of calls a day on a connection (review 8 Oct, finding 15): reviews and Hindi merges on it wait for
// midnight once it's used. Empty: none.
const budget = ref<Record<string, string>>({})
async function saveBudget(c: Connection) {
  const n = Number(budget.value[c.id] || 0)
  if (!Number.isInteger(n) || n < 0) { error.value = 'A budget is a whole number of calls a day, or empty for none'; return }
  await run(() => call<View>('POST', `/api/octo/settings/providers/${encodeURIComponent(c.id)}`, { daily_calls: n }),
    n ? `${c.label}: up to ${n} calls a day.` : `${c.label}: no daily budget.`)
}
const confirmRemove = ref<string | null>(null)
const remove = (c: Connection) => run(() => call<View>('POST', `/api/octo/settings/providers/${encodeURIComponent(c.id)}/remove`, {}), `${c.label} removed, with its key.`)
  .then(() => { confirmRemove.value = null })
const inUse = (c: Connection) => !!view.value?.review?.startsWith(`${c.id}:`)
const planName = (cc: ClaudeCode) => cc.plan ? `Claude ${cc.plan[0].toUpperCase()}${cc.plan.slice(1)} plan` : 'your Claude login'
const fixCommand = (fix: string) => fix.slice(fix.indexOf(': ') + 2)
const price = (m: ClaudeModel) => `$${m.price[0]} in · $${m.price[1]} out per million tokens`
</script>

<template>
  <section class="card pad" aria-labelledby="mk">
    <div class="row-head">
      <h2 id="mk">Models and keys</h2>
      <span v-if="view && !view.blocked" class="ok small">Reviews have a model</span>
    </div>
    <p class="muted"><template v-if="!view || view.first_lab">The review, the Hindi merge and the coach send</template><template v-else>The review
      and the coach send</template> a conversation's <b>text</b> (the transcript, names and numbers)
      to the model you choose<template v-if="view && !view.first_lab">, and so does merging the two drafts of a Hindi one</template>. The audio and voiceprints never leave this Mac. There are two ways to Claude: Claude Code on
      this Mac, on your own Claude plan, or an Anthropic API key, billed per use. Or another service.</p>
    <p v-if="view?.blocked" class="warn" role="status">Reviews are waiting for a model: set up one of these, then choose it
      <template v-if="props.inSetup">here, below.</template><template v-else>in <b>Review with</b>, below.</template></p>
    <ReviewModel v-if="!props.inSetup" ref="picker" />

    <div v-if="view" class="service" aria-labelledby="cc-h">
      <div class="row-head">
        <div><b id="cc-h">Claude Code on this Mac: your Claude Pro or Max plan</b>
          <span v-if="view.claude_code.in_use" class="tag">reviews use it</span></div>
        <span v-if="!view.claude_code.fix" class="ok small">Ready</span>
      </div>
      <p class="muted small">Runs the <span class="mono">claude</span> command, logged in with your Claude account: no bill per use,
        within your plan's limits, which it shares with your own Claude use (at most 60 calls a day here). Your plan's terms
        are between you and Anthropic.</p>
      <ul class="checks small">
        <li><span :class="view.claude_code.path ? 'ok' : 'error'">{{ view.claude_code.path ? '✓' : '✗' }}</span>
          Installed<template v-if="view.claude_code.path">: <span class="mono">{{ view.claude_code.path }}</span></template></li>
        <li v-if="view.claude_code.path"><span :class="view.claude_code.version_ok ? 'ok' : 'error'">{{ view.claude_code.version_ok ? '✓' : '✗' }}</span>
          Version {{ view.claude_code.version ?? 'unknown' }} (2.1.280 or later, for Claude Opus 5.5)</li>
        <li v-if="view.claude_code.version_ok"><span :class="view.claude_code.logged_in ? 'ok' : 'error'">{{ view.claude_code.logged_in ? '✓' : '✗' }}</span>
          {{ view.claude_code.logged_in ? `Logged in: ${planName(view.claude_code)}` : 'Not logged in' }}</li>
      </ul>
      <p v-if="view.claude_code.logged_in && view.claude_code.auth && view.claude_code.auth !== 'claude.ai'" class="warn small">
        Claude Code here is logged in with an API key, not a Claude plan: Anthropic bills that per use.</p>
      <p v-if="view.claude_code.fix" class="small">{{ view.claude_code.fix.slice(0, view.claude_code.fix.indexOf(': ')) }}, in Terminal:
        <code class="mono">{{ fixCommand(view.claude_code.fix) }}</code>, then <button type="button" class="btn btn-small" :disabled="busy" @click="load">Check again</button></p>
      <p v-else-if="!view.claude_code.in_use" class="muted small">To use it, choose “Claude Code on this Mac” for reviews
        <template v-if="props.inSetup">here, below.</template><template v-else>in <b>Review with</b>, above.</template></p>
    </div>

    <p v-if="!view && !error" class="muted loading" role="status">Loading…</p>
    <ul v-if="view?.connections.length" class="services">
      <li v-for="c in view.connections" :key="c.id" class="service">
        <div class="row-head">
          <div>
            <b>{{ c.label }}</b>
            <span class="muted small"> · {{ c.kind === 'anthropic' ? 'Anthropic API key: billed per use' : 'OpenAI-compatible' }}</span>
            <span v-if="inUse(c)" class="tag">reviews use it</span>
          </div>
          <span class="mono small muted">{{ c.key ? `key ${c.key}` : 'no key' }}</span>
        </div>
        <div v-if="c.base_url" class="mono small muted">{{ c.base_url }}</div>
        <div class="small">Models: {{ c.models.join(', ') }}</div>
        <form class="line" @submit.prevent="saveBudget(c)">
          <label class="small" :for="`budget-${c.id}`">Calls a day, at most</label>
          <input :id="`budget-${c.id}`" :value="budget[c.id] ?? (c.daily_calls ? String(c.daily_calls) : '')" inputmode="numeric"
                 placeholder="no limit" class="budget" :disabled="busy" @input="budget[c.id] = ($event.target as HTMLInputElement).value">
          <button type="submit" class="btn btn-small" :disabled="busy">Save</button>
          <span class="muted small">a review is one call, a Hindi conversation one per chunk</span>
        </form>
        <div class="line">
          <select :value="testModel[c.id] || firstModel(c)" :aria-label="`Model to test on ${c.label}`" class="small-select"
                  @change="testModel[c.id] = ($event.target as HTMLSelectElement).value">
            <option v-for="m in c.models" :key="m" :value="m">{{ m }}</option>
          </select>
          <button type="button" class="btn btn-small" :disabled="busy" @click="test(c)">Test</button>
          <span v-if="tested[c.id]" class="small" aria-live="polite">{{ tested[c.id] }}</span>
        </div>
        <form class="line" @submit.prevent="replaceKey(c)">
          <input v-model="newKey[c.id]" type="password" autocomplete="off" spellcheck="false" placeholder="New key"
                 :aria-label="`New key for ${c.label}`" class="grow" :disabled="busy">
          <button type="submit" class="btn btn-small" :disabled="busy || !newKey[c.id]?.trim()">Replace key</button>
          <button v-if="confirmRemove !== c.id" type="button" class="btn btn-small" :disabled="busy" @click="confirmRemove = c.id">Remove…</button>
          <template v-else>
            <span class="small">{{ inUse(c) ? 'Reviews will wait for another model.' : 'Remove it and its key?' }}</span>
            <button type="button" class="btn btn-small" @click="confirmRemove = null">Cancel</button>
            <button type="button" class="btn btn-small danger" :disabled="busy" @click="remove(c)">Remove</button>
          </template>
        </form>
      </li>
    </ul>

    <form v-if="view" class="add" @submit.prevent="add">
      <fieldset class="kinds">
        <legend class="small muted">Or connect a service with a key</legend>
        <label><input v-model="kind" type="radio" value="anthropic"> Anthropic API key: billed per use</label>
        <label><input v-model="kind" type="radio" value="openai"> OpenAI-compatible</label>
      </fieldset>
      <template v-if="kind === 'anthropic'">
        <p class="muted small">Create a key at console.anthropic.com → Settings → API keys. You pay Anthropic for what the reviews use;
          on Claude Opus 5.5 a review has cost about $0.10 plus $0.006 a minute of conversation (14 measured).</p>
        <table class="prices small">
          <tr v-for="m in view.claude_models" :key="m.id"><th scope="row">{{ m.label }}</th><td>{{ price(m) }}</td></tr>
        </table>
      </template>
      <template v-else>
        <p class="muted small">OpenAI, Google's OpenAI-compatible endpoint, OpenRouter, or a model server on this Mac (LM Studio,
          Ollama's /v1). Models not tested with Octo say so <template v-if="props.inSetup">when you choose one</template><template
          v-else>in <b>Review with</b></template>.</p>
        <div class="grid">
          <label class="field">Name<input v-model="label" type="text" placeholder="e.g. OpenRouter" :disabled="busy"></label>
          <label class="field">Base URL<input v-model="baseUrl" type="url" placeholder="https://…/v1" spellcheck="false" :disabled="busy"></label>
        </div>
        <label class="field">Models, as the service spells them (comma or one per line)
          <textarea v-model="models" rows="2" spellcheck="false" :disabled="busy"></textarea>
        </label>
      </template>
      <div class="line">
        <label class="field grow">{{ kind === 'anthropic' ? 'API key' : 'API key (if it needs one)' }}
          <input v-model="key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste the key" :disabled="busy">
        </label>
        <button type="submit" class="btn" :disabled="busy || !canAdd">{{ busy ? 'Saving…' : 'Connect' }}</button>
      </div>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="note" class="ok small" aria-live="polite">{{ note }}</p>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.warn { padding: 10px 12px; border-radius: 10px; background: var(--accent-soft); color: var(--ink); }
.services { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.service { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; }
.tag { margin-left: 8px; font-size: 11px; padding: 2px 7px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
.line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.grow { flex: 1; min-width: 200px; }
.add { display: flex; flex-direction: column; gap: 10px; padding-top: 12px; border-top: 1px solid var(--line); }
.kinds { border: 0; margin: 0; padding: 0; display: flex; gap: 16px; flex-wrap: wrap; font-size: 14px; }
.kinds legend { padding: 0 0 6px; }
.kinds input { accent-color: var(--accent); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.line .field { flex-direction: column; }
.field input, .field textarea, .small-select { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
.line input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; min-height: 36px; }
.prices { border-collapse: collapse; }
.prices th { text-align: left; font-weight: 500; padding: 2px 16px 2px 0; }
.prices td { color: var(--muted); }
.danger { color: var(--fail); }
.checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
code { overflow-wrap: anywhere; }
.mono { font-family: var(--mono, ui-monospace, monospace); }
.budget { width: 6.5em; }
</style>
