<script setup lang="ts">
// The recorder's name and version, and the two ways to stop using it.
import { onMounted, ref } from 'vue'
import Sheet from '../components/Sheet.vue'
import type { Connection } from '../connection'
import { appVersion, versionText } from '../version'

const props = defineProps<{ open: boolean; c: Connection }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const s = props.c.s
const forgetting = ref(false)
const app = ref('')
onMounted(async () => { app.value = versionText(await appVersion()) })

async function forget() {
  forgetting.value = false
  emit('close')
  await props.c.forget()
}
async function another() {
  emit('close')
  await props.c.findAnother()
}
</script>

<template>
  <Sheet :open="open && !forgetting" title="About this recorder" @close="emit('close')">
    <ul class="rows">
      <li class="row"><span>Name</span><span class="value">{{ s.recorder?.name }}</span></li>
      <li class="row"><span>Recorder software</span><span class="value">{{ s.status?.fw ?? s.recorder?.fw ?? '—' }}</span></li>
      <li class="row"><span>This app</span><span class="value">{{ app || '—' }}</span></li>
    </ul>
    <p class="hint">Software updates come from your Mac, over USB or the same Wi-Fi.</p>
    <button type="button" class="btn" @click="another">Find a different recorder</button>
    <button type="button" class="btn quiet danger-text" @click="forgetting = true">Forget this recorder</button>
  </Sheet>
  <Sheet :open="open && forgetting" :title="`Forget ${s.recorder?.name}?`" @close="forgetting = false">
    <p>Your phone stops connecting to it. To use it again, find it and type a new code.</p>
    <p class="sub">To remove it completely, also forget "{{ s.recorder?.name }}" in Settings, then Bluetooth. An app can't do that part.</p>
    <button type="button" class="btn danger" @click="forget">Forget</button>
    <button type="button" class="btn quiet" @click="forgetting = false">Keep</button>
  </Sheet>
</template>

<style scoped>
.danger-text { color: var(--fail); }
</style>
