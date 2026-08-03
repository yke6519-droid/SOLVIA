import axios from 'axios'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
const TOKEN_KEY = 'solar-agent-access-token'
const USER_KEY = 'solar-agent-user'
const EXPIRES_KEY = 'solar-agent-token-expires-at'
const TOKEN_LIFETIME_KEY = 'solar-agent-token-lifetime-ms'
export const AUTH_REFRESH_LEAD_MS = 60 * 1000
let refreshPromise = null

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
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

const authClient = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
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
  async (error) => {
    const originalRequest = error.config || {}
    const canRetry = error.response?.status === 401 && !originalRequest._authRetried
    if (!canRetry) return Promise.reject(error)

    originalRequest._authRetried = true
    try {
      await refreshAccessToken()
      originalRequest.headers = originalRequest.headers || {}
      originalRequest.headers.Authorization = `Bearer ${getAccessToken()}`
      return authClient.request(originalRequest)
    } catch (refreshError) {
      handleAuthExpired()
      return Promise.reject(refreshError)
    }
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
  return new ApiError(detail, response.status, {
    code: errorInfo.code,
    retryable: errorInfo.retryable,
    details: errorInfo.details,
    requestId: errorInfo.request_id || response.headers.get('x-request-id'),
  })
}

/**
 * 解析 Axios 的二进制错误响应。
 *
 * 文件下载正常时返回 Blob；但认证失败时，后端仍会返回 JSON 错误体。
 * Axios 在 responseType=blob 下会把这个 JSON 也包装成 Blob，因此不能
 * 直接读取 error.response.data.error，必须先调用 Blob.text() 再解析 JSON。
 */
async function normalizeBinaryResponseError(error, fallback = '请求失败，请稍后重试') {
  const responseData = error?.response?.data
  if (typeof Blob !== 'undefined' && responseData instanceof Blob) {
    try {
      const rawText = await responseData.text()
      const payload = JSON.parse(rawText)
      const errorInfo = payload?.error || payload || {}
      return new ApiError(
        errorInfo.message || payload?.detail || payload?.message || fallback,
        error.response.status,
        {
          code: errorInfo.code,
          retryable: errorInfo.retryable,
          details: errorInfo.details,
          requestId: errorInfo.request_id || error.response.headers?.['x-request-id'],
        },
      )
    } catch {
      // 如果响应不是 JSON（例如网关返回的纯文本），继续使用统一兜底错误。
    }
  }
  return normalizeAxiosError(error, fallback)
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
  window.localStorage.setItem(TOKEN_LIFETIME_KEY, String(expiresIn * 1000))
}

export function clearAuth() {
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(USER_KEY)
  window.localStorage.removeItem(EXPIRES_KEY)
  window.localStorage.removeItem(TOKEN_LIFETIME_KEY)
}

export function getStoredAuth(options = {}) {
  const allowExpired = Boolean(options.allowExpired)
  const token = window.localStorage.getItem(TOKEN_KEY)
  const expiresAt = Number(window.localStorage.getItem(EXPIRES_KEY) || 0)
  const lifetimeMs = Number(window.localStorage.getItem(TOKEN_LIFETIME_KEY) || 0)
  if (!token || (!allowExpired && expiresAt && expiresAt <= Date.now())) {
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
  return {
    access_token: token,
    user,
    expires_at: expiresAt || null,
    lifetime_ms: Number.isFinite(lifetimeMs) && lifetimeMs > 0 ? lifetimeMs : null,
    is_expired: Boolean(expiresAt && expiresAt <= Date.now()),
  }
}

export function getAccessToken() {
  return getStoredAuth()?.access_token || ''
}

export function getAuthRefreshLeadMs(auth = getStoredAuth({ allowExpired: true })) {
  if (!auth?.expires_at) return AUTH_REFRESH_LEAD_MS
  const remaining = Math.max(0, auth.expires_at - Date.now())
  const lifetime = auth.lifetime_ms || Math.max(remaining * 2, 1000)
  return Math.min(AUTH_REFRESH_LEAD_MS, Math.max(1000, Math.floor(lifetime * 0.2)))
}

function shouldRefreshAccessToken() {
  const auth = getStoredAuth({ allowExpired: true })
  if (!auth?.expires_at) return false
  return auth.expires_at - Date.now() <= getAuthRefreshLeadMs(auth)
}

export async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    try {
      const { data } = await publicClient.post('/auth/refresh', {})
      if (!data?.access_token || !data?.user) {
        throw new ApiError('续期响应缺少必要的凭证信息', 502, { code: 'AUTH_RESPONSE_INVALID' })
      }
      saveAuth(data)
      window.dispatchEvent(new CustomEvent('solar-agent-auth-refreshed', { detail: data.user }))
      return data
    } catch (error) {
      const normalized = normalizeAxiosError(error, '登录状态续期失败，请重新登录')
      handleAuthExpired()
      throw normalized
    } finally {
      refreshPromise = null
    }
  })()

  return refreshPromise
}

export async function ensureFreshAccessToken() {
  const auth = getStoredAuth({ allowExpired: true })
  if (!auth?.access_token) {
    handleAuthExpired()
    throw new ApiError('登录状态已失效，请重新登录', 401, { code: 'AUTH_REQUIRED' })
  }
  if (shouldRefreshAccessToken()) return refreshAccessToken()
  return auth
}

export async function logout() {
  try {
    await publicClient.post('/auth/logout', {})
  } catch (error) {
    // The local state must still be cleared even if the server is unreachable.
    throw normalizeAxiosError(error, '退出登录请求失败')
  }
}

export async function apiFetch(path, options = {}) {
  await ensureFreshAccessToken()

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

async function uploadPowerImport(path, file, skipClean = false, previewHash = '') {
  await ensureFreshAccessToken()
  const formData = new FormData()
  formData.append('file', file, file.name)
  formData.append('skip_clean', String(Boolean(skipClean)))
  if (previewHash) formData.append('preview_hash', previewHash)

  try {
    const { data } = await authClient.post(path, formData, {
      // 浏览器会自动补全 multipart boundary，不要手动拼接 boundary。
      headers: { 'Content-Type': 'multipart/form-data' },
      // Excel 预览/入库可能需要较长时间，只放宽导入接口，不影响普通接口。
      timeout: 180000,
    })
    return data
  } catch (error) {
    throw normalizeAxiosError(error, '文件导入请求失败')
  }
}

export function previewPowerImport(file, options = {}) {
  return uploadPowerImport('/import/power/preview', file, options.skipClean, '')
}

export function executePowerImport(file, options = {}) {
  return uploadPowerImport(
    '/import/power/execute',
    file,
    options.skipClean,
    options.previewHash,
  )
}

export async function uploadAttachment(sessionId, file) {
  await ensureFreshAccessToken()
  const formData = new FormData()
  formData.append('session_id', sessionId)
  formData.append('file', file, file.name)
  try {
    const { data } = await authClient.post('/attachments', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    return data
  } catch (error) {
    throw normalizeAxiosError(error, '附件上传失败')
  }
}

/**
 * 下载 Agent 生成的文件。
 * 使用 Axios 获取 Blob，是因为浏览器直接打开链接不会自动携带
 * localStorage 中的 Bearer Token；下载请求仍然复用统一认证拦截器。
 */
export async function downloadGeneratedFile(fileId, filename = '下载文件') {
  // 使用本次检查返回的最新凭证，避免刷新竞态导致请求头仍拿到旧 Token。
  const auth = await ensureFreshAccessToken()
  try {
    const { data } = await authClient.get(
      `/files/${encodeURIComponent(fileId)}/download`,
      {
        responseType: 'blob',
        timeout: 120000,
        headers: { Authorization: `Bearer ${auth.access_token}` },
      },
    )
    const objectUrl = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename || '下载文件'
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  } catch (error) {
    throw await normalizeBinaryResponseError(error, '文件下载失败，请稍后重试')
  }
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
export async function streamChat({ sessionId, message, attachments = [], signal, onEvent }) {
  const openStream = async () => fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      signal,
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${getAccessToken()}`,
      },
      body: JSON.stringify({ session_id: sessionId, message, attachments }),
    })

  let response
  try {
    await ensureFreshAccessToken()
    response = await openStream()
  } catch (error) {
    if (error.name === 'AbortError') throw error
    if (error instanceof ApiError) throw error
    throw new ApiError('暂时无法连接后端服务，请确认 FastAPI 已启动在 8001 端口', 0)
  }

  if (response.status === 401) {
    try {
      await refreshAccessToken()
      response = await openStream()
    } catch (error) {
      if (error.name === 'AbortError') throw error
      if (error instanceof ApiError) throw error
      throw new ApiError('登录状态续期失败，请重新登录', 401, { code: 'AUTH_REQUIRED' })
    }
  }
  if (!response.ok) {
    const error = await parseResponseError(response, '流式对话请求失败')
    if (error.status === 401) handleAuthExpired()
    throw error
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
