<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { kept } from '../lab/kept'
import { mss, when } from '../lab/format'
import { href } from '../lab/router'
import { canDelete } from '../lab/where'
import type { Person } from '../lab/types'

const people = ref<Person[] | null>(null)
const error = ref('')
async function load() {
  try { people.value = await lab.people() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
onMounted(load)
const filter = ref('')
const shown = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return q ? people.value?.filter((p) => p.name.toLowerCase().includes(q)) ?? [] : people.value ?? []
})

// B36: prepare for someone never recorded (a first meeting): added by name, then their Prepare page.
const newPerson = ref('')
const addingPerson = ref(false)
const newPersonError = ref('')
async function someoneNew() {
  addingPerson.value = true
  newPersonError.value = ''
  try { location.hash = href.prepare((await lab.preparePerson(newPerson.value.trim())).person_id) }
  catch (e) { newPersonError.value = e instanceof Error ? e.message : String(e) } finally { addingPerson.value = false }
}

// Rename everywhere. If the new name is someone else already, offer to merge: they're the same person.
const renaming = ref<string | null>(null)
const newName = ref('')
const renameError = ref('')
const mergeInto = ref('')
async function startRename(p: Person) {
  renaming.value = p.name
  newName.value = p.name
  renameError.value = mergeInto.value = ''
}
async function saveRename(merge = false) {
  if (!renaming.value || !newName.value.trim()) return
  try {
    await lab.rename(renaming.value, newName.value.trim(), merge)
    renaming.value = null
    await load()
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.endsWith('already exists')) mergeInto.value = msg.replace(/ already exists$/, '')
    else renameError.value = msg
  }
}

// Agreement to be recognised by voice (product plan 4.6): in a new lab, someone else's voiceprint is kept
// only once it's recorded. Withdrawing it deletes their voiceprints; their name stays on conversations.
const consentFor = ref<string | null>(null)
const how = ref('asked in person')
const consentNote = ref('')
async function recordConsent(p: Person) {
  try {
    await lab.consent(p.id, how.value, consentNote.value)
    consentFor.value = null
    consentNote.value = ''
    await load()
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
async function withdraw(p: Person) {
  if (!confirm(`Withdraw ${p.name}'s agreement? Their ${p.voiceprints} voiceprint(s) are deleted now, and from this Mac's daily backups within 14 days; their name stays on conversations.`)) return
  try { await lab.withdrawConsent(p.id); await load() } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}

// "Forget" asks for the name to be typed: it deletes a biometric, and can't be undone.
const forgetting = ref<Person | null>(null)
const typed = ref('')
const dialogError = ref('')
const field = ref<HTMLInputElement | null>(null)
async function ask(p: Person) {
  forgetting.value = p
  typed.value = ''
  dialogError.value = ''
  await nextTick()
  field.value?.focus()
}
async function confirmForget() {
  if (!forgetting.value) return
  try {
    await lab.forget(forgetting.value.name, typed.value)
    kept.people.clear()  // what the pages showed of them, held while the app is open
    kept.conversations.clear()
    kept.list = kept.progress = null
    forgetting.value = null
    await load()
  } catch (e) {
    dialogError.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <main class="page">
    <div>
      <h1>People</h1>
      <p class="muted">Everyone you've named. Their voiceprints are kept only on this Mac, and only so they're recognised next time<template
        v-if="people?.some((p) => p.needs_consent)">, and only for people whose agreement you've recorded</template>.
        Open someone's name before you talk to them: your conversations, open steps and what the reviews read in them.</p>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!people" class="muted loading" role="status">Loading…</p>
    <template v-else>
    <input v-if="people.length > 5" v-model="filter" type="search" class="find" placeholder="Find someone" aria-label="Find someone"
           autocomplete="off" @keydown.esc="filter = ''">
    <details class="newp">
      <summary class="small">Meeting someone Octo hasn't heard yet?</summary>
      <form class="newp-form" @submit.prevent="someoneNew">
        <label class="sr" for="np-name">Their name</label>
        <input id="np-name" v-model="newPerson" type="text" class="find" maxlength="60" placeholder="Their name, as people say it">
        <button type="submit" class="btn btn-small" :disabled="newPerson.trim().length < 2 || addingPerson">Prepare</button>
      </form>
      <p class="muted small">They're added here by name, with no voiceprint. The plan reads what others said about them.</p>
      <p v-if="newPersonError" class="error small" role="alert">{{ newPersonError }}</p>
    </details>
    <section class="card" aria-label="People">
      <div class="row th"><span>Name</span><span>Voiceprints</span><span>Last heard</span><span>Conversations</span><span /></div>
      <p v-if="!shown.length" class="row muted">Nobody called “{{ filter.trim() }}”.</p>
      <div v-for="p in shown" :key="p.name" class="row">
        <span v-if="renaming === p.name" class="name edit">
          <label class="sr" :for="`rn-${p.name}`">New name for {{ p.name }}</label>
          <input :id="`rn-${p.name}`" v-model="newName" maxlength="40" @keydown.enter.prevent="saveRename()" @keydown.esc="renaming = null">
          <button type="button" class="btn btn-small btn-primary" @click="saveRename()">Save</button>
          <button type="button" class="btn btn-small" @click="renaming = null">Cancel</button>
          <span v-if="mergeInto" class="merge small">{{ mergeInto }} is already someone here. Same person?
            <button type="button" class="btn btn-small" @click="saveRename(true)">Yes, combine them</button></span>
          <span v-if="renameError" class="error small" role="alert">{{ renameError }}</span>
        </span>
        <span v-else class="name"><i :class="p.self ? 'me' : 's1'" /><b v-if="p.self">{{ p.name }}</b><a v-else class="who"
          :href="href.person(p.id)" :title="`Before you talk to ${p.name}`">{{ p.name }}</a><span v-if="p.self" class="muted"> (you)</span></span>
        <span v-if="p.needs_consent && !p.consent" class="consent">
          <span class="muted small">Not recognised by voice: their agreement isn't recorded.</span>
          <template v-if="consentFor === p.id">
            <select v-model="how" aria-label="How they agreed" class="small">
              <option value="asked in person">Asked in person</option><option value="in writing">In writing</option><option value="other">Other</option>
            </select>
            <input v-model="consentNote" class="small" placeholder="a note (optional)" aria-label="Note" maxlength="200">
            <button type="button" class="btn btn-small btn-primary" @click="recordConsent(p)">Save</button>
            <button type="button" class="btn btn-small" @click="consentFor = null">Cancel</button>
          </template>
          <button v-else type="button" class="btn btn-small" @click="consentFor = p.id">They agreed…</button>
        </span>
        <span v-else>{{ p.voiceprints }} · {{ mss(p.seconds) }} of speech<span v-if="p.enrolled" class="muted small"> · {{ p.enrolled }} from enrolment</span>
          <span v-if="p.needs_consent && p.consent" class="muted small"> · agreed {{ when(p.consent.at) }}, {{ p.consent.how }}
            <button v-if="canDelete" type="button" class="link small" @click="withdraw(p)">Withdraw</button></span></span>
        <span>{{ p.last_heard ? when(p.last_heard) : '—' }}</span>
        <span>{{ p.conversations }}</span>
        <span class="end">
          <template v-if="canDelete">
            <button type="button" class="btn btn-small" @click="startRename(p)">Rename</button>
            <button v-if="!p.self" type="button" class="btn btn-small" @click="ask(p)">Forget…</button>
          </template>
        </span>
      </div>
    </section>
    </template>
    <p class="muted small">Voices you haven't named are compared and then dropped; nothing is kept for them.</p>
    <p v-if="!canDelete" class="muted small">Renaming, forgetting and withdrawing are at home or on your Mac.</p>

    <div v-if="forgetting" class="scrim" @click.self="forgetting = null" @keydown.esc="forgetting = null">
      <div role="dialog" aria-modal="true" aria-labelledby="fg" class="dialog card">
        <h2 id="fg">Forget {{ forgetting.name }}?</h2>
        <ul>
          <li>Their {{ forgetting.voiceprints }} voiceprint{{ forgetting.voiceprints === 1 ? ' is' : 's are' }} deleted from this Mac.</li>
          <li>Their name comes off {{ forgetting.conversations }} conversation{{ forgetting.conversations === 1 ? '' : 's' }}; they show as a lettered speaker there.</li>
          <li>Those conversations' reviews are redone without their name, answered ones too; your answers stay with the old version and still count.</li>
          <li>Coach notes that read a conversation they were in are deleted.</li>
          <li>Conversations and transcripts stay.</li>
          <li>Still holding the name for a while: the daily database backups on this Mac (the last 14 days), and earlier versions of reviews. Tasks already sent to TickTick keep it until you edit them there.</li>
        </ul>
        <label class="field">Type <b>{{ forgetting.name }}</b> to confirm
          <input ref="field" v-model="typed" autocomplete="off" @keydown.enter.prevent="confirmForget">
        </label>
        <p v-if="dialogError" class="error small" role="alert">{{ dialogError }}</p>
        <div class="buttons">
          <button type="button" class="btn" @click="forgetting = null">Cancel</button>
          <button type="button" class="btn btn-danger" :disabled="typed.trim().toLowerCase() !== forgetting.name.toLowerCase()"
                  @click="confirmForget">Forget {{ forgetting.name }}</button>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.edit { flex-wrap: wrap; gap: 6px; }
.edit input { font: inherit; font-size: 14px; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 5px 8px; height: 34px; width: 160px; }
.merge { flex-basis: 100%; display: flex; align-items: center; gap: 8px; color: var(--ink-2); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.page { width: 100%; max-width: 1040px; margin: 0 auto; padding: 28px 28px 48px; display: flex; flex-direction: column; gap: 18px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
p { margin: 4px 0 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.error { color: var(--fail); }
.row { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1.6fr) 170px 110px 110px; gap: 16px; padding: 14px 22px; border-top: 1px solid var(--line); align-items: center; }
.th { border-top: 0; font-size: 12px; font-weight: 500; color: var(--muted); padding: 10px 22px; }
.name { display: flex; align-items: center; gap: 10px; }
.name i { width: 12px; height: 12px; border-radius: 3px; }
.who { font-weight: 600; color: var(--ink); text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 3px; }
.who:hover { color: var(--accent); text-decoration-color: currentColor; }
i.me { background: var(--me); } i.s1 { background: var(--s1); }
.end { justify-self: end; display: flex; gap: 6px; }
.scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 10; }
.dialog { width: 480px; max-width: 100%; padding: 24px; display: flex; flex-direction: column; gap: 12px; }
.dialog h2 { margin: 0; font-size: 20px; }
.dialog ul { margin: 0; padding-left: 20px; color: var(--ink-2); }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
.field input { font: inherit; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; height: 44px; }
.buttons { display: flex; justify-content: flex-end; gap: 10px; }
@media (max-width: 760px) { .row { grid-template-columns: 1fr 1fr; } .th { display: none; } }
.newp summary { cursor: pointer; padding: 10px 0; color: var(--accent); }
.newp-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 4px 0 6px; }
.newp p { margin: 0; }
.find { font: inherit; font-size: 15px; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; height: 40px; width: 280px; max-width: 100%; }
.consent { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.consent select, .consent input { font: inherit; font-size: 13px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 4px 8px; }
.link { border: 0; background: none; color: var(--accent); padding: 0; cursor: pointer; text-decoration: underline; font: inherit; }
</style>
