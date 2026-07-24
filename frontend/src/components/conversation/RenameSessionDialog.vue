<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
  session: {
    type: Object,
    default: null,
  },
  loading: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close', 'confirm'])
const title = ref('')

// 每次打开弹窗都从当前会话同步名称，避免保留上一次编辑的内容。
watch(
  () => [props.open, props.session?.id],
  () => {
    if (props.open) title.value = props.session?.title || ''
  },
  { immediate: true },
)

const characterCount = computed(() => Array.from(title.value || '').length)
const normalizedTitle = computed(() => String(title.value || '').trim())
const canConfirm = computed(() => Boolean(normalizedTitle.value) && !props.loading)

function close() {
  if (!props.loading) emit('close')
}

function confirm() {
  if (!canConfirm.value) return
  // 组件层先做长度保护，业务层仍会再次校验，避免绕过限制。
  emit('confirm', Array.from(normalizedTitle.value).slice(0, 10).join(''))
}
</script>

<template>
  <a-modal
    :open="open"
    :width="420"
    :footer="null"
    :closable="!loading"
    :mask-closable="false"
    :keyboard="!loading"
    :get-container="false"
    class="rename-session-modal"
    @cancel="close"
  >
    <div class="rename-session-content">
      <div class="rename-session-heading">
        <div class="rename-session-mark" aria-hidden="true">↗</div>
        <div>
          <p class="rename-session-kicker">会话设置</p>
          <h2>重命名会话</h2>
        </div>
      </div>

      <p class="rename-session-description">给这段运营任务一个更容易识别的名称。</p>

      <a-form class="rename-session-form" @finish="confirm">
        <div class="rename-session-label-row">
          <label for="rename-session-title">会话名称</label>
          <span>{{ characterCount }}/10</span>
        </div>
        <a-input
          id="rename-session-title"
          v-model:value="title"
          size="large"
          :maxlength="10"
          autocomplete="off"
          placeholder="例如：英杰站今日预测"
          :disabled="loading"
          @press-enter="confirm"
        />
        <div class="rename-session-actions">
          <a-button html-type="button" :disabled="loading" @click="close">取消</a-button>
          <a-button
            type="primary"
            html-type="button"
            :loading="loading"
            :disabled="!canConfirm"
            @click="confirm"
          >
            保存名称
          </a-button>
        </div>
      </a-form>
    </div>
  </a-modal>
</template>

<style scoped>
.rename-session-content { padding: 6px 2px 1px; color: var(--text-main); }
.rename-session-heading { display: flex; align-items: center; gap: 12px; }
.rename-session-mark { width: 34px; height: 34px; display: grid; place-items: center; color: var(--ink-on-accent); border-radius: 10px; background: linear-gradient(135deg, var(--accent), var(--accent-soft)); font-size: 18px; font-weight: 700; transform: rotate(-8deg); }
.rename-session-kicker { margin: 0 0 3px; color: var(--accent); font-size: 11px; font-weight: 650; letter-spacing: .1em; }
.rename-session-heading h2 { margin: 0; color: var(--text-main); font-size: 21px; font-weight: 650; letter-spacing: -.03em; }
.rename-session-description { margin: 20px 0 18px; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.rename-session-form { display: flex; flex-direction: column; gap: 10px; }
.rename-session-label-row { display: flex; align-items: center; justify-content: space-between; color: var(--text-soft); font-size: 12px; font-weight: 600; }
.rename-session-label-row span { color: var(--text-faint); font-family: 'Space Mono', monospace; font-size: 10px; font-weight: 400; }
.rename-session-form :deep(.ant-input) { color: var(--text-main); border-color: var(--border-strong); border-radius: 8px; background: var(--surface-input); box-shadow: none; }
.rename-session-form :deep(.ant-input::placeholder) { color: var(--text-faint); }
.rename-session-form :deep(.ant-input:hover), .rename-session-form :deep(.ant-input:focus) { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 13%, transparent); }
.rename-session-actions { display: flex; justify-content: flex-end; gap: 9px; margin-top: 12px; }
.rename-session-actions :deep(.ant-btn) { min-width: 78px; height: 36px; border-radius: 8px; font-size: 13px; font-weight: 650; }
.rename-session-actions :deep(.ant-btn-default) { color: var(--text-muted); border-color: var(--border-strong); background: transparent; }
.rename-session-actions :deep(.ant-btn-default:hover) { color: var(--text-main); border-color: var(--accent); }
.rename-session-actions :deep(.ant-btn-primary) { color: var(--ink-on-accent); border-color: var(--accent); background: var(--accent); box-shadow: 0 6px 16px color-mix(in srgb, var(--accent) 22%, transparent); }
.rename-session-actions :deep(.ant-btn-primary:hover) { border-color: var(--accent-hover); background: var(--accent-hover); }
</style>
