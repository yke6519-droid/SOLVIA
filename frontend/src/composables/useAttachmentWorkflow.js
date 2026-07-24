import { uploadAttachment } from '../api'

/**
 * 对话附件工作流。
 *
 * 附件状态仍由 App.vue 持有，是因为会话切换、流式发送和附件选择器
 * 都需要共享同一份响应式数据；这里集中封装上传、移除和清理逻辑，
 * 避免把文件上传的业务细节继续堆在页面组件中。
 */
export function useAttachmentWorkflow({
  pendingAttachments,
  attachmentUploading,
  attachmentError,
  ensureActiveSession,
  showToast,
  errorMessage,
}) {
  /**
   * 上传文件并绑定到当前会话。
   *
   * 后端只返回附件元数据和 attachment_id，真实文件路径不会暴露给
   * 前端或 Agent；后续发送消息时由 useChatStream 携带 attachment_id。
   */
  async function handleConversationAttachment(file) {
    if (!file) return

    attachmentError.value = ''
    attachmentUploading.value = true
    try {
      const sessionId = await ensureActiveSession()
      const data = await uploadAttachment(sessionId, file)
      if (!data?.attachment?.attachment_id) {
        throw new Error('后端没有返回有效的附件 ID')
      }
      pendingAttachments.value = [
        ...pendingAttachments.value,
        data.attachment,
      ]
      showToast(`附件已加入对话：${data.attachment.filename}`)
    } catch (error) {
      attachmentError.value = errorMessage(error, '附件上传失败，请稍后重试')
    } finally {
      attachmentUploading.value = false
    }
  }

  /** 从当前待发送附件列表中移除一个文件。 */
  function removeConversationAttachment(attachmentId) {
    pendingAttachments.value = pendingAttachments.value.filter(
      (attachment) => attachment.attachment_id !== attachmentId,
    )
  }

  /** 清理未发送附件，供切换会话、退出登录和新建会话复用。 */
  function clearAttachments() {
    pendingAttachments.value = []
    attachmentError.value = ''
  }

  return {
    handleConversationAttachment,
    removeConversationAttachment,
    clearAttachments,
  }
}
