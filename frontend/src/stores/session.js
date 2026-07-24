import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 会话列表状态中心。
 *
 * 网络请求和消息流暂时仍由 App.vue 调度，本 Store 先统一会话列表、
 * 当前会话和分页状态，为后续拆分 conversation/session composable 做准备。
 */
export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const activeSessionId = ref('')
  const isLoadingSessions = ref(false)
  const isLoadingMoreSessions = ref(false)
  const hasMoreSessions = ref(false)
  const nextSessionCursor = ref(null)
  const sessionTotal = ref(0)

  function resetSessionState() {
    sessions.value = []
    activeSessionId.value = ''
    isLoadingSessions.value = false
    isLoadingMoreSessions.value = false
    hasMoreSessions.value = false
    nextSessionCursor.value = null
    sessionTotal.value = 0
  }

  return {
    sessions,
    activeSessionId,
    isLoadingSessions,
    isLoadingMoreSessions,
    hasMoreSessions,
    nextSessionCursor,
    sessionTotal,
    resetSessionState,
  }
})
