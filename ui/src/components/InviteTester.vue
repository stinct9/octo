<script setup lang="ts">
// Settings → Invite a tester (octo/invite.py): only in a lab whose owner kept a Gmail account with `octo invite setup`.
// The route answers 404 otherwise, and then nothing renders.
import { onMounted, ref } from 'vue'

interface Invites { ready: boolean; from: string; sent: { name: string; email: string; at: number }[] }

const st = ref<Invites | null>(null)
const name = ref('')
const email = ref('')
const busy = ref(false)
const error = ref('')
const done = ref('')

onMounted(async () => {
  const r = await fetch('/api/octo/settings/invite').catch(() => null)
  if (r?.ok) st.value = await r.json()
})
async function invite() {
  busy.value = true
  error.value = ''
  done.value = ''
  try {
    const r = await fetch('/api/octo/settings/invite', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.value, email: email.value }) })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(data.detail ?? r.statusText)
    st.value = data
    done.value = `Sent to ${email.value.trim()}.`
    name.value = email.value = ''
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  busy.value = false
}
const when = (at: number) => new Date(at * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
</script>

<template>
  <section v-if="st" class="card pad" aria-labelledby="invite">
    <h2 id="invite">Invite a tester</h2>
    <p class="muted">Sends one email from <b>{{ st.from }}</b> with the install line and the four steps to set up. Replies
      come back to that account.</p>
    <form class="connect" @submit.prevent="invite">
      <label class="field">Name<input v-model="name" type="text" autocomplete="off" :disabled="busy" placeholder="First name"></label>
      <label class="field">Email<input v-model="email" type="email" autocomplete="off" :disabled="busy"></label>
      <button type="submit" class="btn" :disabled="busy || !name.trim() || !email.includes('@')">{{ busy ? 'Sending…' : 'Invite' }}</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="done" class="ok small" aria-live="polite">{{ done }}</p>
    <template v-if="st.sent.length">
      <h3 class="small">Invited</h3>
      <ul class="sent small"><li v-for="s in st.sent" :key="s.at">{{ s.name }} · {{ s.email }} · {{ when(s.at) }}</li></ul>
    </template>
  </section>
</template>

<style scoped>
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h3 { margin: 0; font-weight: 600; }
p { margin: 0; }
.connect { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); flex: 1; min-width: 180px; }
.field input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
.sent { margin: 0; padding-left: 18px; color: var(--muted); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
</style>
