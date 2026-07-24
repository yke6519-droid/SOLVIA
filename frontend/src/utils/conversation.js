/**
 * 会话数据转换工具。
 *
 * 这里仅放“输入数据 -> 前端展示模型”的纯函数，不直接读取 Pinia、路由或组件状态。
 * 这样历史会话、流式消息和图表快照都可以复用同一套转换逻辑。
 */

/** 将后端时间转换为工作台使用的短日期时间。 */
export function formatTime(value) {
  if (!value) return 'Just now'
  const normalized = String(value).includes(' ') ? String(value).replace(' ', 'T') : String(value)
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 兼容后端不同命名的 Token 用量字段，并统一为前端模型。 */
export function normalizeTokenUsage(value) {
  if (!value || typeof value !== 'object') return null
  const input = Number(value.input_tokens ?? value.prompt_tokens ?? 0)
  const output = Number(value.output_tokens ?? value.completion_tokens ?? 0)
  const total = Number(value.total_tokens ?? input + output)
  if (![input, output, total].some((item) => Number.isFinite(item) && item > 0)) return null
  return {
    input_tokens: Math.max(0, Math.trunc(input || 0)),
    output_tokens: Math.max(0, Math.trunc(output || 0)),
    total_tokens: Math.max(0, Math.trunc(total || 0)),
  }
}

/** 将字符串或旧版字段格式转换为统一图表数据；非法数据直接返回 null。 */
export function normalizeChartData(chartData) {
  if (typeof chartData === 'string') {
    try {
      chartData = JSON.parse(chartData)
    } catch {
      return null
    }
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

/** 合并同一站点、同一数据来源的历史图表快照，避免恢复会话时丢失序列。 */
export function mergeChartData(currentChart, incomingChart) {
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
  for (const chart of [current, incoming]) {
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
  metadata.day_count = rangeStart && rangeEnd
    ? Math.round((new Date(rangeEnd) - new Date(rangeStart)) / 86400000) + 1
    : undefined

  return {
    ...incoming,
    title: `${metadata.station || metadata.station_full_name || '站点'} ${rangeStart || ''}${rangeEnd && rangeEnd !== rangeStart ? ` 至 ${rangeEnd}` : ''}${granularity === 'hourly' ? '逐小时' : '按日'}发电量`,
    x_axis: { ...incoming.x_axis, data: labels, label: granularity === 'hourly' ? '时间（时）' : '日期（日）' },
    series,
    metadata,
  }
}

/** 根据首条用户消息生成不超过 10 个字符的会话标题。 */
export function buildSessionTitle(value) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim()
  return Array.from(normalized).slice(0, 10).join('') || '新会话'
}

export function sessionTitleFromMessages(items) {
  const firstUserMessage = (items || []).find((item) => item.role === 'user')
  return buildSessionTitle(firstUserMessage?.content)
}

/** 将会话列表接口响应映射为侧边栏展示模型。 */
export function mapSession(item) {
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

/** 将历史消息和图表快照映射为消息列表展示模型。 */
export function mapMessage(item, index) {
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
    attachments: Array.isArray(item.attachments) ? item.attachments : [],
    files: Array.isArray(item.files) ? item.files : [],
    tokenUsage: normalizeTokenUsage(item.token_usage || item.tokenUsage || item.usage),
  }
}
