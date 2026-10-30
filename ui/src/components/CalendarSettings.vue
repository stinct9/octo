<script setup lang="ts">
// The calendar (B23, octo/events.py): this Mac's accounts through the Octo app, and calendar links. A link goes in,
// never out: the page sees its host and last four characters.
import { onMounted, ref } from 'vue'

interface Link { id: string; host: string; ends: string; added_at: number; fetched_at: number | null; events: number | null; error: string | null }
interface Cal { links: Link[]; mac: boolean; mac_access: 'full' | 'denied' | 'ask' | null; mac_synced_at: number | null; mac_events: number | null }

const cal = ref<Cal | null>(null)
const url = ref('')
const error = ref('')
const busy = ref(false)
const octo = (window as unknown as { webkit?: { messageHandlers?: { octo?: { postMessage: (m: string) => void } } } })
  .webkit?.messageHandlers?.octo

async function run(method: string, path: string, body?: unknown) {
  busy.value = true
  error.value = ''
  try {
    const res = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail ?? res.statusText)
    cal.value = data as Cal
    return true
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    return false
  } finally {
    busy.value = false
  }
}
const load = () => run('GET', '/api/octo/settings/calendar')
async function add() {
  if (await run('POST', '/api/octo/settings/calendar/links', { url: url.value })) url.value = ''  // don't keep it in the page
}
async function useMac(on: boolean) {
  if (await run('POST', '/api/octo/settings/calendar/mac', { on }) && on) octo?.postMessage('calendar')  // macOS asks once
}
const when = (t: number) => new Date(t * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
onMounted(load)
</script>

<template>
  <section class="card pad" aria-labelledby="cal-h">
    <h2 id="cal-h">Calendar</h2>
    <p class="muted">Ten minutes before an event with someone Octo knows, a banner with the event's name and time opens their
      page. When a recording overlaps an event, its invitees are offered as names for the speakers; you choose. Events
      are read two days back and two days ahead, and nothing is written to your calendar.</p>
    <p v-if="!cal" class="muted loading" role="status">Loading…</p>
    <template v-else>
      <h3>This Mac's calendars</h3>
      <p class="muted small">Whatever the Calendar app shows, including Google and Microsoft 365 accounts added in System Settings →
        Internet Accounts. Read by the Octo app, which asks macOS once.</p>
      <div v-if="!cal.mac"><button type="button" class="btn btn-small" :disabled="busy" @click="useMac(true)">Use this Mac's calendars</button></div>
      <template v-else>
        <p class="small">
          <template v-if="cal.mac_access === 'full'">Reading them<template v-if="cal.mac_synced_at">: {{ cal.mac_events }} event{{ cal.mac_events === 1 ? '' : 's' }}, {{ when(cal.mac_synced_at) }}</template>.</template>
          <span v-else-if="cal.mac_access === 'denied'" class="error">macOS doesn't let Octo read them: allow Octo in System Settings → Privacy &amp; Security → Calendars.</span>
          <template v-else-if="octo">Waiting for macOS to ask you.</template>
          <template v-else>The Octo app reads them within a minute, and asks macOS first. Open it from Applications if it isn't in the menu bar.</template>
        </p>
        <div><button type="button" class="btn btn-small" :disabled="busy" @click="useMac(false)">Stop</button></div>
      </template>

      <h3>Calendar links</h3>
      <p class="muted small">Google Calendar: the calendar's settings → <b>Secret address in iCal format</b>. Outlook: Settings →
        Calendar → Shared calendars → <b>Publish a calendar</b>, the ICS link. Anyone with the link can read the calendar, so it's
        kept only on this Mac (data/calendar.json, readable by you only) and never shown again. Fetched every 5 minutes.</p>
      <ul v-if="cal.links.length" class="links">
        <li v-for="l in cal.links" :key="l.id">
          <span class="mono">{{ l.host }}/…{{ l.ends }}</span>
          <span v-if="l.error" class="error small">{{ l.error }}</span>
          <span v-else-if="l.fetched_at" class="muted small">{{ l.events }} event{{ l.events === 1 ? '' : 's' }}, {{ when(l.fetched_at) }}</span>
          <button type="button" class="btn btn-small" :disabled="busy" @click="run('POST', `/api/octo/settings/calendar/links/${l.id}/remove`, {})">Remove</button>
        </li>
      </ul>
      <form class="connect" @submit.prevent="add">
        <label class="field">Calendar link
          <input v-model="url" type="password" autocomplete="off" spellcheck="false" placeholder="https://… or webcal://…" :disabled="busy">
        </label>
        <button type="submit" class="btn" :disabled="busy || !url.trim()">{{ busy ? 'Checking…' : 'Add' }}</button>
      </form>
      <p class="muted small">Removing a link deletes it and its events here. Reset the link in your calendar too if you're done with it.</p>
    </template>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h3 { margin: 6px 0 0; font-size: 15px; font-weight: 600; }
p { margin: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); }
.links { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.links li { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; font-size: 14px; }
.connect { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); flex: 1; min-width: 220px; }
.field input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
</style>
