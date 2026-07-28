import { ref } from 'vue'
import {
  downloadGeneratedFile,
  replyToQuestion,
  streamChat,
} from '../api'
import {
  mergeChartData,
  normalizeChartData,
  normalizeTokenUsage,
} from '../utils/conversation'

/**
 * 流式对话编排。
 *
 * 这里不负责页面布局，只把 AgentExecutor 的 SSE 事件转换为消息模型：
 * token、工具开始/结束、图表、文件、用户确认、错误和完成状态。
 */
export function useChatStream({
  input,
  messages,
  isStreaming,
  pendingQuestion,
  expandedMessageId,
  activeView,
  pendingAttachments,
  attachmentError,
  activeSessionId,
  ensureActiveSession,
  maybeAssignSessionTitleFromFirstMessage,
  showToast,
  errorMessage,
  scrollToBottom,
  scrollToBottomIfFollowing,
}) {
  const isReplying = ref(false)
  const abortController = ref(null)

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

  function appendChart(message, chart) {
    const chartData = normalizeChartData(chart)
    if (!chartData) return
    message.charts.push(chartData)
    message.chartData = mergeChartData(message.chartData, chartData)
  }

  function handleToolStart(message, data) {
    const toolName = data?.name || '\u5de5\u5177'
    completeActiveProcessStep(message)
    addProcessStep(message, `\u6b63\u5728\u8c03\u7528\u5de5\u5177\uff1a${toolName}`, 'tool')
    message.toolEvents.push({ type: 'start', name: toolName })
  }

  function handleToolEnd(message, data) {
    const toolName = data?.name || '\u5de5\u5177'
    const toolStep = [...(message.processSteps || [])].reverse().find(
      (step) => step.type === 'tool' && step.status === 'active' && step.label.includes(toolName),
    )
    if (toolStep) toolStep.status = 'done'
    addProcessStep(message, '\u6b63\u5728\u6574\u7406\u5de5\u5177\u7ed3\u679c', 'tool-result')
    message.toolEvents.push({
      type: 'end',
      name: toolName,
      result: data?.result || '',
      resultType: data?.result_type || 'text',
      code: data?.code || '',
      details: data?.error?.details || data?.details || {},
    })
    if (data?.result_type === 'file' && data?.file?.file_id) {
      const exists = message.files.some((file) => file.file_id === data.file.file_id)
      if (!exists) message.files.push(data.file)
    }
    appendChart(message, data?.chart_data)
  }

  function handleChartSpec(message, data) {
    const toolName = data?.name || 'create_chart_plan'
    const toolStep = [...(message.processSteps || [])].reverse().find(
      (step) => step.type === 'tool' && step.status === 'active' && step.label.includes(toolName),
    )
    if (toolStep) toolStep.status = 'done'
    addProcessStep(message, '\u56fe\u8868\u5df2\u751f\u6210', 'chart', 'done')
    message.toolEvents.push({
      type: 'end',
      name: toolName,
      result: data?.result || '\u56fe\u8868\u5df2\u751f\u6210',
      resultType: 'chart_spec',
    })
    appendChart(message, data?.chart_spec)
  }

  function handleUserInputRequired(message, data) {
    completeActiveProcessStep(message)
    addProcessStep(message, '\u7b49\u5f85\u4f60\u7684\u786e\u8ba4', 'waiting')
    message.status = 'waiting'
    pendingQuestion.value = {
      messageId: message.id,
      question: data?.question || 'Please provide the required business conditions',
      answer: '',
      status: 'waiting',
      submittedAnswer: '',
      error: '',
    }
  }

  async function sendMessage() {
    const content = input.value.trim()
    if (!content || isStreaming.value || pendingQuestion.value) return

    let sessionId
    try {
      sessionId = await ensureActiveSession()
      maybeAssignSessionTitleFromFirstMessage(sessionId, content)
    } catch (error) {
      showToast(errorMessage(error, '\u65b0\u5efa\u4f1a\u8bdd\u5931\u8d25'))
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
      attachments: pendingAttachments.value.map((attachment) => ({ ...attachment })),
    }
    const messageAttachments = pendingAttachments.value.map(
      (attachment) => ({ attachment_id: attachment.attachment_id }),
    )
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
      files: [],
      tokenUsage: null,
      processSteps: createProcessSteps(assistantMessageId),
    }
    messages.value.push(userMessage, assistantMessageData)
    // 从响应式数组中重新取出助手消息，确保 token 更新能够触发 Vue 重渲染。
    const assistantMessage = messages.value[messages.value.length - 1]
    input.value = ''
    pendingAttachments.value = []
    attachmentError.value = ''
    activeView.value = 'conversation'
    isStreaming.value = true
    pendingQuestion.value = null
    abortController.value = new AbortController()
    const runController = abortController.value
    let streamFailed = false
    // 新任务开始时保证用户刚发送的消息可见；后续事件尊重用户的滚动位置。
    await scrollToBottom()

    try {
      await streamChat({
        sessionId,
        message: content,
        attachments: messageAttachments,
        signal: runController.signal,
        onEvent: (eventName, data) => {
          if (eventName === 'token') {
            handleAgentToken(assistantMessage, data?.content || '')
            if (data?.usage) assistantMessage.tokenUsage = normalizeTokenUsage(data.usage)
          } else if (eventName === 'usage') {
            assistantMessage.tokenUsage = normalizeTokenUsage(data?.usage)
          } else if (eventName === 'thinking' || eventName === 'agent_step' || eventName === 'step') {
            handleAgentStep(assistantMessage, data)
          } else if (eventName === 'tool_start') {
            handleToolStart(assistantMessage, data)
          } else if (eventName === 'tool_end') {
            handleToolEnd(assistantMessage, data)
          } else if (eventName === 'chart_spec') {
            handleChartSpec(assistantMessage, data)
          } else if (eventName === 'user_input_required') {
            handleUserInputRequired(assistantMessage, data)
          } else if (eventName === 'error') {
            streamFailed = true
            completeActiveProcessStep(assistantMessage, 'error')
            addProcessStep(assistantMessage, '\u4efb\u52a1\u6267\u884c\u5931\u8d25', 'error', 'error')
            assistantMessage.status = 'error'
            assistantMessage.errorCode = data?.code || 'CHAT_AGENT_FAILED'
            assistantMessage.content = errorMessage(data, '\u4efb\u52a1\u6267\u884c\u5931\u8d25')
          } else if (eventName === 'done') {
            if (data?.usage) assistantMessage.tokenUsage = normalizeTokenUsage(data.usage)
            if (!assistantMessage.content && data?.output) assistantMessage.content = data.output
            if (!streamFailed) {
              completeActiveProcessStep(assistantMessage)
              addProcessStep(assistantMessage, '\u4efb\u52a1\u5b8c\u6210', 'complete', 'done')
              assistantMessage.status = 'complete'
            }
            if (!assistantMessage.content && !assistantMessage.chartData) assistantMessage.content = 'Task completed.'
            pendingQuestion.value = null
          }
          scrollToBottomIfFollowing()
        },
      })
    } catch (error) {
      if (error?.name === 'AbortError') {
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
      await scrollToBottomIfFollowing()
    }
  }

  async function handleGeneratedFileDownload(file) {
    if (!file?.file_id) return
    try {
      await downloadGeneratedFile(file.file_id, file.filename)
    } catch (error) {
      showToast(errorMessage(error, '文件下载失败，请稍后重试'))
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

  return {
    sendMessage,
    handleGeneratedFileDownload,
    submitQuestionReply,
    updatePendingQuestionAnswer,
    stopStreaming,
    toggleToolDetails,
  }
}
