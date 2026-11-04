<script setup lang="ts">
// Home's agenda (B34): today's and tomorrow's calendar events (B23), who's in each with their page, and once one has
// started, the conversation recorded during it. Hidden without a calendar or events.
import { computed, onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { href } from '../lab/router'
import type { AgendaEvent } from '../lab/types'
import FoldToggle from './FoldToggle.vue'

const events = ref<AgendaEvent[]>([])
const open = ref(true)
onMounted(async () => {
  try { events.value = await lab.agenda() } catch { /* the page works without it */ }
})
const now = Date.now() / 1000
const midnight = new Date(new Date().setHours(24, 0, 0, 0)).getTime() / 1000  // tonight's: tomorrow starts here
const days = computed(() => [
  { label: 'Today', events: events.value.filter((e) => e.start < midnight) },
  { label: 'Tomorrow', events: events.value.filter((e) => e.start >= midnight) },
].filter((d) => d.events.length))
// B36: a plan for each person in it; someone Octo doesn't know is added to People by name first.
const adding = ref<string | null>(null)
async function someoneNew(name: string, key?: string) {
  adding.value = key ?? name
  try { location.hash = href.prepare((await lab.preparePerson(name, key)).person_id) } catch { /* stays on home */ } finally { adding.value = null }
}
const hm = (t: number) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <section v-if="days.length" class="card agenda" aria-labelledby="agenda-h">
    <div class="agenda-head"><h2 id="agenda-h" class="eyebrow-a">Your calendar</h2>
      <span v-if="!open" class="small muted">{{ days.map((d) => `${d.events.length} ${d.label.toLowerCase()}`).join(' · ') }}</span>
      <a class="small" :href="href.calendar">The month →</a><FoldToggle v-model="open" name="agenda" label="your calendar" /></div>
    <div v-for="d in open ? days : []" :key="d.label" class="day">
      <h3 class="small muted">{{ d.label }}</h3>
      <ul>
        <li v-for="e in d.events" :key="e.id" :class="{ past: e.end <= now }">
          <span class="time small muted">{{ hm(e.start) }}–{{ hm(e.end) }}</span>
          <span class="what">
            <span class="title">{{ e.title || '(No title)' }}</span>
            <span v-if="e.people.length" class="small"> · <template v-for="(p, i) in e.people" :key="p.name">{{ i ? ', ' : '' }}<a
              v-if="p.person_id" :href="href.person(p.person_id)" :title="`Before you talk to ${p.name}`">{{ p.name }}</a><span
              v-else class="muted">{{ p.name }}</span></template></span>
          </span>
          <a v-if="e.conversation" class="small rec" :href="href.conversation(e.conversation)">The conversation</a>
          <span v-else-if="e.end <= now" class="small muted rec">Nothing recorded</span>
          <span v-else-if="e.start <= now" class="small rec now">Now</span>
          <span v-else-if="e.people.length" class="small rec">Prepare<template v-for="(p, i) in e.people" :key="p.key ?? p.name">{{ i ? ' ·' : ':' }}
            <a v-if="p.person_id" class="tap" :href="href.prepare(p.person_id)" :aria-label="`${p.prepared ? 'Your plan' : 'Prepare'} for ${e.title || 'the meeting'} with ${p.name}`">{{
              p.prepared ? `${p.name}'s plan` : p.name }}</a><button v-else type="button" class="tap link" :disabled="adding === p.key"
              :aria-label="`Prepare for ${e.title || 'the meeting'} with ${p.name}, who isn't in People yet`" @click="someoneNew(p.name, p.key)">{{ p.name }}</button></template></span>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.agenda { padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; }
.eyebrow-a { margin: 0; font-size: 13px; color: var(--accent); font-weight: 500; }
h3 { margin: 0; font-weight: 500; }
ul { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
li { display: flex; align-items: baseline; gap: 12px; }
.time { flex: none; width: 92px; font-variant-numeric: tabular-nums; }
.what { flex: 1; min-width: 0; }
.title { font-weight: 600; color: var(--ink); }
.past .title { font-weight: 500; color: var(--ink-2); }
.rec { flex: none; }
.now { color: var(--accent); font-weight: 500; }
.tap { display: inline-block; padding: 12px 0; margin: -12px 0; }
.link { background: none; border: 0; color: var(--accent); font: inherit; cursor: pointer; text-decoration: underline; }
a { color: var(--accent); }
.small { font-size: 13px; }
.muted { color: var(--muted); }
@media (max-width: 640px) { li { flex-wrap: wrap; gap: 2px 12px; } .what { flex-basis: 100%; order: 1; } }
.agenda-head { display: flex; align-items: baseline; gap: 12px; }
.eyebrow-a { flex: 1; }
</style>
