<script setup lang="ts">
// "Octo hasn't heard from…" on a person's page (B17): once it's been twice their usual gap, the line and a mute;
// once muted, a way back. Loads its own data, so the page only places it.
import { onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import type { Quiet } from '../lab/types'

const props = defineProps<{ person: string }>()
const q = ref<Quiet | null>(null)
const error = ref('')
onMounted(async () => { try { q.value = await lab.quiet(props.person) } catch { /* the rest of the page still shows */ } })
async function mute(on: boolean) {
  try { q.value = await lab.muteQuiet(props.person, on); error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
</script>

<template>
  <p v-if="q?.muted" class="quiet muted small">You asked Octo not to say when it hasn't heard from {{ q.name }}.
    <button type="button" class="link small" @click="mute(false)">Say it again</button></p>
  <p v-else-if="q?.text" class="quiet small">{{ q.text }}.
    <button type="button" class="link small" @click="mute(true)">Don't say this for {{ q.name }}</button></p>
  <p v-if="error" class="error small" role="alert">{{ error }}</p>
</template>

<style scoped>
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.quiet, .error { margin: 0; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
</style>
