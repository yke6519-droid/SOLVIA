<script setup>
import { nextTick, ref } from 'vue'
import AskUserCard from '../AskUserCard.vue'
import MarkdownMessage from '../MarkdownMessage.vue'
import PowerChart from '../PowerChart.vue'

defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
  pendingQuestion: {
    type: Object,
    default: null,
  },
  isLoadingMessages: Boolean,
  isLoadingOlderMessages: Boolean,
  hasOlderMessages: Boolean,
  expandedMessageId: {
    type: [String, Number],
    default: null,
  },
  theme: {
    type: String,
    default: 'dark',
  },
})

const emit = defineEmits([
  'load-older',
  'update-question-answer',
  'submit-question',
  'toggle-tool-details',
  'download-file',
  'notify',
])

const listElement = ref(null)

function formatFileSize(value) {
  const size = Number(value || 0)
  if (!Number.isFinite(size) || size <= 0) return '文件大小未知'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function formatTokenCount(value) {
  const count = Number(value || 0)
  return Number.isFinite(count) ? count.toLocaleString('zh-CN') : '0'
}

function chartDataTypeLabel(value) {
  const labels = {
    actual: '实际数据',
    predicted: '预测数据',
    comparison: '预测与实际对比',
  }
  return labels[String(value || '').toLowerCase()] || '发电量数据'
}

function chartStationLabel(chartData) {
  const metadata = chartData?.metadata || {}
  return metadata.station_full_name || metadata.station || metadata.station_name || '未标注站点'
}

function chartGranularityLabel(chartData) {
  const metadata = chartData?.metadata || {}
  const granularity = String(metadata.granularity || metadata.time_granularity || '').toLowerCase()
  if (granularity === 'hourly') return '逐小时'
  if (granularity === 'daily') return '按日汇总'
  const labels = chartData?.x_axis?.data || []
  return labels.some((label) => /\d{1,2}:\d{2}/.test(String(label))) ? '逐小时' : '按日汇总'
}

function chartRangeLabel(chartData) {
  const metadata = chartData?.metadata || {}
  const start = metadata.range_start || metadata.start_date || metadata.date || '未标注时间'
  const end = metadata.range_end || metadata.end_date
  return end && end !== start ? `${start} 至 ${end}` : String(start)
}

function resultMetrics(chartData) {
  const metadata = chartData?.metadata || {}
  const total = metadata.predicted_total_kwh ?? metadata.actual_total_kwh ?? '-'
  const peak = metadata.predicted_peak_value_kwh ?? metadata.actual_peak_value_kwh ?? '-'
  return [
    { label: '总发电量', value: total, unit: 'kWh' },
    { label: '峰值发电量', value: peak, unit: 'kWh' },
    { label: '数据类型', value: chartDataTypeLabel(metadata.data_type), unit: '' },
  ]
}

function getScrollMetrics() {
  return {
    scrollHeight: listElement.value?.scrollHeight || 0,
    scrollTop: listElement.value?.scrollTop || 0,
  }
}

async function restoreAfterPrepend(previousMetrics) {
  await nextTick()
  if (!listElement.value) return
  listElement.value.scrollTop = listElement.value.scrollHeight
    - (previousMetrics?.scrollHeight || 0)
    + (previousMetrics?.scrollTop || 0)
}

async function scrollToBottom() {
  await nextTick()
  if (listElement.value) listElement.value.scrollTop = listElement.value.scrollHeight
}

// 历史分页和流式对话只调用语义方法，不依赖子组件内部 DOM 结构。
defineExpose({
  getScrollMetrics,
  restoreAfterPrepend,
  scrollToBottom,
})
</script>

<template>
  <div ref="listElement" class="message-list">
    <a-button
      v-if="!isLoadingMessages && hasOlderMessages"
      class="load-older-button"
      size="small"
      :disabled="isLoadingOlderMessages"
      :loading="isLoadingOlderMessages"
      @click="$emit('load-older')"
    >
      加载更早消息
    </a-button>
    <div v-if="isLoadingMessages" class="message-loading">
      <a-spin size="small" />
      正在加载历史消息…
    </div>
    <a-empty
      v-else-if="messages.length === 0"
      class="message-empty"
      image="simple"
      description="这是一个新的会话，输入任务开始吧。"
    />

    <article
      v-for="message in messages"
      :key="message.id"
      class="message-row"
      :class="message.role"
    >
      <div v-if="message.role === 'assistant'" class="assistant-avatar">S</div>
      <div class="message-body">
        <div class="message-meta">
          <span>{{ message.role === 'assistant' ? 'Solvia' : '你' }}</span>
          <span>{{ message.time }}</span>
        </div>
        <div
          class="message-card"
          :class="{
            'result-card': message.status === 'complete' && message.chartData,
            'message-error': message.status === 'error',
          }"
        >
          <div v-if="message.attachments?.length" class="message-attachments">
            <div
              v-for="attachment in message.attachments"
              :key="attachment.attachment_id"
              class="message-attachment-item"
            >
              <span>FILE</span>{{ attachment.filename }}
            </div>
          </div>

          <MarkdownMessage
            v-if="message.content"
            :content="message.content"
            :streaming="message.status === 'streaming'"
          />

          <div v-if="message.files?.length" class="generated-files" aria-label="生成文件">
            <div v-for="file in message.files" :key="file.file_id" class="generated-file-card">
              <div class="generated-file-meta">
                <span class="generated-file-icon">FILE</span>
                <div>
                  <strong>{{ file.filename }}</strong>
                  <small>{{ formatFileSize(file.size_bytes) }}</small>
                </div>
              </div>
              <a-button
                class="generated-file-download"
                type="primary"
                size="small"
                @click="$emit('download-file', file)"
              >
                下载文件
              </a-button>
            </div>
          </div>
          <p v-else-if="message.status === 'streaming'" class="message-placeholder">
            {{
              message.processSteps?.length
                ? message.processSteps[message.processSteps.length - 1].label
                : '正在理解你的任务…'
            }}
          </p>

          <AskUserCard
            v-if="
              pendingQuestion?.messageId === message.id
                && (
                  message.status === 'waiting'
                  || pendingQuestion.status === 'submitting'
                  || pendingQuestion.status === 'submitted'
                )
            "
            :question="pendingQuestion.question"
            :answer="pendingQuestion.answer"
            :status="pendingQuestion.status"
            :submitted-answer="pendingQuestion.submittedAnswer"
            :error="pendingQuestion.error"
            @update:answer="$emit('update-question-answer', $event)"
            @submit="$emit('submit-question')"
          />

          <div
            v-if="
              message.processSteps?.length
                && ['streaming', 'waiting', 'stopped', 'error'].includes(message.status)
            "
            class="execution-track"
          >
            <div
              v-for="step in message.processSteps"
              :key="step.id"
              class="execution-item"
              :class="[step.status, step.type]"
            >
              <span></span>{{ step.label }}
            </div>
          </div>

          <div v-if="message.status === 'complete' && message.chartData" class="prediction-result">
            <div class="result-topline">
              <span>发电分析结果</span>
              <span>{{ chartDataTypeLabel(message.chartData.metadata?.data_type) }}</span>
            </div>
            <div class="result-context">
              <div><span>站点信息</span><strong>{{ chartStationLabel(message.chartData) }}</strong></div>
              <div><span>时间跨度</span><strong>{{ chartRangeLabel(message.chartData) }}</strong></div>
              <div><span>统计粒度</span><strong>{{ chartGranularityLabel(message.chartData) }}</strong></div>
            </div>
            <div class="metric-row">
              <div v-for="metric in resultMetrics(message.chartData)" :key="metric.label">
                <span>{{ metric.label }}</span>
                <strong>{{ metric.value }} <em v-if="metric.unit">{{ metric.unit }}</em></strong>
              </div>
            </div>
            <PowerChart class="chart-shell" :chart-data="message.chartData" :theme="theme" />
            <div class="result-actions">
              <a-button
                class="text-button"
                type="text"
                size="small"
                @click="$emit('toggle-tool-details', message.id)"
              >
                {{ expandedMessageId === message.id ? '收起执行详情' : '查看执行详情' }} ↗
              </a-button>
              <a-button
                class="export-button"
                type="primary"
                size="small"
                @click="$emit('notify', '导出功能将在后续版本接入')"
              >
                导出预测结果
              </a-button>
            </div>
            <div v-if="expandedMessageId === message.id" class="tool-details">
              <div
                v-for="(event, index) in message.toolEvents"
                :key="`${message.id}-detail-${index}`"
              >
                {{ event.name }} · {{ event.result || (event.type === 'start' ? '执行中' : '完成') }}
              </div>
              <div v-if="!message.toolEvents.length">本次任务没有额外工具事件</div>
            </div>
          </div>
        </div>

        <div
          v-if="message.role === 'assistant' && message.tokenUsage"
          class="token-usage"
          aria-label="本轮 Token 用量"
        >
          本轮消耗 Token：输入 {{ formatTokenCount(message.tokenUsage.input_tokens) }}
          · 输出 {{ formatTokenCount(message.tokenUsage.output_tokens) }}
          · 合计 {{ formatTokenCount(message.tokenUsage.total_tokens) }}
        </div>
      </div>
    </article>
  </div>
</template>
