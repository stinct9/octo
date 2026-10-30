<script setup lang="ts">
// The recorder's Wi-Fi networks: it uses Wi-Fi to send recordings to the Mac, keeps up to five and tries the
// top one first. Checked the way the recorder will check them, and told on Save, not while typing.
import { computed, ref } from 'vue'
import Sheet from '../components/Sheet.vue'
import type { Connection } from '../connection'
import { networkProblem } from '../recorder'
import { haptic } from '../haptics'

const props = defineProps<{ open: boolean; c: Connection }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const s = props.c.s

const adding = ref(false)
const ssid = ref('')
const password = ref('')
const show = ref(false)
const problem = ref('')
const note = ref('')
const busy = ref(false)
const removing = ref<string | null>(null)

const current = computed(() => (s.status?.wifi ? s.status.ssid : ''))
const replaces = computed(() => (s.nets.length >= 5 ? s.nets[s.nets.length - 1] : ''))

async function save() {
  problem.value = networkProblem(ssid.value.trim(), password.value) ?? ''
  if (problem.value) return
  busy.value = true
  try {
    await props.c.addNetwork(ssid.value.trim(), password.value)
    haptic('success')
    note.value = 'Saved. Your recorder tries it next time it looks for Wi-Fi.'
    ssid.value = ''
    password.value = ''
    adding.value = false
  } catch {
    problem.value = 'Couldn\'t reach the recorder. Nothing was saved.'
  } finally { busy.value = false }
}

async function remove(name: string) {
  removing.value = null
  busy.value = true
  try {
    await props.c.removeNetwork(name)
    note.value = `Removed ${name}.`
  } catch {
    note.value = 'Couldn\'t reach the recorder. Nothing was removed.'
  } finally { busy.value = false }
}

function close() { adding.value = false; problem.value = ''; note.value = ''; emit('close') }
</script>

<template>
  <Sheet :open="open && !adding && !removing" title="Wi-Fi networks" @close="close">
    <p class="sub">Your recorder uses Wi-Fi to send recordings to your Mac. It keeps up to five and tries the top one first.</p>
    <ul v-if="s.nets.length" class="rows">
      <li v-for="(n, i) in s.nets" :key="n" class="row">
        <span>{{ n }}<span v-if="n === current" class="hint"> · Connected</span><span v-else-if="i === 0" class="hint"> · Tried first</span></span>
        <button type="button" class="link" :disabled="busy" :aria-label="`Remove ${n}`" @click="removing = n">Remove</button>
      </li>
    </ul>
    <p v-else class="sub">No networks yet. Add your home Wi-Fi so it can send recordings to your Mac.</p>
    <p v-if="note" class="ok small" aria-live="polite">{{ note }}</p>
    <button type="button" class="btn primary" :disabled="busy" @click="adding = true; note = ''">Add a network</button>
  </Sheet>

  <Sheet :open="open && adding" title="Add a network" @close="adding = false; problem = ''">
    <form class="add" @submit.prevent="save">
      <label class="field">Network name<input v-model="ssid" type="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" :disabled="busy"></label>
      <label class="field">Password
        <span class="with-toggle">
          <input v-model="password" :type="show ? 'text' : 'password'" autocomplete="off" autocapitalize="off" placeholder="Leave empty if there's none" :disabled="busy">
          <button type="button" :aria-pressed="show" @click="show = !show">{{ show ? 'Hide' : 'Show' }}</button>
        </span>
      </label>
      <p class="hint">2.4 GHz only. If your router shows two networks, such as "Home" and "Home-5G", pick the one without 5G.</p>
      <p v-if="replaces" class="warn small">Adding one replaces the oldest, {{ replaces }}.</p>
      <p v-if="problem" class="error small" role="alert">{{ problem }}</p>
      <button type="submit" class="btn primary" :disabled="busy">{{ busy ? 'Saving…' : 'Save to recorder' }}</button>
    </form>
  </Sheet>

  <Sheet :open="open && !!removing" :title="`Remove ${removing}?`" @close="removing = null">
    <p v-if="removing === current">Your recorder is using it now. Without it, recordings wait on the card until it finds another network.</p>
    <p v-else>Your recorder stops trying this network. You can add it again later.</p>
    <button type="button" class="btn danger" :disabled="busy" @click="remove(removing!)">Remove</button>
    <button type="button" class="btn quiet" @click="removing = null">Keep</button>
  </Sheet>
</template>

<style scoped>
.add { display: flex; flex-direction: column; gap: 14px; }
.row .link { min-height: 44px; }
</style>
