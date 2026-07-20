<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import LoginView from './views/LoginView.vue'
import PowerChart from './components/PowerChart.vue'
import AskUserCard from './components/AskUserCard.vue'
import MarkdownMessage from './components/MarkdownMessage.vue'
import {
  clearAuth,
  createSession as createSessionApi,
  deleteSession as deleteSessionApi,
  getSessionMessages,
  getStoredAuth,
  listSessions,
  renameSession as renameSessionApi,
  replyToQuestion,
  streamChat,
} from './api'
import { useRoute, useRouter } from './router'
import { HISTORY_PAGE_SIZE, SESSION_PAGE_SIZE } from './config'

const router = useRouter()
const route = useRoute()
const storedAuth = getStoredAuth()
const currentUser = ref(storedAuth?.user || null)
const sessions = ref([])
const activeSessionId = ref('')
const messages = ref([])
const input = ref('')
const isStreaming = ref(false)
const isLoadingSessions = ref(false)
const isLoadingMoreSessions = ref(false)
const hasMoreSessions = ref(false)
const nextSessionCursor = ref(null)
const sessionTotal = ref(0)
const isLoadingMessages = ref(false)
const isLoadingOlderMessages = ref(false)
const hasOlderMessages = ref(false)
const nextBeforeMessageId = ref(null)
const isReplying = ref(false)
const pendingQuestion = ref(null)
const expandedMessageId = ref(null)
const activeView = ref('conversation')
const toast = ref('')
const authExpiredModalVisible = ref(false)
const theme = ref(window.localStorage.getItem('solar-agent-theme') || 'dark')
const messageList = ref(null)
const composerInput = ref(null)
const abortController = ref(null)
const isLoginRoute = computed(() => route.path === '/login')
const activeSession = computed(() => sessions.value.find((session) => session.id === activeSessionId.value) || null)
const conversationTitle = computed(() => activeSession.value?.title || '新会话')
const statusLabel = computed(() => (pendingQuestion.value ? 'Waiting for reply' : isStreaming.value ? 'Processing' : 'Online'))
const displayName = computed(() => currentUser.value?.display_name || currentUser.value?.username || 'User')
let toastTimer = null
let authExpiryTimer = null
let historyRequestId = 0

function updateThemeMeta(nextTheme) {
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', nextTheme === 'light' ? '#F3F2ED' : '#0B1117')
}
updateThemeMeta(theme.value)

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  window.localStorage.setItem('solar-agent-theme', theme.value)
  updateThemeMeta(theme.value)
}

function showToast(text) {
  toast.value = text
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 2400)
}

function errorMessage(error, fallback = '操作失败，请稍后重试') {
  const messages = {
    AUTH_REQUIRED: '登录已失效，请重新登录',
    AUTH_TOKEN_EXPIRED: '登录已过期，请重新登录',
    AUTH_TOKEN_INVALID: '登录凭证无效，请重新登录',
    SESSION_BUSY: '当前会话正在处理请求，请稍后再试',
    SESSION_NOT_FOUND: '会话不存在或已被删除',
    SESSION_FORBIDDEN: '你没有权限访问这个会话',
    DATA_NOT_FOUND: '没有找到符合条件的数据',
    DATA_SOURCE_UNSUPPORTED: '当前数据来源不受支持',
    DATA_SOURCE_COMBINATION_UNSUPPORTED: '当前数据来源组合暂不支持',
    DATA_RANGE_INVALID: '查询日期范围无效',
    DATA_POINT_LIMIT_EXCEEDED: '图表数据量过大，请缩短时间范围或减少站点',
    DATA_SERIES_LIMIT_EXCEEDED: '图表序列数量超出当前能力范围',
    STATION_NOT_FOUND: '没有找到对应的站点',
    CAPABILITY_NOT_ENABLED: '当前图表能力暂未启用',
    CHART_PLAN_INVALID: '图表生成计划不符合当前能力范围',
    DB_UNAVAILABLE: '数据服务暂时不可用，请稍后重试',
    SERVICE_UNAVAILABLE: '服务暂时不可用，请稍后重试',
    UPSTREAM_TIMEOUT: '外部服务响应超时，请稍后重试',
  }
  return messages[error?.code] || error?.message || fallback
}

function formatTime(value) {
  if (!value) return 'Just now'
  const normalized = String(value).includes(' ') ? String(value).replace(' ', 'T') : String(value)
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function normalizeChartData(chartData) {
  if (typeof chartData === 'string') {
    try { chartData = JSON.parse(chartData) } catch { return null }
  }
  if (!chartData || typeof chartData !== 'object') return null
  const xAxis = chartData.x_axis || chartData.xAxis
  const series = Array.isArray(chartData.series) ? chartData.series : []
  if (!Array.isArray(xAxis?.data) || !series.length) return null
  return { ...chartData, x_axis: xAxis, series, metadata: chartData.metadata || {} }
}

function chartLabelWithDate(label, dateHint) {
  const value = String(label)
  if (/^\d{1,2}:\d{2}$/.test(value) && dateHint) {
    const [hour, minute] = value.split(':')
    return `${dateHint} ${hour.padStart(2, '0')}:${minute}`
  }
  return value
}

function mergeChartData(currentChart, incomingChart) {
  const current = normalizeChartData(currentChart)
  const incoming = normalizeChartData(incomingChart)
  if (!current) return incoming
  if (!incoming) return current

  const currentType = current.metadata?.data_type || 'actual'
  const incomingType = incoming.metadata?.data_type || currentType
  const currentStation = current.metadata?.station_full_name || current.metadata?.station
  const incomingStation = incoming.metadata?.station_full_name || incoming.metadata?.station
  if (currentType !== incomingType || (currentStation && incomingStation && currentStation !== incomingStation)) {
    return incoming
  }

  const labelSet = new Set()
  const seriesMaps = new Map()
  const seriesColors = new Map()
  const charts = [current, incoming]
  for (const chart of charts) {
    const dateHint = chart.metadata?.date
    const labels = chart.x_axis.data.map((label) => chartLabelWithDate(label, dateHint))
    labels.forEach((label) => labelSet.add(label))
    chart.series.forEach((series) => {
      const name = series.name || `series-${seriesMaps.size + 1}`
      if (!seriesMaps.has(name)) seriesMaps.set(name, new Map())
      seriesColors.set(name, series.color || seriesColors.get(name) || 'var(--chart-accent)')
      labels.forEach((label, index) => {
        const value = series.data?.[index]
        if (value !== undefined && value !== null) seriesMaps.get(name).set(label, Number(value) || 0)
      })
    })
  }

  const labels = Array.from(labelSet).sort()
  const series = Array.from(seriesMaps.entries()).map(([name, values]) => ({
    name,
    color: seriesColors.get(name),
    data: labels.map((label) => values.has(label) ? values.get(label) : null),
  }))
  const metadata = { ...current.metadata, ...incoming.metadata }
  const rangeStart = labels[0]?.slice(0, 10) || metadata.start_date || metadata.date
  const rangeEnd = labels[labels.length - 1]?.slice(0, 10) || metadata.end_date || metadata.date
  const granularity = metadata.granularity || (labels.some((label) => label.includes(':')) ? 'hourly' : 'daily')
  metadata.start_date = rangeStart
  metadata.end_date = rangeEnd
  metadata.range_start = rangeStart
  metadata.range_end = rangeEnd
  metadata.date = rangeStart === rangeEnd ? rangeStart : null
  metadata.granularity = granularity
  metadata.point_count = labels.length
  metadata.day_count = rangeStart && rangeEnd ? Math.round((new Date(rangeEnd) - new Date(rangeStart)) / 86400000) + 1 : undefined

  return {
    ...incoming,
    title: `${metadata.station || metadata.station_full_name || '站点'} ${rangeStart || ''}${rangeEnd && rangeEnd !== rangeStart ? ` 至 ${rangeEnd}` : ''}${granularity === 'hourly' ? '逐小时' : '按日'}发电量`,
    x_axis: { ...incoming.x_axis, data: labels, label: granularity === 'hourly' ? '时间（时）' : '日期（日）' },
    series,
    metadata,
  }
}

function buildSessionTitle(value) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim()
  return Array.from(normalized).slice(0, 10).join('') || '新会话'
}

function sessionTitleFromMessages(items) {
  const firstUserMessage = (items || []).find((item) => item.role === 'user')
  return buildSessionTitle(firstUserMessage?.content)
}

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

function mapSession(item) {
  const serverTitle = String(item.title || item.name || '').trim()
  return {
    id: item.session_id,
    title: serverTitle || '新会话',
    titleFromServer: Boolean(serverTitle && serverTitle !== '新会话'),
    time: formatTime(item.last_message_at),
    lastMessageAt: item.last_message_at || null,
    active: false,
  }
}

function mapMessage(item, index) {
  const historicalCharts = Array.isArray(item.charts)
    ? item.charts.map(normalizeChartData).filter(Boolean)
    : []
  const legacyChart = normalizeChartData(item.chart_data || item.chartData)
  const chartData = historicalCharts.length
    ? historicalCharts.reduce((current, chart) => mergeChartData(current, chart), null)
    : legacyChart
  return {
    id: item.id || `history-${item.created_at || index}-${index}`,
    role: item.role === 'user' ? 'user' : 'assistant',
    content: item.content || '',
    time: formatTime(item.created_at),
    status: 'complete',
    chartData,
    charts: historicalCharts,
    toolEvents: Array.isArray(item.tool_events) ? item.tool_events : [],
  }
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
      // Keep historical chart snapshots available for later restoration improvements.
      messages.value = []
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
  markActiveSession(session.id)
  messages.value = []
  hasOlderMessages.value = false
  nextBeforeMessageId.value = null
  isLoadingMessages.value = true
  const requestId = ++historyRequestId
  try {
    const data = await getSessionMessages(session.id, { limit: HISTORY_PAGE_SIZE })
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
  const list = messageList.value
  const previousHeight = list?.scrollHeight || 0
  const previousTop = list?.scrollTop || 0
  isLoadingOlderMessages.value = true
  const requestId = historyRequestId
  try {
    const data = await getSessionMessages(sessionId, {
      limit: HISTORY_PAGE_SIZE,
      beforeId: nextBeforeMessageId.value,
    })
    if (requestId !== historyRequestId || activeSessionId.value !== sessionId) return
    const olderMessages = (data.messages || []).map(mapMessage)
    messages.value = [...olderMessages, ...messages.value]
    hasOlderMessages.value = Boolean(data.has_more)
    nextBeforeMessageId.value = data.next_before_id || null
    await nextTick()
    if (list) list.scrollTop = list.scrollHeight - previousHeight + previousTop
  } catch (error) {
    if (requestId === historyRequestId && activeSessionId.value === sessionId) showToast(errorMessage(error, '历史消息加载失败'))
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
  pendingQuestion.value = null
  expandedMessageId.value = null
  activeView.value = 'conversation'
  router.replace('/workspace')
}

async function renameSession(session) {
  if (isStreaming.value) return
  const requestedTitle = window.prompt('重命名会话（最多 10 个字）', session.title)
  if (requestedTitle === null) return
  const title = requestedTitle.trim()
  if (!title) {
    showToast('会话名称不能为空')
    return
  }

  try {
    const data = await renameSessionApi(session.id, Array.from(title).slice(0, 10).join(''))
    session.title = data.title || title
    session.titleFromServer = true
    showToast('会话已重命名')
  } catch (error) {
    showToast(errorMessage(error, '会话重命名失败'))
  }
}

async function removeSession(session) {
  if (isStreaming.value) {
    showToast('当前会话正在处理请求，请稍后再试')
    return
  }
  if (!window.confirm('Delete session ' + session.title + '?')) return
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

function useShortcut(text) {
  input.value = text
  nextTick(() => composerInput.value?.focus())
}

function findMessage(messageId) {
  return messages.value.find((message) => message.id === messageId)
}

function createProcessSteps(messageId) {
  return [{
    id: `${messageId}-step-0`,
    type: 'thinking',
    status: 'active',
    label: '\u6b63\u5728\u7406\u89e3\u4f60\u7684\u4efb\u52a1',
  }]
}

function addProcessStep(message, label, type = 'thinking', status = 'active') {
  if (!message.processSteps) message.processSteps = []
  const lastStep = message.processSteps[message.processSteps.length - 1]
  if (lastStep?.status === 'active' && lastStep.label === label) return lastStep
  const step = {
    id: `${message.id}-step-${message.processSteps.length}`,
    type,
    status,
    label,
  }
  message.processSteps.push(step)
  return step
}

function completeActiveProcessStep(message, status = 'done') {
  const steps = message.processSteps || []
  const activeStep = [...steps].reverse().find((step) => step.status === 'active')
  if (activeStep) activeStep.status = status
  return activeStep
}

function handleAgentToken(message, content) {
  if (!content) return
  message.content += content
  const currentStep = message.processSteps?.[message.processSteps.length - 1]
  if (!currentStep || currentStep.type === 'thinking' || currentStep.type === 'tool-result') {
    completeActiveProcessStep(message)
    addProcessStep(message, '\u6b63\u5728\u7ec4\u7ec7\u56de\u7b54', 'response')
  }
}

function handleAgentStep(message, data) {
  const label = data?.label || data?.stage
  if (!label) return
  completeActiveProcessStep(message)
  addProcessStep(message, String(label).slice(0, 80), data?.type === 'tool' ? 'tool' : 'thinking')
}

async function sendMessage() {
  const content = input.value.trim()
  if (!content || isStreaming.value || pendingQuestion.value) return

  let sessionId
  try {
    sessionId = await ensureActiveSession()
    maybeAssignSessionTitleFromFirstMessage(sessionId, content)
  } catch (error) {
    showToast(errorMessage(error, '新建会话失败'))
    return
  }

  const userMessage = {
    id: `user-${Date.now()}`,
    role: 'user',
    content,
    time: 'Now',
    status: 'complete',
    chartData: null,
    charts: [],
    toolEvents: [],
  }
  const assistantMessageId = 'assistant-' + (Date.now() + 1)
  const assistantMessageData = {
    id: assistantMessageId,
    role: 'assistant',
    content: '',
    time: 'Now',
    status: 'streaming',
    chartData: null,
    charts: [],
    toolEvents: [],
    processSteps: createProcessSteps(assistantMessageId),
  }
  messages.value.push(userMessage, assistantMessageData)
  // 从响应式数组中重新取出助手消息，确保 SSE token 更新能够触发 Vue 重渲染。
  const assistantMessage = messages.value[messages.value.length - 1]
  input.value = ''
  activeView.value = 'conversation'
  isStreaming.value = true
  pendingQuestion.value = null
  abortController.value = new AbortController()
  const runController = abortController.value
  let streamFailed = false

  try {
    await streamChat({
      sessionId,
      message: content,
      signal: runController.signal,
      onEvent: (eventName, data) => {
        if (eventName === 'token') {
          handleAgentToken(assistantMessage, data?.content || '')
        } else if (eventName === 'thinking' || eventName === 'agent_step' || eventName === 'step') {
          handleAgentStep(assistantMessage, data)
        } else if (eventName === 'tool_start') {
          const toolName = data?.name || '\u5de5\u5177'
          completeActiveProcessStep(assistantMessage)
          addProcessStep(assistantMessage, `\u6b63\u5728\u8c03\u7528\u5de5\u5177\uff1a${toolName}`, 'tool')
          assistantMessage.toolEvents.push({ type: 'start', name: toolName })
        } else if (eventName === 'tool_end') {
          const toolName = data?.name || '\u5de5\u5177'
          const toolStep = [...(assistantMessage.processSteps || [])].reverse().find((step) => step.type === 'tool' && step.status === 'active' && step.label.includes(toolName))
          if (toolStep) toolStep.status = 'done'
          addProcessStep(assistantMessage, '\u6b63\u5728\u6574\u7406\u5de5\u5177\u7ed3\u679c', 'tool-result')
          assistantMessage.toolEvents.push({
            type: 'end',
            name: toolName,
            result: data?.result || '',
            resultType: data?.result_type || 'text',
            code: data?.code || '',
            details: data?.error?.details || data?.details || {},
          })
          const chartData = normalizeChartData(data?.chart_data)
          if (chartData) {
            assistantMessage.charts.push(chartData)
            assistantMessage.chartData = mergeChartData(assistantMessage.chartData, chartData)
          }
        } else if (eventName === 'chart_spec') {
          const toolName = data?.name || 'create_chart_plan'
          const toolStep = [...(assistantMessage.processSteps || [])].reverse().find((step) => step.type === 'tool' && step.status === 'active' && step.label.includes(toolName))
          if (toolStep) toolStep.status = 'done'
          addProcessStep(assistantMessage, '图表已生成', 'chart', 'done')
          assistantMessage.toolEvents.push({
            type: 'end',
            name: toolName,
            result: data?.result || '图表已生成',
            resultType: 'chart_spec',
          })
          const chartData = normalizeChartData(data?.chart_spec)
          if (chartData) {
            assistantMessage.charts.push(chartData)
            assistantMessage.chartData = mergeChartData(assistantMessage.chartData, chartData)
          }
        } else if (eventName === 'user_input_required') {
          completeActiveProcessStep(assistantMessage)
          addProcessStep(assistantMessage, '\u7b49\u5f85\u4f60\u7684\u786e\u8ba4', 'waiting')
          assistantMessage.status = 'waiting'
          pendingQuestion.value = {
            messageId: assistantMessage.id,
            question: data?.question || 'Please provide the required business conditions',
            answer: '',
            status: 'waiting',
            submittedAnswer: '',
            error: '',
          }
        } else if (eventName === 'error') {
          streamFailed = true
          completeActiveProcessStep(assistantMessage, 'error')
          addProcessStep(assistantMessage, '\u4efb\u52a1\u6267\u884c\u5931\u8d25', 'error', 'error')
          assistantMessage.status = 'error'
          assistantMessage.errorCode = data?.code || 'CHAT_AGENT_FAILED'
          assistantMessage.content = errorMessage(data, '任务执行失败')
        } else if (eventName === 'done') {
          if (!assistantMessage.content && data?.output) assistantMessage.content = data.output
          if (!streamFailed) {
            completeActiveProcessStep(assistantMessage)
            addProcessStep(assistantMessage, '\u4efb\u52a1\u5b8c\u6210', 'complete', 'done')
            assistantMessage.status = 'complete'
          }
          if (!assistantMessage.content && !assistantMessage.chartData) assistantMessage.content = 'Task completed.'
          pendingQuestion.value = null
        }
        scrollToBottom()
      },
    })
  } catch (error) {
    if (error.name === 'AbortError') {
      completeActiveProcessStep(assistantMessage, 'stopped')
      addProcessStep(assistantMessage, '\u4efb\u52a1\u5df2\u505c\u6b62', 'stopped', 'done')
      assistantMessage.status = 'stopped'
      assistantMessage.content = assistantMessage.content || 'Task stopped.'
    } else if (error?.status === 401) {
      // 401 已由 API 层触发登录失效弹窗，这里不再覆盖成普通流式错误。
      completeActiveProcessStep(assistantMessage, 'error')
      assistantMessage.status = 'error'
      assistantMessage.content = '登录已过期，请重新登录后继续。'
    } else {
      completeActiveProcessStep(assistantMessage, 'error')
      addProcessStep(assistantMessage, '\u6d41\u5f0f\u8fde\u63a5\u5931\u8d25', 'error', 'error')
      assistantMessage.status = 'error'
      assistantMessage.errorCode = error?.code || 'CHAT_STREAM_INTERRUPTED'
      assistantMessage.content = errorMessage(error, '流式连接失败，请稍后重试')
      showToast(errorMessage(error, '流式连接失败，请稍后重试'))
    }
  } finally {
    if (abortController.value === runController) abortController.value = null
    isStreaming.value = false
    pendingQuestion.value = null
    await scrollToBottom()
  }
}

async function submitQuestionReply(answerValue = pendingQuestion.value?.answer) {
  const question = pendingQuestion.value
  const answer = String(answerValue || '').trim()
  if (!question || !answer || !activeSessionId.value || isReplying.value || question.status === 'submitted') return

  question.answer = answer
  question.error = ''
  question.status = 'submitting'
  isReplying.value = true
  try {
    await replyToQuestion(activeSessionId.value, answer)
    const message = findMessage(question.messageId)
    if (message) {
      completeActiveProcessStep(message)
      addProcessStep(message, '已收到你的回复，继续执行任务', 'thinking')
      message.status = 'streaming'
    }
    question.submittedAnswer = answer
    question.status = 'submitted'
    showToast('已收到回复，任务继续执行')
  } catch (error) {
    question.status = 'waiting'
    question.error = errorMessage(error, '回复发送失败，请重试')
    showToast(question.error)
  } finally {
    isReplying.value = false
  }
}

function updatePendingQuestionAnswer(value) {
  if (pendingQuestion.value) pendingQuestion.value.answer = value
}

function stopStreaming() {
  if (!abortController.value) return
  abortController.value.abort()
  isStreaming.value = false
  pendingQuestion.value = null
}

function toggleToolDetails(messageId) {
  expandedMessageId.value = expandedMessageId.value === messageId ? null : messageId
}

function chartDataTypeLabel(value) {
  const labels = {
    actual: '实际数据',
    predicted: '预测数据',
    comparison: '预测与实际对比',
  }
  return labels[String(value || '').toLowerCase()] || '发电量数据'
}

function chartStationLabel(chartData) {
  const metadata = chartData?.metadata || {}
  return metadata.station_full_name || metadata.station || metadata.station_name || '未标注站点'
}

function chartGranularityLabel(chartData) {
  const metadata = chartData?.metadata || {}
  const granularity = String(metadata.granularity || metadata.time_granularity || '').toLowerCase()
  if (granularity === 'hourly') return '逐小时'
  if (granularity === 'daily') return '按日汇总'

  const labels = chartData?.x_axis?.data || []
  return labels.some((label) => /\d{1,2}:\d{2}/.test(String(label))) ? '逐小时' : '按日汇总'
}

function chartRangeLabel(chartData) {
  const metadata = chartData?.metadata || {}
  const start = metadata.range_start || metadata.start_date || metadata.date || '未标注时间'
  const end = metadata.range_end || metadata.end_date
  return end && end !== start ? `${start} 至 ${end}` : String(start)
}

function resultMetrics(chartData) {
  const metadata = chartData?.metadata || {}
  const total = metadata.predicted_total_kwh ?? metadata.actual_total_kwh ?? '-'
  const peak = metadata.predicted_peak_value_kwh ?? metadata.actual_peak_value_kwh ?? '-'
  return [
    { label: '总发电量', value: total, unit: 'kWh' },
    { label: '峰值发电量', value: peak, unit: 'kWh' },
    { label: '数据类型', value: chartDataTypeLabel(metadata.data_type), unit: '' },
  ]
}

function handleAuthenticated(user) {
  currentUser.value = user
  scheduleAuthExpiryCheck()
  showToast('欢迎回来，' + (user.display_name || user.username))
  loadSessions()
}

function handleAuthExpired() {
  stopStreaming()
  currentUser.value = null
  activeSessionId.value = ''
  sessions.value = []
  messages.value = []
  if (!isLoginRoute.value) authExpiredModalVisible.value = true
}

function scheduleAuthExpiryCheck() {
  if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
  authExpiryTimer = null

  const auth = getStoredAuth()
  if (!auth?.expires_at) return
  const delay = Math.max(0, auth.expires_at - Date.now()) + 100
  authExpiryTimer = window.setTimeout(() => {
    authExpiryTimer = null
    if (!getStoredAuth() && !isLoginRoute.value) handleAuthExpired()
  }, delay)
}

function goToLoginAfterAuthExpired() {
  authExpiredModalVisible.value = false
  router.replace('/login')
}

function logout() {
  stopStreaming()
  clearAuth()
  currentUser.value = null
  sessions.value = []
  messages.value = []
  activeSessionId.value = ''
  router.replace('/login')
  showToast('已退出登录')
}

function scrollToBottom() {
  return nextTick(() => {
    if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
  })
}

onMounted(() => {
  window.addEventListener('solar-agent-auth-expired', handleAuthExpired)
  if (currentUser.value && !isLoginRoute.value) {
    scheduleAuthExpiryCheck()
    loadSessions()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('solar-agent-auth-expired', handleAuthExpired)
  if (abortController.value) abortController.value.abort()
  if (toastTimer) window.clearTimeout(toastTimer)
  if (authExpiryTimer) window.clearTimeout(authExpiryTimer)
})
</script>

<template>
  <div class="app-shell" :data-theme="theme">
    <LoginView v-if="isLoginRoute" :theme="theme" @authenticated="handleAuthenticated" />

    <template v-else>
      <header class="topbar">
        <div class="brand-lockup">
          <div class="brand-mark"><span></span><span></span><span></span></div>
          <div>
            <div class="brand-name">SOLARAGENT</div>
            <div class="brand-caption">光伏运营指挥台</div>
          </div>
        </div>
        <div class="topbar-center">自然语言驱动的光伏任务工作台</div>
        <div class="user-menu">
          <span class="status-dot"></span><span>{{ statusLabel }}</span>
          <button class="theme-toggle" type="button" :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'" :aria-pressed="theme === 'light'" @click="toggleTheme">
            <span class="theme-toggle-mark" aria-hidden="true"></span><span>{{ theme === 'dark' ? '浅色' : '深色' }}</span>
          </button>
          <span class="user-divider"></span><span class="user-name">{{ displayName }}</span>
          <button class="icon-button" type="button" aria-label="更多设置" @click="showToast('设置功能将在后续版本开放')">···</button>
        </div>
      </header>

      <div class="workspace">
        <aside class="sidebar">
          <div class="sidebar-heading"><span>会话</span><span class="session-count">{{ sessionTotal || sessions.length }}</span></div>
          <button class="new-session" type="button" @click="createSession"><span class="plus"></span><span>回到首页</span></button>
          <div class="session-list">
            <div v-if="isLoadingSessions" class="session-empty">正在加载会话…</div>
            <div v-else-if="sessions.length === 0" class="session-empty">还没有会话</div>
            <button v-for="session in sessions" :key="session.id" class="session-item" :class="{ active: session.active }" type="button" @click="selectSession(session)">
              <span class="session-signal"></span>
              <span class="session-copy"><span class="session-title">{{ session.title }}</span><span class="session-time">{{ session.time }}</span></span>
              <span class="session-actions"><span class="session-rename" role="button" tabindex="0" title="重命名会话" @click.stop="renameSession(session)" @keydown.enter.stop="renameSession(session)">✎</span><span class="session-more" title="删除会话" @click.stop="removeSession(session)">···</span></span>
            </button>
            <button v-if="!isLoadingSessions && hasMoreSessions" class="load-more-sessions" type="button" :disabled="isLoadingMoreSessions" @click="loadSessions({ append: true })">
              {{ isLoadingMoreSessions ? '正在加载…' : '加载更多会话' }}
            </button>
          </div>
          <div class="sidebar-section-title">最近结果</div>
          <button class="result-link" type="button" @click="showToast('结果历史将在后续版本接入')"><span class="result-icon chart-icon"></span><span>英杰站预测曲线</span><span class="result-arrow">↗</span></button>
          <button class="result-link" type="button" @click="showToast('结果历史将在后续版本接入')"><span class="result-icon report-icon"></span><span>哲丰站五月报告</span><span class="result-arrow">↗</span></button>
          <div class="sidebar-footer"><div class="footer-line"><span class="footer-dot"></span>数据服务正常</div><button class="logout-button" type="button" @click="logout">退出登录</button></div>
        </aside>

        <main class="main-panel">
          <section v-if="!activeSessionId" class="welcome-panel">
            <div class="sun-orbit orbit-one"></div><div class="sun-orbit orbit-two"></div>
            <div class="welcome-copy">
              <p class="eyebrow">欢迎进入光伏运营工作台</p>
              <h1 class="welcome-title"><span class="welcome-title-line welcome-title-left">把一个光伏问题，</span><span class="welcome-title-line welcome-title-right"><span class="inline-solar">交给阳光</span>和数据。</span></h1>
              <p class="welcome-description">查询、预测、导入、复盘。用一句自然语言开始一项真实的运营任务。</p>
            </div>
            <div class="shortcut-grid">
              <button class="shortcut-card" type="button" @click="useShortcut('查询英杰站今天的实际发电量')"><span class="shortcut-kicker">实际数据</span><strong>查询发电量</strong><span>按站点和日期获取真实记录</span><span class="shortcut-arrow">↗</span></button>
              <button class="shortcut-card accent" type="button" @click="useShortcut('预测英杰站明天的发电量')"><span class="shortcut-kicker">预测任务</span><strong>预测未来发电</strong><span>确认条件后运行预测模型</span><span class="shortcut-arrow">↗</span></button>
              <button class="shortcut-card" type="button" @click="useShortcut('导入这份发电量数据文件')"><span class="shortcut-kicker">数据资产</span><strong>导入数据文件</strong><span>预览、清洗并确认入库</span><span class="shortcut-arrow">↗</span></button>
            </div>
          </section>

          <section v-else class="conversation-view">
            <div class="conversation-header"><h2>{{ conversationTitle }}</h2><div class="view-switcher"><button type="button" :class="{ selected: activeView === 'conversation' }" @click="activeView = 'conversation'">对话</button><button type="button" :class="{ selected: activeView === 'details' }" @click="activeView = 'details'">任务详情</button></div></div>
            <div ref="messageList" class="message-list">
              <button v-if="!isLoadingMessages && hasOlderMessages" class="load-older-button" type="button" :disabled="isLoadingOlderMessages" @click="loadOlderMessages">{{ isLoadingOlderMessages ? '正在加载更早消息…' : '加载更早消息' }}</button>
              <div v-if="isLoadingMessages" class="message-loading">正在加载历史消息…</div>
              <div v-else-if="messages.length === 0" class="message-empty">这是一个新的会话，输入任务开始吧。</div>
              <article v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
                <div v-if="message.role === 'assistant'" class="assistant-avatar">S</div>
                <div class="message-body">
                  <div class="message-meta"><span>{{ message.role === 'assistant' ? 'SolarAgent' : '你' }}</span><span>{{ message.time }}</span></div>
                  <div class="message-card" :class="{ 'result-card': message.status === 'complete' && message.chartData, 'message-error': message.status === 'error' }">
                    <MarkdownMessage v-if="message.content" :content="message.content" :streaming="message.status === 'streaming'" />
                    <p v-else-if="message.status === 'streaming'" class="message-placeholder">{{ message.processSteps && message.processSteps.length ? message.processSteps[message.processSteps.length - 1].label : '正在理解你的任务…' }}</p>
                    <AskUserCard
                      v-if="pendingQuestion?.messageId === message.id && (message.status === 'waiting' || pendingQuestion.status === 'submitting' || pendingQuestion.status === 'submitted')"
                      :question="pendingQuestion.question"
                      :answer="pendingQuestion.answer"
                      :status="pendingQuestion.status"
                      :submitted-answer="pendingQuestion.submittedAnswer"
                      :error="pendingQuestion.error"
                      @update:answer="updatePendingQuestionAnswer"
                      @submit="submitQuestionReply"
                    />
                    <div v-if="message.processSteps && message.processSteps.length && (message.status === 'streaming' || message.status === 'waiting' || message.status === 'stopped' || message.status === 'error')" class="execution-track"><div v-for="step in message.processSteps" :key="step.id" class="execution-item" :class="[step.status, step.type]"><span></span>{{ step.label }}</div></div>
                    <div v-if="message.status === 'complete' && message.chartData" class="prediction-result">
                      <div class="result-topline"><span>发电分析结果</span><span>{{ chartDataTypeLabel(message.chartData.metadata?.data_type) }}</span></div>
                      <div class="result-context">
                        <div><span>站点信息</span><strong>{{ chartStationLabel(message.chartData) }}</strong></div>
                        <div><span>时间跨度</span><strong>{{ chartRangeLabel(message.chartData) }}</strong></div>
                        <div><span>统计粒度</span><strong>{{ chartGranularityLabel(message.chartData) }}</strong></div>
                      </div>
                      <div class="metric-row"><div v-for="metric in resultMetrics(message.chartData)" :key="metric.label"><span>{{ metric.label }}</span><strong>{{ metric.value }} <em v-if="metric.unit">{{ metric.unit }}</em></strong></div></div>
                      <PowerChart class="chart-shell" :chart-data="message.chartData" :theme="theme" />
                      <div class="result-actions"><button class="text-button" type="button" @click="toggleToolDetails(message.id)">{{ expandedMessageId === message.id ? '收起执行详情' : '查看执行详情' }} ↗</button><button class="export-button" type="button" @click="showToast('导出功能将在后续版本接入')">导出预测结果</button></div>
                      <div v-if="expandedMessageId === message.id" class="tool-details"><div v-for="(event, index) in message.toolEvents" :key="message.id + '-detail-' + index">{{ event.name }} · {{ event.result || (event.type === 'start' ? '执行中' : '完成') }}</div><div v-if="!message.toolEvents.length">本次任务没有额外工具事件</div></div>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <div class="composer-wrap">
            <div class="composer"><button class="composer-add" type="button" aria-label="添加文件" @click="showToast('文件导入将在后续版本接入')">+</button><input ref="composerInput" v-model="input" type="text" placeholder="告诉我你想完成的光伏任务" :disabled="isStreaming || Boolean(pendingQuestion)" @keydown.enter="sendMessage" /><button v-if="isStreaming" class="stop-button" type="button" @click="stopStreaming">停止</button><button v-else class="send-button" type="button" aria-label="发送消息" @click="sendMessage">↗</button></div>
            <div class="composer-foot"><span>SolarAgent 可能需要你确认关键业务条件</span><span>Enter 发送</span></div>
          </div>
        </main>
      </div>
    </template>
    <div v-if="toast" class="toast">{{ toast }}</div>
    <div v-if="authExpiredModalVisible" class="auth-expired-backdrop" role="presentation">
      <section class="auth-expired-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-expired-title">
        <div class="auth-expired-icon" aria-hidden="true">!</div>
        <p class="auth-expired-kicker">登录状态</p>
        <h2 id="auth-expired-title">登录已过期</h2>
        <p class="auth-expired-message">为了保护你的账号和会话数据，请重新登录后继续使用。</p>
        <button class="auth-expired-confirm" type="button" @click="goToLoginAfterAuthExpired">重新登录</button>
      </section>
    </div>
  </div>
</template>
