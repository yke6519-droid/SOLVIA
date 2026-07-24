import { ref } from 'vue'
import {
  createSession as createSessionApi,
  deleteSession as deleteSessionApi,
  getSessionMessages,
  listSessions,
  renameSession as renameSessionApi,
} from '../api'
import { HISTORY_PAGE_SIZE, SESSION_PAGE_SIZE } from '../config'
import {
  buildSessionTitle,
  mapMessage,
  mapSession,
  sessionTitleFromMessages,
} from '../utils/conversation'

/**
 * 会话列表和历史消息管理。
 *
 * 这里统一处理游标分页、请求去重和会话切换竞态校验。组件只接收返回的
 * 状态与方法，不需要了解 before_id、next_cursor 等后端分页细节。
 */
export function useSessionHistory({
  router,
  currentUser,
  sessions,
  activeSessionId,
  isLoadingSessions,
  isLoadingMoreSessions,
  hasMoreSessions,
  nextSessionCursor,
  sessionTotal,
  messages,
  isStreaming,
  pendingQuestion,
  pendingAttachments,
  attachmentError,
  input,
  expandedMessageId,
  activeView,
  messageList,
  showToast,
  errorMessage,
  scrollToBottom,
}) {
  const isLoadingMessages = ref(false)
  const isLoadingOlderMessages = ref(false)
  const hasOlderMessages = ref(false)
  const nextBeforeMessageId = ref(null)
  let historyRequestId = 0
  const historyRequests = new Map()

  function updateLocalSessionTitle(sessionId, title) {
    const session = sessions.value.find((item) => item.id === sessionId)
    if (!session || session.titleFromServer) return
    session.title = buildSessionTitle(title)
  }

  function maybeAssignSessionTitleFromFirstMessage(sessionId, content) {
    const session = sessions.value.find((item) => item.id === sessionId)
    if (!session || session.titleFromServer) return
    if (messages.value.some((message) => message.role === 'user')) return
    updateLocalSessionTitle(sessionId, content)
  }

  function getSessionMessagesDeduped(sessionId, options = {}) {
    const limit = options.limit || HISTORY_PAGE_SIZE
    const beforeId = options.beforeId || ''
    const requestKey = `${sessionId}|${limit}|${beforeId}`
    const existing = historyRequests.get(requestKey)
    if (existing) return existing

    let request
    request = getSessionMessages(sessionId, options).finally(() => {
      if (historyRequests.get(requestKey) === request) historyRequests.delete(requestKey)
    })
    historyRequests.set(requestKey, request)
    return request
  }

  function markActiveSession(sessionId) {
    activeSessionId.value = sessionId
    sessions.value.forEach((session) => { session.active = session.id === sessionId })
  }

  async function loadSessions({ append = false } = {}) {
    if (!currentUser.value) return
    if (append) {
      if (isLoadingMoreSessions.value || !hasMoreSessions.value || !nextSessionCursor.value) return
      isLoadingMoreSessions.value = true
    } else {
      isLoadingSessions.value = true
      nextSessionCursor.value = null
    }
    if (!append) historyRequestId += 1
    try {
      const data = await listSessions({
        limit: SESSION_PAGE_SIZE,
        before: append ? nextSessionCursor.value : null,
      })
      const page = (data.sessions || []).map(mapSession)
      sessions.value = append ? [...sessions.value, ...page] : page
      sessionTotal.value = Number(data.total || sessions.value.length)
      hasMoreSessions.value = Boolean(data.has_more)
      nextSessionCursor.value = data.next_cursor || null
      if (!append) {
        // 首次进入工作台时可以清空旧消息；但如果用户已经从首页发起了
        // 新会话，loadSessions 可能与 sendMessage 并发返回，此时绝不能
        // 把刚写入的用户消息和正在接收的助手消息清掉。
        if (!activeSessionId.value && !isStreaming.value) {
          messages.value = []
        }
      }
    } catch (error) {
      showToast(errorMessage(error, '会话列表加载失败'))
    } finally {
      if (append) isLoadingMoreSessions.value = false
      else isLoadingSessions.value = false
    }
  }

  async function selectSession(session) {
    if (isStreaming.value) {
      showToast('当前会话正在处理请求，请稍后再试')
      return
    }
    // 当前会话已经加载过时直接复用，避免切换时闪过欢迎页或重复请求。
    if (activeSessionId.value === session.id && (isLoadingMessages.value || messages.value.length > 0 || !hasOlderMessages.value)) {
      return
    }
    if (pendingAttachments.value.length) {
      pendingAttachments.value = []
      attachmentError.value = ''
      showToast('切换会话时已清除未发送附件')
    }
    markActiveSession(session.id)
    messages.value = []
    hasOlderMessages.value = false
    nextBeforeMessageId.value = null
    isLoadingMessages.value = true
    const requestId = ++historyRequestId
    try {
      const data = await getSessionMessagesDeduped(session.id, { limit: HISTORY_PAGE_SIZE })
      if (requestId !== historyRequestId || activeSessionId.value !== session.id) return
      const historicalMessages = (data.messages || []).map(mapMessage)
      messages.value = historicalMessages
      hasOlderMessages.value = Boolean(data.has_more)
      nextBeforeMessageId.value = data.next_before_id || null
      if (!session.titleFromServer) {
        updateLocalSessionTitle(session.id, sessionTitleFromMessages(historicalMessages))
      }
      await scrollToBottom()
    } catch (error) {
      if (requestId !== historyRequestId || activeSessionId.value !== session.id) return
      messages.value = []
      showToast(errorMessage(error, '历史消息加载失败'))
    } finally {
      if (requestId === historyRequestId) isLoadingMessages.value = false
    }
  }

  async function loadOlderMessages() {
    const sessionId = activeSessionId.value
    if (!sessionId || !hasOlderMessages.value || !nextBeforeMessageId.value || isLoadingOlderMessages.value) return
    const previousMetrics = messageList.value?.getScrollMetrics() || {
      scrollHeight: 0,
      scrollTop: 0,
    }
    isLoadingOlderMessages.value = true
    const requestId = historyRequestId
    try {
      const data = await getSessionMessagesDeduped(sessionId, {
        limit: HISTORY_PAGE_SIZE,
        beforeId: nextBeforeMessageId.value,
      })
      if (requestId !== historyRequestId || activeSessionId.value !== sessionId) return
      const olderMessages = (data.messages || []).map(mapMessage)
      messages.value = [...olderMessages, ...messages.value]
      hasOlderMessages.value = Boolean(data.has_more)
      nextBeforeMessageId.value = data.next_before_id || null
      await messageList.value?.restoreAfterPrepend(previousMetrics)
    } catch (error) {
      if (requestId === historyRequestId && activeSessionId.value === sessionId) {
        showToast(errorMessage(error, '历史消息加载失败'))
      }
    } finally {
      isLoadingOlderMessages.value = false
    }
  }

  function createSession() {
    if (isStreaming.value || pendingQuestion.value) return
    historyRequestId += 1
    activeSessionId.value = ''
    sessions.value.forEach((session) => { session.active = false })
    messages.value = []
    hasOlderMessages.value = false
    nextBeforeMessageId.value = null
    isLoadingMessages.value = false
    input.value = ''
    pendingAttachments.value = []
    attachmentError.value = ''
    pendingQuestion.value = null
    expandedMessageId.value = null
    activeView.value = 'conversation'
    router.replace('/workspace')
  }

  async function renameSession(session, requestedTitle) {
    if (isStreaming.value || !session) return false
    const title = String(requestedTitle || '').trim()
    if (!title) {
      showToast('会话名称不能为空')
      return false
    }

    try {
      const data = await renameSessionApi(session.id, Array.from(title).slice(0, 10).join(''))
      session.title = data.title || title
      session.titleFromServer = true
      showToast('会话已重命名')
      return true
    } catch (error) {
      showToast(errorMessage(error, '会话重命名失败'))
      return false
    }
  }

  async function removeSession(session) {
    if (isStreaming.value) {
      showToast('当前会话正在处理请求，请稍后再试')
      return
    }
    try {
      await deleteSessionApi(session.id)
      sessions.value = sessions.value.filter((item) => item.id !== session.id)
      sessionTotal.value = Math.max(0, sessionTotal.value - 1)
      if (activeSessionId.value === session.id) {
        activeSessionId.value = ''
        messages.value = []
      }
      showToast('会话已删除')
    } catch (error) {
      showToast(errorMessage(error, '会话删除失败'))
    }
  }

  async function ensureActiveSession() {
    if (activeSessionId.value) return activeSessionId.value
    const data = await createSessionApi()
    const session = mapSession({ session_id: data.session_id, title: data.title })
    sessions.value = [session, ...sessions.value]
    sessionTotal.value += 1
    markActiveSession(session.id)
    return session.id
  }

  return {
    isLoadingMessages,
    isLoadingOlderMessages,
    hasOlderMessages,
    nextBeforeMessageId,
    loadSessions,
    selectSession,
    loadOlderMessages,
    createSession,
    renameSession,
    removeSession,
    ensureActiveSession,
    maybeAssignSessionTitleFromFirstMessage,
  }
}
