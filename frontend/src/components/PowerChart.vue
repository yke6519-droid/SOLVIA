<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([GridComponent, LegendComponent, TooltipComponent, BarChart, LineChart, PieChart, CanvasRenderer])

const props = defineProps({
  chartData: { type: [Object, String], default: null },
  theme: { type: String, default: 'dark' },
})

const chartElement = ref(null)
const chartInstance = shallowRef(null)
let resizeObserver = null

const darkPalette = {
  text: '#edf6f2',
  muted: '#9aa9a3',
  border: 'rgba(228, 238, 233, .14)',
  grid: 'rgba(228, 238, 233, .10)',
  tooltipBg: '#182326',
  tooltipBorder: 'rgba(101, 230, 207, .34)',
}

const lightPalette = {
  text: '#26342f',
  muted: '#6f7b74',
  border: 'rgba(43, 57, 51, .14)',
  grid: 'rgba(43, 57, 51, .10)',
  tooltipBg: '#fbfaf6',
  tooltipBorder: 'rgba(75, 142, 131, .36)',
}

function normalizeChartData(value) {
  if (typeof value === 'string') {
    try { value = JSON.parse(value) } catch { return null }
  }
  if (!value || typeof value !== 'object') return null
  const xAxis = value.x_axis || value.xAxis
  const series = Array.isArray(value.series) ? value.series : []
  if (!Array.isArray(xAxis?.data) || !series.length) return null
  return { ...value, x_axis: xAxis, series, metadata: value.metadata || {} }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  return numeric.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function shortenAxisLabel(value) {
  const label = String(value ?? '')
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(label)) return label.slice(5)
  if (/^\d{4}-\d{2}-\d{2}$/.test(label)) return label.slice(5)
  return label
}

function chartTypeOf(data) {
  return ['line', 'bar', 'pie'].includes(data?.chart_type) ? data.chart_type : 'line'
}

function localizedSeriesName(name, index) {
  const source = String(name || '').trim()
  const normalized = source.toLowerCase()
  if (normalized.includes('actual')) return '实际发电量'
  if (normalized.includes('predict') || normalized.includes('forecast')) return '预测发电量'
  if (normalized.includes('comparison') || normalized.includes('compare')) return '预测与实际对比'
  if (normalized.includes('generation') || normalized.includes('power')) return '发电量'
  return source || `数据序列 ${index + 1}`
}

function chartUnit(data) {
  const explicitUnit = data.y_axis?.unit || data.metadata?.unit || data.series?.[0]?.unit
  if (explicitUnit) return String(explicitUnit)
  const label = String(data.y_axis?.label || '')
  const matched = label.match(/[（(]([^）)]+)[）)]/)
  if (matched?.[1]) return matched[1]
  return /generation|power|发电量/i.test(label) ? 'kWh' : ''
}

function xAxisName(data, labels) {
  const granularity = String(data.metadata?.granularity || data.metadata?.time_granularity || '').toLowerCase()
  if (granularity === 'hourly' || labels.some((label) => /\d{1,2}:\d{2}/.test(label))) return '时间（时）'
  if (granularity === 'daily' || labels.some((label) => /^\d{4}-\d{2}-\d{2}$/.test(label))) return '日期（日）'
  const label = String(data.x_axis?.label || '').toLowerCase()
  return label.includes('date') ? '日期（日）' : '时间'
}

function yAxisName(data, unit) {
  const rawLabel = String(data.y_axis?.label || '')
  const normalized = rawLabel.toLowerCase()
  const base = /generation|power|发电量/.test(normalized) ? '发电量' : (rawLabel.replace(/[（(][^）)]+[）)]/g, '').trim() || '数值')
  return unit ? `${base}（${unit}）` : base
}

function buildOption() {
  const data = normalizeChartData(props.chartData)
  if (!data) return null

  const palette = props.theme === 'light' ? lightPalette : darkPalette
  const chartType = chartTypeOf(data)
  const labels = data.x_axis.data.map((item) => String(item))
  const unit = chartUnit(data)
  const sourceSeries = data.series.map((series, index) => ({
    ...series,
    name: localizedSeriesName(series.name, index),
    color: series.color && !String(series.color).startsWith('var(')
      ? series.color
      : (index === 0 ? (props.theme === 'light' ? '#4b8e83' : '#65e6cf') : '#e6a04b'),
    data: Array.isArray(series.data) ? series.data : [],
  }))

  if (chartType === 'pie') {
    const firstSeries = sourceSeries[0]
    const pieData = labels.map((name, index) => ({ name, value: firstSeries?.data?.[index] ?? 0 }))
    return {
      animationDuration: 420,
      color: sourceSeries.map((series) => series.color),
      tooltip: {
        trigger: 'item',
        backgroundColor: palette.tooltipBg,
        borderColor: palette.tooltipBorder,
        textStyle: { color: palette.text },
        formatter: (params) => `${escapeHtml(params.name)}<br/>${params.marker}${escapeHtml(firstSeries?.name || '发电量')}：<strong>${formatValue(params.value)} ${escapeHtml(firstSeries?.unit || unit)}</strong>`,
      },
      legend: { bottom: 0, type: 'scroll', textStyle: { color: palette.muted } },
      series: [{
        type: 'pie',
        radius: ['42%', '70%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        label: { color: palette.text },
        data: pieData,
      }],
    }
  }

  return {
    animationDuration: 420,
    color: sourceSeries.map((series) => series.color),
    grid: {
      left: 52,
      right: sourceSeries.length > 1 ? 72 : 18,
      top: sourceSeries.length > 1 ? 38 : 20,
      bottom: labels.length > 12 ? 52 : 40,
      containLabel: true,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        lineStyle: { color: props.theme === 'light' ? '#4b8e83' : '#65e6cf', opacity: 0.5 },
        crossStyle: { color: props.theme === 'light' ? '#4b8e83' : '#65e6cf', opacity: 0.35 },
      },
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: palette.text },
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params]
        const axisLabel = items[0]?.axisValueLabel ?? items[0]?.axisValue ?? ''
        const rows = items
          .filter((item) => item.value !== null && item.value !== undefined)
          .map((item) => {
            const source = sourceSeries[item.seriesIndex] || {}
            const rawValue = Array.isArray(item.value) ? item.value[item.value.length - 1] : item.value
            return `${item.marker}${escapeHtml(item.seriesName)}：<strong>${formatValue(rawValue)} ${escapeHtml(source.unit || unit)}</strong>`
          })
        return `<div style="font-weight:600;margin-bottom:5px">${escapeHtml(axisLabel)}</div>${rows.join('<br/>')}`
      },
    },
    legend: sourceSeries.length > 1 ? {
      top: 0,
      right: 0,
      type: 'scroll',
      textStyle: { color: palette.muted },
    } : undefined,
    xAxis: {
      type: 'category',
      data: labels,
      name: xAxisName(data, labels),
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: palette.muted, fontSize: 11 },
      boundaryGap: chartType === 'bar',
      axisLine: { lineStyle: { color: palette.border } },
      axisTick: { show: false },
      axisLabel: { color: palette.muted, hideOverlap: true, formatter: shortenAxisLabel },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: yAxisName(data, unit),
      nameTextStyle: { color: palette.muted, padding: [0, 0, 0, 10], fontSize: 11 },
      axisLabel: { color: palette.muted },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: sourceSeries.map((series, index) => ({
      name: series.name,
      type: chartType,
      data: series.data,
      smooth: chartType === 'line',
      showSymbol: labels.length <= 48,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: chartType === 'line' ? { width: 2.5 } : undefined,
      areaStyle: chartType === 'line' && index === 0 ? { opacity: 0.12 } : undefined,
      emphasis: { focus: 'series' },
      barMaxWidth: chartType === 'bar' ? 28 : undefined,
    })),
  }
}

function renderChart() {
  if (!chartElement.value) return
  if (!chartInstance.value) chartInstance.value = echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  const option = buildOption()
  if (option) chartInstance.value.setOption(option, true)
  else chartInstance.value.clear()
}

function resizeChart() {
  chartInstance.value?.resize()
}

watch(() => [props.chartData, props.theme], () => nextTick(renderChart), { deep: true })

onMounted(() => {
  nextTick(() => {
    renderChart()
    if (typeof ResizeObserver !== 'undefined' && chartElement.value) {
      resizeObserver = new ResizeObserver(resizeChart)
      resizeObserver.observe(chartElement.value)
    } else {
      window.addEventListener('resize', resizeChart)
    }
  })
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', resizeChart)
  chartInstance.value?.dispose()
  chartInstance.value = null
})
</script>

<template>
  <div ref="chartElement" class="power-chart" aria-label="发电量图表"></div>
</template>
