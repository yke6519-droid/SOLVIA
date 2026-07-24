<script setup>
import { computed } from 'vue'

const emit = defineEmits(['update:active-view'])
const props = defineProps({
  title: {
    type: String,
    default: '新会话',
  },
  activeView: {
    type: String,
    default: 'conversation',
  },
})

const selectedView = computed({
  get: () => props.activeView,
  set: (value) => emit('update:active-view', value),
})

const viewOptions = [
  { label: '对话', value: 'conversation' },
  { label: '任务详情', value: 'details' },
]
</script>

<template>
  <div class="conversation-header">
    <h2>{{ title }}</h2>
    <a-segmented
      v-model:value="selectedView"
      class="view-switcher"
      :options="viewOptions"
      aria-label="会话视图"
    />
  </div>
</template>
