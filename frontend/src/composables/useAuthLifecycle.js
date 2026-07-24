import { onBeforeUnmount, onMounted } from 'vue'
import {
  clearAuth,
  getAuthRefreshLeadMs,
  getStoredAuth,
  logout as logoutApi,
  refreshAccessToken,
} from '../api'

const AUTH_ACTIVE_WINDOW_MS = 30 * 60 * 1000

/**
 * 认证生命周期管理。
 *
 * 这里把“用户是否活跃、何时刷新 Access Token、何时弹出登录过期提示”
 * 从页面组件中抽离。它不负责渲染，只通过参数回调清理工作区和加载会话。
 */
export function useAuthLifecycle({
  router,
  isLoginRoute,
  currentUser,
  authStore,
  sessionStore,
  authExpiredModalVisible,
  showToast,
  loadSessions,
  stopStreaming,
  resetWorkspace,
}) {
  let authExpiryTimer = null
  let authIdleTimer = null
  let lastAuthActivityAt = 0
  let pageWasHidden = false
  let windowWasBlurred = false

  function isAuthSessionActive() {
    return lastAuthActivityAt > 0 && Date.now() - lastAuthActivityAt <= AUTH_ACTIVE_WINDOW_MS
  }

  function handleAuthExpired() {
    stopStreaming()
    if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
    authExpiryTimer = null
    if (authIdleTimer) window.clearTimeout(authIdleTimer)
    authIdleTimer = null
    lastAuthActivityAt = 0
    authStore.clearAuthenticatedUser()
    sessionStore.resetSessionState()
    resetWorkspace()
    if (!isLoginRoute.value) authExpiredModalVisible.value = true
  }

  function scheduleAuthIdleTimeout() {
    if (authIdleTimer) window.clearTimeout(authIdleTimer)
    authIdleTimer = null
    if (!currentUser.value || !lastAuthActivityAt) return

    const delay = Math.max(0, AUTH_ACTIVE_WINDOW_MS - (Date.now() - lastAuthActivityAt))
    authIdleTimer = window.setTimeout(() => {
      authIdleTimer = null
      if (Date.now() - lastAuthActivityAt >= AUTH_ACTIVE_WINDOW_MS) {
        handleAuthExpired()
        return
      }
      scheduleAuthIdleTimeout()
    }, delay + 50)
  }

  async function refreshAuthIfNeeded() {
    const auth = getStoredAuth({ allowExpired: true })
    if (!auth?.access_token) {
      handleAuthExpired()
      return
    }
    if (!isAuthSessionActive()) {
      scheduleAuthExpiryCheck({ atExpiry: true })
      return
    }
    if (auth.expires_at && auth.expires_at - Date.now() > getAuthRefreshLeadMs(auth)) {
      scheduleAuthExpiryCheck()
      return
    }
    try {
      const refreshed = await refreshAccessToken()
      currentUser.value = refreshed.user
      scheduleAuthExpiryCheck()
    } catch {
      // api.js 会清理本地认证状态并触发 auth-expired 事件。
    }
  }

  function scheduleAuthExpiryCheck({ atExpiry = false } = {}) {
    if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
    authExpiryTimer = null

    const auth = getStoredAuth({ allowExpired: true })
    if (!auth?.expires_at) return
    const remaining = auth.expires_at - Date.now()
    if (remaining <= 0) {
      if (isAuthSessionActive()) void refreshAuthIfNeeded()
      else handleAuthExpired()
      return
    }

    const delay = atExpiry ? remaining : Math.max(0, remaining - getAuthRefreshLeadMs(auth))
    authExpiryTimer = window.setTimeout(() => {
      authExpiryTimer = null
      if (atExpiry && !isAuthSessionActive()) {
        handleAuthExpired()
        return
      }
      void refreshAuthIfNeeded()
    }, delay)
  }

  function recordAuthActivity() {
    if (!currentUser.value || isLoginRoute.value) return
    const auth = getStoredAuth({ allowExpired: true })
    if (!auth?.access_token) return

    const wasActive = isAuthSessionActive()
    if (!wasActive && auth.expires_at && auth.expires_at <= Date.now()) {
      // 用户长时间离开后回来，不因为重新聚焦而自动续期。
      handleAuthExpired()
      return
    }
    lastAuthActivityAt = Date.now()
    scheduleAuthIdleTimeout()
    void refreshAuthIfNeeded()
  }

  function handleVisibilityChange() {
    if (document.visibilityState === 'hidden') {
      pageWasHidden = true
      return
    }
    if (pageWasHidden) {
      pageWasHidden = false
      recordAuthActivity()
    }
  }

  function handleWindowBlur() {
    windowWasBlurred = true
  }

  function handleWindowFocus() {
    if (!windowWasBlurred) return
    windowWasBlurred = false
    recordAuthActivity()
  }

  function handleAuthenticated(user) {
    authStore.setAuthenticatedUser(user)
    // 登录后的自动加载不算活跃操作，只有用户后续交互才允许续期。
    lastAuthActivityAt = 0
    scheduleAuthExpiryCheck()
    showToast('欢迎回来，' + (user.display_name || user.username))
    loadSessions()
  }

  function goToLoginAfterAuthExpired() {
    authExpiredModalVisible.value = false
    router.replace('/login')
  }

  async function logout() {
    stopStreaming()
    try {
      await logoutApi()
    } catch {
      // 后端不可用时仍然完成本地退出。
    }
    clearAuth()
    if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
    authExpiryTimer = null
    if (authIdleTimer) window.clearTimeout(authIdleTimer)
    authIdleTimer = null
    lastAuthActivityAt = 0
    authStore.clearAuthenticatedUser()
    sessionStore.resetSessionState()
    resetWorkspace()
    router.replace('/login')
    showToast('已退出登录')
  }

  onMounted(() => {
    window.addEventListener('solar-agent-auth-expired', handleAuthExpired)
    window.addEventListener('solar-agent-auth-refreshed', scheduleAuthExpiryCheck)
    window.addEventListener('pointerdown', recordAuthActivity, { passive: true })
    window.addEventListener('keydown', recordAuthActivity)
    window.addEventListener('touchstart', recordAuthActivity, { passive: true })
    window.addEventListener('blur', handleWindowBlur)
    window.addEventListener('focus', handleWindowFocus)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    if (currentUser.value && !isLoginRoute.value) {
      lastAuthActivityAt = 0
      scheduleAuthExpiryCheck()
      loadSessions()
    }
  })

  onBeforeUnmount(() => {
    window.removeEventListener('solar-agent-auth-expired', handleAuthExpired)
    window.removeEventListener('solar-agent-auth-refreshed', scheduleAuthExpiryCheck)
    window.removeEventListener('pointerdown', recordAuthActivity)
    window.removeEventListener('keydown', recordAuthActivity)
    window.removeEventListener('touchstart', recordAuthActivity)
    window.removeEventListener('blur', handleWindowBlur)
    window.removeEventListener('focus', handleWindowFocus)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
    if (authIdleTimer) window.clearTimeout(authIdleTimer)
  })

  return {
    handleAuthenticated,
    handleAuthExpired,
    goToLoginAfterAuthExpired,
    logout,
    scheduleAuthExpiryCheck,
  }
}
