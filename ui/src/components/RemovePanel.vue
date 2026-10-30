<script setup lang="ts">
import { ref } from 'vue'
import { lab } from '../lab/api'
import { mss, when } from '../lab/format'
import type { FixResult, Part, Speaker } from '../lab/types'

const props = defineProps<{
  id: string
  speakers: Speaker[]
  leftOut: string[]
  duration: number
  now: number
  audioDeleted: boolean
  nameOf: (sp: Speaker) => string
  /** On a phone at home: only the whole conversation (a part, and leaving someone out, are on the Mac). */
  deleteOnly?: boolean
  /** B35: the parts of the one discussion it's in. From the first, Delete takes them all; from a later one, only it. */
  parts?: Part[] | null
}>()
const emit = defineEmits<{ fixed: [r: FixResult]; redo: []; gone: []; ungrouped: [] }>()
const first = () => !!props.parts?.length && props.parts[0].id === props.id
const span = (x: Part) => `${when(x.at)}, ${mss(x.duration_s)}`
function ungroup(x: Part) {  // "Not one discussion" here, for when only one part should go
  void run(() => lab.ungroup(props.id, x.first ?? '', x.id), () => emit('ungrouped'))
}

const asking = ref<'' | 'range' | 'delete'>('')
const from = ref('')
const to = ref('')
const error = ref('')
const busy = ref(false)

/** "45", "2:05", "1:02:05" (the form mss() fills in past an hour) → seconds. */
function parse(v: string): number | null {
  const m = v.trim().match(/^(?:(?:(\d+):)?(\d+):)?(\d+(?:\.\d+)?)$/)
  return m ? Number(m[1] ?? 0) * 3600 + Number(m[2] ?? 0) * 60 + Number(m[3]) : null
}
function openRange() {
  asking.value = 'range'
  from.value = mss(props.now)
  to.value = mss(Math.min(props.duration, props.now + 30))
  error.value = ''
}
async function run(action: () => Promise<unknown>, after: () => void) {
  busy.value = true
  error.value = ''
  try { await action(); asking.value = ''; after() } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
function deleteRange() {
  const a = parse(from.value)
  const b = parse(to.value)
  if (a === null || b === null || b <= a) { error.value = 'From and to as m:ss (or h:mm:ss), with "to" after "from".'; return }
  void run(() => lab.deleteRange(props.id, a, Math.min(b, props.duration)), () => emit('redo'))
}
async function toggle(box: HTMLInputElement, letter: string, on: boolean) {
  try { emit('fixed', await lab.leaveOut(props.id, letter, on)) } catch (e) {
    box.checked = !on  // not saved: the box shows what's true (M20)
    error.value = `Couldn't change that: ${e instanceof Error ? e.message : e}`
  }
}
</script>

<template>
  <section class="card pad" aria-labelledby="rm">
    <h2 id="rm">{{ deleteOnly ? 'Delete' : 'Remove' }}</h2>

    <fieldset v-if="!deleteOnly && speakers.some((s) => !s.self)">
      <legend class="small muted">Leave out of insights (their words never reach the review; in Hindi, the merge of the drafts still sees them)</legend>
      <label v-for="sp in speakers.filter((s) => !s.self)" :key="sp.letter" class="check">
        <input type="checkbox" :checked="leftOut.includes(sp.letter)" @change="toggle($event.target as HTMLInputElement, sp.letter, ($event.target as HTMLInputElement).checked)">
        {{ nameOf(sp) }}
      </label>
    </fieldset>

    <div class="actions">
      <button v-if="!audioDeleted && !deleteOnly" type="button" class="link" @click="openRange">Delete a part of this conversation…</button>
      <button type="button" class="link" @click="asking = 'delete'; error = ''">Delete this conversation…</button>
    </div>

    <div v-if="asking === 'range'" class="confirm" role="group" aria-label="Delete a part">
      <div class="times">
        <label>From <input v-model="from" inputmode="decimal" placeholder="m:ss"></label>
        <label>To <input v-model="to" inputmode="decimal" placeholder="m:ss"></label>
      </div>
      <p class="small">That stretch of the recording becomes silence and the conversation is worked out again from what's
        left. Earlier transcripts, reviews and your answers on them are deleted, and so is any coach note that read it;
        speakers need confirming again. It stops counting as reviewed until you answer the new review.</p>
      <div class="buttons">
        <button type="button" class="btn btn-small" @click="asking = ''">Cancel</button>
        <button type="button" class="btn btn-small btn-danger" :disabled="busy" @click="deleteRange">Delete this part</button>
      </div>
    </div>

    <div v-if="asking === 'delete'" class="confirm" role="group" aria-label="Delete conversation">
      <p v-if="parts?.length && first()" class="small strong">This discussion is {{ parts.length }} parts
        ({{ parts.map(span).join('; ') }}): all of them are deleted.<template v-if="!deleteOnly"> To delete only one, say it
        isn't one discussion first:<template v-for="x in parts.slice(1)" :key="x.id">{{ ' ' }}<button type="button" class="link"
        :disabled="busy" @click="ungroup(x)">Not one discussion at part {{ x.n }}</button></template>.</template></p>
      <p v-else-if="parts?.length" class="small strong">Only this part (part {{ parts.find((x) => x.id === id)?.n }} of
        {{ parts.length }}) is deleted. The rest of the discussion stays; a review of the whole that read this part goes with it, with its answers.</p>
      <p class="small">The {{ parts?.length && first() ? 'parts\' audio becomes' : "conversation's audio becomes" }} silence, and {{ parts?.length && first() ? 'their' : 'its' }} transcript,
        speakers, numbers, review, your answers, any voiceprints taken from it and any coach note that read it are
        deleted. This can't be undone.
        The daily database backups on this Mac keep the text for up to 14 days; tasks sent to TickTick stay there.</p>
      <div class="buttons">
        <button type="button" class="btn btn-small" @click="asking = ''">Cancel</button>
        <button type="button" class="btn btn-small btn-danger" :disabled="busy"
                @click="run(() => lab.deleteSession(id), () => emit('gone'))">Delete it</button>
      </div>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.pad { padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
h2 { margin: 0; font-size: 15px; font-weight: 600; color: var(--muted); }
fieldset { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
legend { margin-bottom: 4px; }
.check { display: flex; align-items: center; gap: 8px; min-height: 36px; font-size: 14px; }
.check input { width: 18px; height: 18px; accent-color: var(--accent); }
.actions { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.link { border: 0; background: none; color: var(--accent); cursor: pointer; font: inherit; font-size: 14px; padding: 6px 0; text-align: left; }
.confirm { border: 1px solid var(--line); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; background: var(--quote); }
.confirm p { margin: 0; }
.strong { font-weight: 600; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.times { display: flex; gap: 12px; }
.times label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.times input { font: inherit; font-size: 14px; width: 84px; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; height: 36px; }
.buttons { display: flex; justify-content: flex-end; gap: 8px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); margin: 0; }
</style>
