<script setup>
import { ref } from 'vue'

defineProps({
  attachments: { type: Array, default: () => [] },
  uploading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['select', 'remove'])
const fileInput = ref(null)

function open() {
  if (fileInput.value) fileInput.value.click()
}

function handleFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (file) emit('select', file)
}

defineExpose({ open })
</script>

<template>
  <input ref="fileInput" class="conversation-attachment-input" type="file" accept=".xlsx,.xls,.csv,.txt,.md" @change="handleFileChange" />
  <div v-if="attachments.length || uploading || error" class="conversation-attachment-strip">
    <div v-for="attachment in attachments" :key="attachment.attachment_id" class="conversation-attachment-chip">
      <span class="conversation-attachment-icon">FILE</span>
      <span class="conversation-attachment-name" :title="attachment.filename">{{ attachment.filename }}</span>
      <button type="button" aria-label="移除附件" @click="emit('remove', attachment.attachment_id)">×</button>
    </div>
    <span v-if="uploading" class="conversation-attachment-status">正在上传附件…</span>
    <span v-if="error" class="conversation-attachment-error">{{ error }}</span>
  </div>
</template>

<style scoped>
.conversation-attachment-input { display: none; }
.conversation-attachment-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
.conversation-attachment-chip { display: inline-flex; align-items: center; gap: 7px; max-width: 270px; padding: 6px 8px; color: var(--text-soft); border: 1px solid var(--border-subtle); border-radius: 7px; background: var(--surface-soft); font-size: 12px; }
.conversation-attachment-icon { color: var(--accent); font-family: 'Space Mono', monospace; font-size: 9px; font-weight: 700; }
.conversation-attachment-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-attachment-chip button { color: var(--text-muted); background: transparent; font-size: 15px; line-height: 1; }
.conversation-attachment-chip button:hover { color: var(--danger); }
.conversation-attachment-status { color: var(--text-muted); font-size: 12px; }
.conversation-attachment-error { color: var(--danger); font-size: 12px; }
</style>
