<script setup lang="ts">
import { onMounted, ref } from 'vue'
import CalendarSettings from '../components/CalendarSettings.vue'
import ExportImport from '../components/ExportImport.vue'
import ModelsKeys from '../components/ModelsKeys.vue'
import RecorderSettings from '../components/RecorderSettings.vue'

interface TickTick {
  connected: boolean
  list_id?: string | null
  list?: string
  timezone?: string | null
  token_ends?: string
  lists?: { id: string; name: string }[] | null
  error?: string | null
}

const tt = ref<TickTick | null>(null)
const token = ref('')
const error = ref('')
const busy = ref(false)
const saved = ref('')

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail ?? res.statusText)
  return data as T
}
async function run(fn: () => Promise<TickTick>, done = '') {
  busy.value = true
  error.value = ''
  saved.value = ''
  try {
    tt.value = await fn()
    saved.value = done
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
const load = () => run(() => call<TickTick>('GET', '/api/octo/settings/ticktick'))
async function connect() {
  await run(() => call<TickTick>('POST', '/api/octo/settings/ticktick', { token: token.value }), 'Connected.')
  if (!error.value) token.value = ''  // don't keep it in the page
}
const chooseList = (id: string) => run(() => call<TickTick>('POST', '/api/octo/settings/ticktick/list', { list_id: id || null }), 'Saved.')
const disconnect = () => run(() => call<TickTick>('POST', '/api/octo/settings/ticktick/disconnect', {}))

// Talk it through's effort (B31): medium until changed.
const talk = ref<{ effort: string; efforts: string[] } | null>(null)
const talkError = ref('')
const EFFORT_SAYS: Record<string, string> = {
  low: 'Quickest, shallower replies',
  medium: 'In between (the default)',
  high: 'Deepest, a minute or two',
}
async function setTalk(effort: string) {
  talkError.value = ''
  try {
    talk.value = await call('POST', '/api/octo/settings/talk', { effort })
  } catch (e) {
    talkError.value = e instanceof Error ? e.message : String(e)
  }
}
onMounted(load)
onMounted(async () => { talk.value = await call<{ effort: string; efforts: string[] }>('GET', '/api/octo/settings/talk').catch(() => null) })
</script>

<template>
  <main class="page">
    <div>
      <h1>Settings</h1>
      <p class="muted">The models your reviews run on, connections to your other tools, and exporting your lab. This page is only on the Mac.
        Your name and principles are on <a href="#/setup">Set up Octo</a>.</p>
    </div>

    <ModelsKeys />
    <RecorderSettings />

    <section class="card pad" aria-labelledby="talk">
      <h2 id="talk">Talk it through</h2>
      <p class="muted">How hard your review's model thinks before each reply when you talk a conversation through.
        Less is quicker; more goes deeper. Reviews themselves aren't affected.</p>
      <p v-if="!talk" class="muted loading" role="status">Loading…</p>
      <div v-else class="choices" role="radiogroup" aria-label="Effort">
        <label v-for="e in talk.efforts" :key="e" class="choice">
          <input type="radio" name="talk-effort" :value="e" :checked="talk.effort === e" @change="setTalk(e)">
          <span><b class="cap">{{ e }}</b> <span class="muted small">{{ EFFORT_SAYS[e] }}</span></span>
        </label>
      </div>
      <p v-if="talkError" class="error" role="alert">{{ talkError }}</p>
    </section>

    <section class="card pad" aria-labelledby="tt">
      <div class="row-head">
        <h2 id="tt">TickTick</h2>
        <span v-if="tt?.connected" class="ok small">Connected</span>
      </div>
      <p class="muted">Each task in a review's "What to do" gets an <b>Add to TickTick</b> button. It sends the task's title,
        details, why, due date and a link back to the conversation, tagged <span class="mono">conversation-lab</span>.
        Nothing is sent until you press it.</p>

      <p v-if="!tt" class="muted loading" role="status">Loading…</p>
      <template v-else-if="!tt.connected">
        <ol>
          <li>Open TickTick on the web: your avatar → <b>Settings</b> → <b>Account</b> → <b>API Token</b>.</li>
          <li>Create a token, copy it, and paste it here.</li>
        </ol>
        <form class="connect" @submit.prevent="connect">
          <label class="field">API token
            <input v-model="token" type="password" autocomplete="off" spellcheck="false" placeholder="Paste the token" :disabled="busy">
          </label>
          <button type="submit" class="btn" :disabled="busy || !token.trim()">{{ busy ? 'Checking…' : 'Connect' }}</button>
        </form>
        <p class="muted small">It's checked with TickTick, then kept only on this Mac (data/ticktick.json, readable by you only).
          It's never shown again, here or on your phone.</p>
      </template>
      <template v-else>
        <dl class="facts">
          <dt>Token</dt><dd class="mono">••••{{ tt.token_ends }}</dd>
          <dt>Due dates in</dt><dd>{{ tt.timezone ?? '—' }}</dd>
          <dt>Tasks go to</dt>
          <dd>
            <select v-if="tt.lists" :value="tt.list_id ?? ''" :disabled="busy" aria-label="TickTick list"
                    @change="chooseList(($event.target as HTMLSelectElement).value)">
              <option value="">Inbox</option>
              <option v-for="l in tt.lists" :key="l.id" :value="l.id">{{ l.name }}</option>
            </select>
            <span v-else>{{ tt.list }}</span>
          </dd>
        </dl>
        <p v-if="tt.error" class="error small">{{ tt.error }}</p>
        <div><button type="button" class="btn btn-small" :disabled="busy" @click="disconnect">Disconnect</button></div>
        <p class="muted small">Disconnecting deletes the token from this Mac. Revoke it in TickTick too if you're done with it.
          Tasks already sent stay in TickTick.</p>
      </template>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-else-if="saved" class="ok small" aria-live="polite">{{ saved }}</p>
    </section>

    <CalendarSettings />
    <ExportImport />
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 760px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
ol { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
.row-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.connect { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); flex: 1; min-width: 220px; }
.field input, select { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
.facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 16px; margin: 0; align-items: center; font-size: 14px; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.choices { display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
.choice { display: flex; gap: 10px; align-items: center; min-height: 32px; cursor: pointer; }
.cap { text-transform: capitalize; font-weight: 600; }
</style>
