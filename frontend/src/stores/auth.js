import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getStoredAuth } from '../api'

/**
 * 认证状态中心。
 *
 * 第一阶段只统一管理用户信息和登录过期弹窗；Token 的持久化、刷新和
 * Axios 拦截逻辑仍复用现有 api.js，避免重写已稳定的认证链路。
 */
export const useAuthStore = defineStore('auth', () => {
  const currentUser = ref(getStoredAuth()?.user || null)
  const authExpiredModalVisible = ref(false)

  function setAuthenticatedUser(user) {
    currentUser.value = user || null
    authExpiredModalVisible.value = false
  }

  function markAuthExpired() {
    currentUser.value = null
    authExpiredModalVisible.value = true
  }

  function clearAuthenticatedUser() {
    currentUser.value = null
  }

  return {
    currentUser,
    authExpiredModalVisible,
    setAuthenticatedUser,
    markAuthExpired,
    clearAuthenticatedUser,
  }
})
