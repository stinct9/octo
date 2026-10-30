<script setup lang="ts">
// Your week (B17): on home from Monday until dismissed or replaced by the next Monday's, about the seven days
// before; on Progress, an earlier week's counts and focus (`week`). Read from what's stored, no model call.
import { onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { day, dueDay } from '../lab/format'
import { href } from '../lab/router'
import type { WeekCard } from '../lab/types'
import { countsLine, focusLine, weekRange } from '../lab/week'

const props = defineProps<{ week?: string }>()
const c = ref<WeekCard | null>(null)
const error = ref('')
const asked = ref(false)
onMounted(async () => {
  try { c.value = await lab.week(props.week) } catch { /* the page works without it */ }
})
async function dismiss() {
  if (!c.value) return
  try { await lab.dismissWeek(c.value.week); c.value = null } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
async function ask() {
  try { await lab.askCoach(); asked.value = true; error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
</script>

<template>
  <section v-if="c" class="week" :class="{ card: !week }" :aria-labelledby="`week-${c.week}`">
    <div v-if="!week" class="head">
      <h2 :id="`week-${c.week}`" class="eyebrow-w">Your week · {{ weekRange(c.week) }}</h2>
      <button type="button" class="link small" @click="dismiss">Dismiss</button>
    </div>
    <p class="text">{{ countsLine(c.counts) }}</p>
    <p v-if="c.focus" class="small">Your focus: {{ c.focus.text }}<template v-if="focusLine(c.focus)"><br>
      <span class="muted">{{ focusLine(c.focus) }}</span></template></p>
    <template v-if="!week && c.commitments && (c.commitments.mine || c.commitments.theirs)">
      <p class="small">Open commitments: {{ c.commitments.mine }} yours, {{ c.commitments.theirs }} to you</p>
      <ul v-if="c.commitments.due.length" class="list small">
        <li v-for="m in c.commitments.due" :key="m.id">{{ m.mine ? 'You' : m.by }}: {{ m.what }} · by {{ dueDay(m.due!) }}</li>
      </ul>
    </template>
    <ul v-if="!week && c.quiet?.length" class="list small">
      <li v-for="q in c.quiet" :key="q.person_id"><a :href="href.person(q.person_id)">{{ q.text }}</a></li>
    </ul>
    <p v-if="!week && c.coach?.theme" class="small">The coach, {{ day(c.coach.at) }}: <a :href="href.coach">{{ c.coach.theme }}</a>
      · <button v-if="!asked" type="button" class="link small" @click="ask">Ask again</button>
      <span v-else class="muted" role="status">Asked: the coach's card says when it's read them</span></p>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.week { display: flex; flex-direction: column; gap: 6px; }
.card.week { padding: 14px 18px; }
.head { display: flex; align-items: baseline; gap: 12px; }
.eyebrow-w { margin: 0; flex: 1; font-size: 13px; color: var(--accent); font-weight: 500; }
.text { margin: 0; font-size: 16px; font-weight: 600; color: var(--ink); }
p { margin: 0; }
.list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 2px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) { .head .link { min-height: 44px; } }
</style>
