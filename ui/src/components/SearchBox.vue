<script lang="ts">
import { reactive } from 'vue'
import type { SearchWho } from '../lab/types'

// Kept while the app is open, so Back from a result finds the search as it was.
const kept = reactive({ q: '', person: '', who: '' as SearchWho })
</script>

<script setup lang="ts">
// Search across conversations (B12): what was said and the reviews, read on the Mac at search time
// (octo/search.py). A result opens its conversation at that line and plays it.
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { lab } from '../lab/api'
import { searchCount, when } from '../lab/format'
import { href } from '../lab/router'
import type { Person, SearchResults } from '../lab/types'

defineProps<{ blur?: boolean }>()

const people = ref<Person[]>([])
const found = ref<SearchResults | null>(null)
const error = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let asked = 0  // only the latest answer is shown
async function run() {
  clearTimeout(timer)
  const q = kept.q.trim()
  const mine = ++asked
  if (q.length < 2) { found.value = null; error.value = ''; return }
  try {
    const r = await lab.search(q, kept.person, kept.who)
    if (mine === asked) { found.value = r; error.value = '' }
  } catch (e) { if (mine === asked) error.value = e instanceof Error ? e.message : String(e) }
}
watch(() => kept.q, () => { clearTimeout(timer); timer = setTimeout(run, 300) })
watch(() => [kept.person, kept.who], run)
onMounted(() => {
  void lab.people().then((ps) => { people.value = ps.filter((p) => !p.self) }).catch(() => {})
  if (kept.q) void run()
})
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <section class="search" aria-label="Search">
    <form class="bar" role="search" @submit.prevent="run">
      <input v-model="kept.q" type="search" class="q" maxlength="200" placeholder="Search what was said, and the reviews"
             aria-label="Search conversations">
      <select v-model="kept.person" aria-label="Said by">
        <option value="">Anyone</option>
        <option v-for="p in people" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
      <div class="seg" role="group" aria-label="You or them">
        <button type="button" :aria-pressed="kept.who === ''" @click="kept.who = ''">Everyone</button>
        <button type="button" :aria-pressed="kept.who === 'you'" @click="kept.who = 'you'">You</button>
        <button type="button" :aria-pressed="kept.who === 'them'" @click="kept.who = 'them'">Them</button>
      </div>
    </form>
    <a :href="href.ask" class="ask small">Ask a question in your own words →</a>
    <p v-if="error" class="error" role="alert">Couldn't search: {{ error }}</p>
    <div v-else-if="found" class="card results" :class="{ blur }" aria-live="polite">
      <p class="count muted small">{{ searchCount(found) }}</p>
      <a v-for="(r, i) in found.results" :key="i" class="hit" :href="href.line(r.session_id, r.line)">
        <span class="meta"><span class="who" :class="{ me: r.self, part: r.kind === 'review' }">{{ r.who }}</span>
          <span class="muted small">{{ when(r.at) }}<template v-if="r.with.length"> · with {{ r.with.join(', ') }}</template></span></span>
        <span class="text">{{ r.before }}<mark>{{ r.match }}</mark>{{ r.after }}</span>
      </a>
    </div>
  </section>
</template>

<style scoped>
.search { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.q { flex: 1 1 260px; min-width: 0; min-height: 44px; padding: 0 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--surface); color: var(--ink); font: inherit; font-size: 15px; }
select { min-height: 44px; padding: 0 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
  color: var(--ink); font: inherit; font-size: 14px; max-width: 180px; }
.seg { display: flex; padding: 3px; background: var(--chip); border-radius: 10px; }
.seg button { border: 0; background: transparent; color: var(--muted); padding: 8px 12px; min-height: 38px; border-radius: 8px; font-size: 14px; cursor: pointer; }
.seg button[aria-pressed='true'] { background: var(--surface); color: var(--ink); font-weight: 500; }
.ask { align-self: flex-start; text-decoration: none; padding: 4px 0; min-height: 32px; display: inline-flex; align-items: center; }
@media (pointer: coarse) { .ask { min-height: 44px; } }
.results { overflow: hidden; }
.count { margin: 0; padding: 12px 22px 4px; }
.hit { display: flex; flex-direction: column; gap: 4px; padding: 12px 22px; border-bottom: 1px solid var(--line); color: inherit; text-decoration: none; min-height: 44px; }
.hit:last-child { border-bottom: 0; }
.hit:hover { background: var(--quote); }
.meta { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.who { font-weight: 500; }
.who.me { color: var(--me); }
.who.part { color: var(--muted); font-style: italic; }
.text { font-size: 14px; overflow-wrap: anywhere; }
mark { background: var(--accent-soft); color: inherit; border-radius: 3px; padding: 0 2px; }
.blur .text { filter: blur(5px); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); margin: 0; }
@media (max-width: 760px) {
  .count, .hit { padding-left: 16px; padding-right: 16px; }
  select { flex: 1 1 auto; max-width: none; }
}
</style>
