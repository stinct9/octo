<script setup lang="ts">
// Export and import (octo/export.py, docs/export-plan.md). The work runs in the Octo app, never in the lab server: an
// import stops the server, and the password belongs in the Mac's own fields, not in a request. Inside the app the
// buttons hand over to it (window.webkit.messageHandlers.octo); in a browser the card says where to find them.
import { onMounted, ref } from 'vue'

interface Status { last_export_at: number | null; since: number }

const status = ref<Status | null>(null)
const octo = (window as unknown as { webkit?: { messageHandlers?: { octo?: { postMessage: (m: string) => void } } } })
  .webkit?.messageHandlers?.octo
const inApp = !!octo

onMounted(() => {
  fetch('/api/octo/settings/export').then((r) => (r.ok ? r.json() : null)).then((s) => { status.value = s }).catch(() => {})
})

const when = (t: number) => new Date(t * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <section class="card pad" aria-labelledby="ex-h">
    <h2 id="ex-h">Export and import</h2>
    <p>Everything in one encrypted file: conversations, transcripts, reviews, people, voiceprints, your settings and
      keys, and the recordings it still has. For a new Mac, a clean reinstall, or a copy kept somewhere else. Its
      password is yours; Octo doesn't keep it.</p>
    <p v-if="status" class="muted small">
      <template v-if="status.last_export_at">Last export: {{ when(status.last_export_at) }}<template v-if="status.since">;
        {{ status.since }} conversation{{ status.since === 1 ? '' : 's' }} since {{ status.since === 1 ? "isn't" : "aren't" }} in it</template>.</template>
      <template v-else>Not exported yet.</template>
    </p>
    <div v-if="inApp" class="line">
      <button type="button" class="btn" @click="octo!.postMessage('export')">Export…</button>
      <button type="button" class="btn btn-small" @click="octo!.postMessage('import')">Import…</button>
    </div>
    <p v-else class="small">These run in the Octo app (open it from Applications or the octopus in the menu bar):
      <b>File → Export Lab…</b> and <b>File → Import Lab…</b>, or in Terminal <code>octo export</code> and
      <code>octo import "file.dmg"</code>.</p>
    <p class="muted small">Importing replaces this lab; one with recordings is set aside for 14 days, never merged.
      Anything deleted after an export was made comes back if you import it.</p>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
p { margin: 0; }
.pad { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
</style>
