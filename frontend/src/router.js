import { inject, reactive, readonly } from 'vue'
import { getStoredAuth } from './api'

const ROUTER_KEY = Symbol('solar-agent-router')
const routeState = reactive({ path: '' })

function hasValidAuth() {
  return Boolean(getStoredAuth()?.access_token)
}

function normalizePath(path) {
  const value = String(path || '/').split('?')[0].replace(/\/{2,}/g, '/')
  if (value.length > 1) return value.replace(/\/$/, '')
  return value
}

function guardPath(path) {
  const normalized = normalizePath(path)
  const authenticated = hasValidAuth()
  if (normalized === '/login') return authenticated ? '/workspace' : '/login'
  if (normalized === '/' || normalized === '/workspace') return authenticated ? '/workspace' : '/login'
  return authenticated ? '/workspace' : '/login'
}

export function createRouter() {
  const syncLocation = (replace = false) => {
    const nextPath = guardPath(window.location.pathname)
    if (nextPath !== normalizePath(window.location.pathname)) {
      const method = replace ? 'replaceState' : 'replaceState'
      window.history[method]({}, '', nextPath)
    }
    routeState.path = nextPath
  }

  const router = {
    route: readonly(routeState),
    push(path) {
      const nextPath = guardPath(path)
      if (nextPath === routeState.path) return
      window.history.pushState({}, '', nextPath)
      routeState.path = nextPath
    },
    replace(path) {
      const nextPath = guardPath(path)
      window.history.replaceState({}, '', nextPath)
      routeState.path = nextPath
    },
    install(app) {
      syncLocation(true)
      window.addEventListener('popstate', () => syncLocation(true))
      app.provide(ROUTER_KEY, router)
    },
  }

  return router
}

export function useRouter() {
  const router = inject(ROUTER_KEY)
  if (!router) throw new Error('useRouter 必须在 SolarAgent 路由上下文中使用')
  return router
}

export function useRoute() {
  return useRouter().route
}
