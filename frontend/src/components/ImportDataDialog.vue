<script setup>
defineProps({
  visible: { type: Boolean, default: false },
  filename: { type: String, default: '' },
  preview: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  executing: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'select-file', 'confirm'])

function handleBeforeUpload(file) {
  emit('select-file', file)
  // 文件由现有业务接口手动上传，阻止 Upload 组件自动发起请求。
  return false
}
</script>

<template>
  <a-modal
    :open="visible"
    :width="650"
    :footer="null"
    :closable="false"
    :mask-closable="false"
    :get-container="false"
    wrap-class-name="import-dialog-modal"
    @cancel="emit('close')"
  >
    <section class="import-dialog" aria-labelledby="import-dialog-title">
      <div class="import-dialog-header">
        <div>
          <p class="import-dialog-kicker">数据资产</p>
          <h2 id="import-dialog-title">导入发电量文件</h2>
        </div>
        <a-button
          class="import-dialog-close"
          type="text"
          aria-label="关闭"
          :disabled="loading || executing"
          @click="emit('close')"
        >
          ×
        </a-button>
      </div>

      <p class="import-dialog-description">上传 Excel 后，系统会先预览和清洗数据，确认后才会写入电厂数据库。</p>

      <a-upload-dragger
        v-if="!filename && !loading"
        class="import-picker"
        accept=".xlsx,.xls"
        :before-upload="handleBeforeUpload"
        :show-upload-list="false"
        :multiple="false"
      >
        <span class="import-picker-icon">＋</span>
        <span>
          <strong>选择发电量 Excel 文件</strong>
          <small>支持 .xlsx、.xls，单个文件不超过 20 MB</small>
        </span>
      </a-upload-dragger>

      <div v-else class="import-file-summary">
        <span class="import-file-icon">XLS</span>
        <div>
          <strong>{{ filename }}</strong>
          <small v-if="loading">正在解析和清洗文件…</small>
          <small v-else>文件已读取，等待预览结果</small>
        </div>
        <a-upload
          v-if="!loading && !executing"
          accept=".xlsx,.xls"
          :before-upload="handleBeforeUpload"
          :show-upload-list="false"
          :multiple="false"
        >
          <a-button class="import-reselect" size="small">重新选择</a-button>
        </a-upload>
      </div>

      <div v-if="loading" class="import-loading">
        <a-spin size="small" />
        正在生成数据预览
      </div>

      <div v-if="preview" class="import-preview">
        <div class="import-preview-heading">
          <span>清洗预览</span>
          <strong>{{ preview.station_count }} 个站点</strong>
        </div>
        <div class="import-station-list">
          <article v-for="station in preview.stations" :key="station.station_code" class="import-station-item">
            <div class="import-station-title">
              <strong>{{ station.name }}</strong>
              <span>{{ station.capacity_kw ? `${station.capacity_kw} kW` : '装机容量未知' }}</span>
            </div>
            <div class="import-station-meta">
              <span>{{ station.original_rows }} 行 → {{ station.cleaned_rows }} 行</span>
              <span v-if="station.removed_days">删除 {{ station.removed_days }} 天零值数据</span>
              <span v-if="station.time_range">{{ station.time_range[0] }} ~ {{ station.time_range[1] }}</span>
            </div>
          </article>
        </div>
        <a-alert
          class="import-warning"
          type="warning"
          show-icon
          message="确认后将写入 MySQL，重复时间点会自动跳过。"
        />
      </div>

      <a-alert v-if="error" class="import-error" type="error" show-icon :message="error" />

      <div class="import-dialog-actions">
        <a-button
          class="import-secondary-button"
          :disabled="loading || executing"
          @click="emit('close')"
        >
          取消
        </a-button>
        <a-button
          class="import-primary-button"
          type="primary"
          :disabled="!preview || loading || executing"
          :loading="executing"
          @click="emit('confirm')"
        >
          确认入库
        </a-button>
      </div>
    </section>
  </a-modal>
</template>

<style scoped>
.import-dialog { width: 100%; max-height: min(720px, calc(100vh - 96px)); overflow: auto; color: var(--text-main); }
.import-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.import-dialog-kicker { margin: 0 0 5px; color: var(--accent); font-size: 12px; letter-spacing: .12em; }
.import-dialog h2 { margin: 0; font-size: 24px; letter-spacing: -.03em; }
.import-dialog-close { width: 30px; height: 30px; color: var(--text-muted); border-radius: 50%; background: var(--surface-soft); font-size: 22px; line-height: 1; }
.import-dialog-close:hover:not(:disabled) { color: var(--text-main); background: var(--surface-hover); }
.import-dialog-description { margin: 16px 0 20px; color: var(--text-muted); font-size: 14px; line-height: 1.6; }
.import-picker { display: block; width: 100%; color: var(--text-main); }
.import-picker :deep(.ant-upload-drag) { border-color: color-mix(in srgb, var(--accent) 52%, var(--border-strong)); border-radius: 10px; background: var(--surface-soft); }
.import-picker :deep(.ant-upload-drag:hover) { border-color: var(--accent); background: var(--surface-hover); }
.import-picker :deep(.ant-upload-btn) { padding: 22px !important; }
.import-picker :deep(.ant-upload-drag-container) { display: flex; align-items: center; gap: 14px; color: var(--text-main); text-align: left; }
.import-picker-icon { display: grid; place-items: center; width: 38px; height: 38px; color: var(--ink-on-accent); border-radius: 9px; background: var(--accent); font-size: 24px; }
.import-picker strong, .import-file-summary strong { display: block; font-size: 15px; }
.import-picker small, .import-file-summary small { display: block; margin-top: 5px; color: var(--text-muted); font-size: 12px; }
.import-file-summary { display: flex; align-items: center; gap: 12px; padding: 14px; border: 1px solid var(--border-subtle); border-radius: 9px; background: var(--surface-soft); }
.import-file-icon { display: grid; place-items: center; width: 40px; height: 40px; color: var(--ink-on-accent); border-radius: 8px; background: var(--accent); font-family: 'Space Mono', monospace; font-size: 10px; font-weight: 700; }
.import-reselect { margin-left: auto; padding: 7px 10px; color: var(--text-muted); border: 1px solid var(--border-subtle); border-radius: 6px; background: transparent; font-size: 12px; }
.import-reselect:hover { color: var(--text-main); border-color: var(--border-strong); }
.import-loading { display: flex; align-items: center; gap: 8px; margin-top: 14px; color: var(--text-muted); font-size: 13px; }
.import-preview { margin-top: 18px; padding: 15px; border: 1px solid var(--border-subtle); border-radius: 10px; background: var(--surface-soft); }
.import-preview-heading { display: flex; justify-content: space-between; color: var(--text-soft); font-size: 13px; }
.import-preview-heading strong { color: var(--accent); }
.import-station-list { display: grid; gap: 8px; margin-top: 12px; }
.import-station-item { padding: 12px; border: 1px solid var(--border-subtle); border-radius: 8px; background: color-mix(in srgb, var(--surface-input) 60%, transparent); }
.import-station-title, .import-station-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.import-station-title strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.import-station-title span, .import-station-meta { color: var(--text-muted); font-size: 11px; }
.import-station-meta { justify-content: flex-start; flex-wrap: wrap; margin-top: 7px; }
.import-warning { margin: 14px 0 0; font-size: 12px; }
.import-error { margin: 14px 0 0; font-size: 12px; line-height: 1.5; }
.import-dialog-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.import-secondary-button, .import-primary-button { height: auto; padding: 9px 17px; border-radius: 7px; font-size: 13px; font-weight: 600; }
.import-secondary-button { color: var(--text-muted); border: 1px solid var(--border-subtle); background: transparent; }
.import-secondary-button:hover:not(:disabled) { color: var(--text-main); border-color: var(--border-strong); }
.import-primary-button { color: var(--primary-action-ink); background: var(--primary-action); }
.import-primary-button:hover:not(:disabled) { background: var(--primary-action-hover); }
.import-secondary-button:disabled, .import-primary-button:disabled, .import-dialog-close:disabled { cursor: not-allowed; opacity: .55; }
</style>
