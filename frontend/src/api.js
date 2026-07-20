import axios from 'axios'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
const TOKEN_KEY = 'solar-agent-access-token'
const USER_KEY = 'solar-agent-user'
const EXPIRES_KEY = 'solar-agent-token-expires-at'

export class ApiError extends Error {
  constructor(message, status = 0, options = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = options.code || 'UNKNOWN_ERROR'
    this.retryable = Boolean(options.retryable)
    this.details = options.details || {}
    this.requestId = options.requestId || ''
  }
}

const publicClient = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

const authClient = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

authClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

authClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) handleAuthExpired()
    return Promise.reject(error)
  },
)

function handleAuthExpired() {
  clearAuth()
  window.dispatchEvent(new CustomEvent('solar-agent-auth-expired'))
}

function normalizeAxiosError(error, fallback = '请求失败，请稍后重试') {
  if (error instanceof ApiError) return error
  if (error.response) {
    const payload = error.response.data || {}
    const structured = payload.error || payload
    const detail = structured.message || payload.detail || payload.message || fallback
    return new ApiError(detail, error.response.status, {
      code: structured.code,
      retryable: structured.retryable,
      details: structured.details,
      requestId: structured.request_id || error.response.headers?.['x-request-id'],
    })
  }
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return new ApiError('后端响应超时，请稍后重试', 0, { code: 'UPSTREAM_TIMEOUT', retryable: true })
  }
  return new ApiError('暂时无法连接后端服务，请确认 FastAPI 已启动在 8001 端口', 0, { code: 'SERVICE_UNAVAILABLE', retryable: true })
}

async function parseResponseError(response, fallback) {
  let detail = fallback
  let errorInfo = {}
  try {
    const payload = await response.json()
    errorInfo = payload?.error || payload || {}
    detail = errorInfo.message || payload?.detail || payload?.message || detail
  } catch {
    // 非 JSON 错误响应使用默认提示
  }
  if (response.status === 401) handleAuthExpired()
  return new ApiError(detail, response.status, {
    code: errorInfo.code,
    retryable: errorInfo.retryable,
    details: errorInfo.details,
    requestId: errorInfo.request_id || response.headers.get('x-request-id'),
  })
}

export async function login(credentials) {
  try {
    const { data } = await publicClient.post('/auth/login', credentials)
    if (!data?.access_token || !data?.user) {
      throw new ApiError('登录响应缺少必要的凭证信息', 502, { code: 'AUTH_RESPONSE_INVALID' })
    }
    return data
  } catch (error) {
    throw normalizeAxiosError(error, '登录失败，请检查账号和密码')
  }
}

export function saveAuth(auth) {
  if (!auth?.access_token || !auth?.user) {
    throw new ApiError('无法保存无效的登录凭证', 0, { code: 'AUTH_RESPONSE_INVALID' })
  }
  const expiresIn = Number(auth.expires_in || 3600)
  if (!Number.isFinite(expiresIn) || expiresIn <= 0) {
    throw new ApiError('登录响应中的凭证有效期无效', 0, { code: 'AUTH_RESPONSE_INVALID' })
  }
  const expiresAt = Date.now() + expiresIn * 1000
  window.localStorage.setItem(TOKEN_KEY, auth.access_token)
  window.localStorage.setItem(USER_KEY, JSON.stringify(auth.user))
  window.localStorage.setItem(EXPIRES_KEY, String(expiresAt))
}

export function clearAuth() {
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(USER_KEY)
  window.localStorage.removeItem(EXPIRES_KEY)
}

export function getStoredAuth() {
  const token = window.localStorage.getItem(TOKEN_KEY)
  const expiresAt = Number(window.localStorage.getItem(EXPIRES_KEY) || 0)
  if (!token || (expiresAt && expiresAt <= Date.now())) {
    clearAuth()
    return null
  }

  let user = null
  try {
    user = JSON.parse(window.localStorage.getItem(USER_KEY) || 'null')
  } catch {
    clearAuth()
    return null
  }
  if (!user || typeof user !== 'object' || !user.user_id || !user.username) {
    clearAuth()
    return null
  }
  return { access_token: token, user, expires_at: expiresAt || null }
}

export function getAccessToken() {
  return getStoredAuth()?.access_token || ''
}

export async function apiFetch(path, options = {}) {
  if (!getAccessToken()) {
    handleAuthExpired()
    throw new ApiError('登录状态已失效，请重新登录', 401, { code: 'AUTH_REQUIRED' })
  }

  const method = (options.method || 'GET').toLowerCase()
  let data = options.body
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data)
    } catch {
      // 保持原始字符串
    }
  }

  try {
    const { data: responseData } = await authClient.request({
      url: path,
      method,
      data,
      headers: options.headers,
    })
    return responseData
  } catch (error) {
    throw normalizeAxiosError(error)
  }
}

export async function listSessions(options = {}) {
  const params = new URLSearchParams()
  if (options.limit) params.set('limit', String(options.limit))
  if (options.before) params.set('before', String(options.before))
  const query = params.toString()
  return apiFetch(`/sessions${query ? `?${query}` : ''}`)
}

export async function createSession() {
  return apiFetch('/sessions', { method: 'POST' })
}

export async function getSessionMessages(sessionId, options = {}) {
  const params = new URLSearchParams()
  if (options.limit) params.set('limit', String(options.limit))
  if (options.beforeId) params.set('before_id', String(options.beforeId))
  const query = params.toString()
  return apiFetch(`/sessions/${encodeURIComponent(sessionId)}/messages${query ? `?${query}` : ''}`)
}

export async function deleteSession(sessionId) {
  return apiFetch(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export async function renameSession(sessionId, title) {
  return apiFetch('/sessions/' + encodeURIComponent(sessionId), {
    method: 'PATCH',
    body: { title },
  })
}

export async function replyToQuestion(sessionId, answer) {
  return apiFetch(`/chat/${encodeURIComponent(sessionId)}/reply`, {
    method: 'POST',
    body: { answer },
  })
}

/**
 * 读取 POST + SSE 流式聊天。
 * 普通接口统一走 Axios；流式接口使用 Fetch 是为了直接消费 ReadableStream。
 */
export async function streamChat({ sessionId, message, signal, onEvent }) {
  if (!getAccessToken()) {
    handleAuthExpired()
    throw new ApiError('登录状态已失效，请重新登录', 401, { code: 'AUTH_REQUIRED' })
  }

  let response
  try {
    response = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      signal,
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${getAccessToken()}`,
      },
      body: JSON.stringify({ session_id: sessionId, message }),
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new ApiError('暂时无法连接后端服务，请确认 FastAPI 已启动在 8001 端口', 0)
  }

  if (!response.ok) {
    throw await parseResponseError(response, '流式对话请求失败')
  }
  if (!response.body) throw new ApiError('后端未返回可读取的流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventName = 'message'
  let dataLines = []

  const dispatchEvent = async () => {
    if (!dataLines.length) return
    const rawData = dataLines.join('\n')
    let data = rawData
    try {
      data = JSON.parse(rawData)
    } catch {
      // 兼容非 JSON 的 SSE data
    }
    await onEvent?.(eventName, data)
    eventName = 'message'
    dataLines = []
  }

  const consumeLines = async (flush = false) => {
    const lines = buffer.split(/\r?\n/)
    if (!flush) buffer = lines.pop() || ''
    else buffer = ''

    for (const line of lines) {
      if (line === '') {
        await dispatchEvent()
      } else if (line.startsWith('event:')) {
        eventName = line.slice(6).trim() || 'message'
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      await consumeLines()
    }
    buffer += decoder.decode()
    if (buffer) {
      await consumeLines(true)
      await dispatchEvent()
    } else {
      await dispatchEvent()
    }
  } finally {
    reader.releaseLock()
  }
}
