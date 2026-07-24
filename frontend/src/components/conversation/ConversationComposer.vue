<script setup>
import { computed, ref } from 'vue'
import { ArrowUpOutlined, PaperClipOutlined, StopOutlined } from '@ant-design/icons-vue'
import ConversationAttachmentPicker from '../ConversationAttachmentPicker.vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  attachments: {
    type: Array,
    default: () => [],
  },
  uploading: Boolean,
  error: {
    type: String,
    default: '',
  },
  streaming: Boolean,
  waitingForReply: Boolean,
})

const emit = defineEmits([
  'update:modelValue',
  'select-attachment',
  'remove-attachment',
  'send',
  'stop',
])

const attachmentPicker = ref(null)
const composerInput = ref(null)

const inputValue = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

function openAttachmentPicker() {
  if (props.streaming || props.waitingForReply) return
  attachmentPicker.value?.open()
}

function focus() {
  composerInput.value?.focus()
}

// 父组件只下达“打开”和“聚焦”指令，不再直接操作内部 DOM。
defineExpose({
  openAttachmentPicker,
  focus,
})
</script>

<template>
  <div class="composer-wrap">
    <ConversationAttachmentPicker
      ref="attachmentPicker"
      :attachments="attachments"
      :uploading="uploading"
      :error="error"
      @select="$emit('select-attachment', $event)"
      @remove="$emit('remove-attachment', $event)"
    />
    <div class="composer">
      <a-button
        class="composer-add"
        type="text"
        aria-label="添加对话附件"
        :disabled="streaming || waitingForReply"
        @click="openAttachmentPicker"
      >
        <template #icon><PaperClipOutlined /></template>
      </a-button>
      <a-input
        ref="composerInput"
        v-model:value="inputValue"
        class="composer-input"
        placeholder="告诉我你想完成的光伏任务"
        :disabled="streaming || waitingForReply"
        :bordered="false"
        @press-enter="$emit('send')"
      />
      <a-button
        v-if="streaming"
        class="stop-button"
        type="default"
        danger
        aria-label="停止当前任务"
        @click="$emit('stop')"
      >
        <template #icon><StopOutlined /></template>
        停止生成
      </a-button>
      <a-button
        v-else
        class="send-button"
        type="primary"
        aria-label="发送消息"
        @click="$emit('send')"
      >
        <template #icon><ArrowUpOutlined /></template>
      </a-button>
    </div>
  </div>
</template>
