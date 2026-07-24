import { ref } from 'vue'
import { executePowerImport, previewPowerImport } from '../api'

/**
 * 文件导入业务状态。
 *
 * 组件只负责展示 ImportDataDialog；预览和确认入库的请求、状态和错误处理
 * 统一放在这里，避免 App.vue 继续堆积一整套导入流程。
 */
export function useImportWorkflow({ isStreaming, pendingQuestion, showToast, errorMessage }) {
  const importDialogVisible = ref(false)
  const importFile = ref(null)
  const importFilename = ref('')
  const importPreview = ref(null)
  const importLoading = ref(false)
  const importExecuting = ref(false)
  const importError = ref('')

  function openImportDialog() {
    if (isStreaming.value || pendingQuestion.value) {
      showToast('当前任务正在执行，请完成后再导入文件')
      return
    }
    importDialogVisible.value = true
    importFile.value = null
    importFilename.value = ''
    importPreview.value = null
    importLoading.value = false
    importExecuting.value = false
    importError.value = ''
  }

  function closeImportDialog() {
    if (importLoading.value || importExecuting.value) return
    importDialogVisible.value = false
    importFile.value = null
    importFilename.value = ''
    importPreview.value = null
    importLoading.value = false
    importExecuting.value = false
    importError.value = ''
  }

  async function handleImportFile(file) {
    importFile.value = file
    importFilename.value = file.name
    importPreview.value = null
    importError.value = ''
    importLoading.value = true
    try {
      const data = await previewPowerImport(file)
      importPreview.value = data.preview || null
      if (!importPreview.value) importError.value = '后端没有返回有效的文件预览'
    } catch (error) {
      importError.value = errorMessage(error, '文件解析失败，请检查文件格式')
    } finally {
      importLoading.value = false
    }
  }

  async function confirmImport() {
    if (!importFile.value || !importPreview.value || importExecuting.value) return
    importExecuting.value = true
    importError.value = ''
    try {
      const result = await executePowerImport(importFile.value)
      importExecuting.value = false
      closeImportDialog()
      showToast(`导入完成：新增 ${result.total_inserted || 0} 条，跳过 ${result.total_skipped || 0} 条`)
    } catch (error) {
      importError.value = errorMessage(error, '数据入库失败，请稍后重试')
    } finally {
      importExecuting.value = false
    }
  }

  return {
    importDialogVisible,
    importFile,
    importFilename,
    importPreview,
    importLoading,
    importExecuting,
    importError,
    openImportDialog,
    closeImportDialog,
    handleImportFile,
    confirmImport,
  }
}
