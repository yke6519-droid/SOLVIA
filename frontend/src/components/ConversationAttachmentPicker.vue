<script setup>
import { ref } from 'vue'

defineProps({
  attachments: { type: Array, default: () => [] },
  uploading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['select', 'remove'])
const uploadControl = ref(null)

function open() {
  // 文件选择由 Ant Upload 统一管理；这里仅通过组件实例触发它内部的 input。
  // 业务层不再自行维护原生文件 input，后续替换上传实现时只需调整组件。
  const uploadRoot = uploadControl.value?.$el
  const input = uploadRoot?.querySelector?.('input[type="file"]')
  input?.click()
}

function handleBeforeUpload(file) {
  emit('select', file)
  // 文件上传由父组件的业务接口负责，Ant Upload 不自动发起请求。
  return false
}

defineExpose({ open })
</script>

<template>
  <a-upload
    ref="uploadControl"
    class="conversation-attachment-upload"
    accept=".xlsx,.xls,.csv,.txt,.md"
    :multiple="false"
    :show-upload-list="false"
    :before-upload="handleBeforeUpload"
  />
  <div v-if="attachments.length || uploading || error" class="conversation-attachment-strip">
    <div v-for="attachment in attachments" :key="attachment.attachment_id" class="conversation-attachment-chip">
      <span class="conversation-attachment-icon">FILE</span>
      <span class="conversation-attachment-name" :title="attachment.filename">{{ attachment.filename }}</span>
      <a-button
        type="text"
        size="small"
        aria-label="移除附件"
        @click="emit('remove', attachment.attachment_id)"
      >
        ×
      </a-button>
    </div>
    <span v-if="uploading" class="conversation-attachment-status"><a-spin size="small" />正在上传附件…</span>
    <a-alert v-if="error" class="conversation-attachment-error" type="error" :message="error" />
  </div>
</template>

<style scoped>
.conversation-attachment-upload { position: absolute; width: 1px; height: 1px; overflow: hidden; opacity: 0; pointer-events: none; }
.conversation-attachment-upload :deep(.ant-upload) { width: 1px; height: 1px; }
.conversation-attachment-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0 0 8px; }
.conversation-attachment-chip { display: inline-flex; align-items: center; gap: 7px; max-width: 270px; padding: 6px 8px; color: var(--text-soft); border: 1px solid var(--border-subtle); border-radius: 7px; background: var(--surface-soft); font-size: 12px; }
.conversation-attachment-icon { color: var(--accent); font-family: 'Space Mono', monospace; font-size: 9px; font-weight: 700; }
.conversation-attachment-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conversation-attachment-chip button { width: 22px; height: 22px; min-width: 22px; padding: 0; color: var(--text-muted); background: transparent; font-size: 15px; line-height: 1; }
.conversation-attachment-chip button:hover { color: var(--danger); }
.conversation-attachment-status { display: inline-flex; align-items: center; gap: 6px; color: var(--text-muted); font-size: 12px; }
.conversation-attachment-error { padding: 4px 8px; font-size: 12px; }
</style>
