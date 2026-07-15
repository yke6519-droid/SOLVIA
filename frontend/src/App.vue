<script setup>
import { computed, nextTick, ref } from 'vue'

const sessions = ref([
  { id: '预测英杰站明日发电', time: '刚刚', active: true },
  { id: '哲丰一车间五月分析', time: '昨天 16:24', active: false },
  { id: '数据导入检查', time: '7 月 12 日', active: false },
])
const messages = ref([
  { id: 1, role: 'user', content: '预测英杰站明天的发电量', time: '10:42' },
  { id: 2, role: 'assistant', content: '我识别到一个预测任务。为了保证结果准确，请确认下面的执行条件。', time: '10:42', status: 'waiting' },
])
const input = ref('')
const isStreaming = ref(false)
const showConfirmation = ref(true)
const toolDetailsOpen = ref(false)
const activeView = ref('conversation')
const toast = ref('')
const messageList = ref(null)
const statusLabel = computed(() => (isStreaming.value ? '处理中' : '在线'))

function showToast(text) {
  toast.value = text
  setTimeout(() => { toast.value = '' }, 2200)
}
function selectSession(session) {
  sessions.value.forEach((item) => { item.active = item.id === session.id })
  showToast(`已切换到「${session.id}」`)
}
function createSession() {
  sessions.value.forEach((item) => { item.active = false })
  sessions.value.unshift({ id: '未命名光伏任务', time: '刚刚', active: true })
  messages.value = []
  showConfirmation.value = false
  showToast('已创建新会话')
}
function useShortcut(text) {
  input.value = text
  nextTick(sendMessage)
}
function sendMessage() {
  const content = input.value.trim()
  if (!content || isStreaming.value) return
  messages.value.push({ id: Date.now(), role: 'user', content, time: '现在' })
  input.value = ''
  showConfirmation.value = false
  isStreaming.value = true
  setTimeout(() => {
    messages.value.push({ id: Date.now() + 1, role: 'assistant', content: '已收到任务。我会先确认站点和日期，再开始调用业务能力。', time: '现在', status: 'streaming' })
    isStreaming.value = false
    scrollToBottom()
  }, 900)
  scrollToBottom()
}
function confirmPrediction() {
  showConfirmation.value = false
  isStreaming.value = true
  messages.value.push({ id: Date.now(), role: 'assistant', content: '确认收到，正在为英杰站生成 2026-07-16 的预测结果。', time: '现在', status: 'streaming' })
  setTimeout(() => {
    isStreaming.value = false
    messages.value[messages.value.length - 1].status = 'complete'
    showToast('预测结果已生成')
    scrollToBottom()
  }, 1200)
}
function modifyPrediction() {
  showConfirmation.value = false
  input.value = '请把预测日期改为后天'
  showToast('请在输入框中修改预测条件')
}
function scrollToBottom() {
  nextTick(() => { if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight })
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark"><span></span><span></span><span></span></div>
        <div><div class="brand-name">SOLARAGENT</div><div class="brand-caption">光伏运营指挥台</div></div>
      </div>
      <div class="topbar-center">自然语言驱动的光伏任务工作台</div>
      <div class="user-menu"><span class="status-dot"></span><span>{{ statusLabel }}</span><span class="user-divider"></span><span class="user-name">测试用户</span><button class="icon-button" aria-label="打开设置">···</button></div>
    </header>

    <div class="workspace">
      <aside class="sidebar">
        <div class="sidebar-heading"><span>会话</span><span class="session-count">{{ sessions.length }}</span></div>
        <button class="new-session" @click="createSession"><span class="plus">+</span><span>新建会话</span></button>
        <div class="session-list">
          <button v-for="session in sessions" :key="session.id" class="session-item" :class="{ active: session.active }" @click="selectSession(session)">
            <span class="session-signal"></span><span class="session-copy"><span class="session-title">{{ session.id }}</span><span class="session-time">{{ session.time }}</span></span><span class="session-more">···</span>
          </button>
        </div>
        <div class="sidebar-section-title">最近结果</div>
        <button class="result-link" @click="showToast('结果历史将在 API 接入后启用')"><span class="result-icon chart-icon"></span><span>英杰站预测曲线</span><span class="result-arrow">↗</span></button>
        <button class="result-link" @click="showToast('结果历史将在 API 接入后启用')"><span class="result-icon report-icon"></span><span>哲丰站五月报告</span><span class="result-arrow">↗</span></button>
        <div class="sidebar-footer"><div class="footer-line"><span class="footer-dot"></span>数据服务正常</div><button class="logout-button" @click="showToast('退出登录将在 API 接入后启用')">退出登录</button></div>
      </aside>

      <main class="main-panel">
        <section v-if="messages.length === 0" class="welcome-panel">
          <div class="sun-orbit orbit-one"></div><div class="sun-orbit orbit-two"></div>
          <div class="welcome-copy"><p class="eyebrow">欢迎进入光伏运营工作台</p><h1>把一个光伏问题，<span class="inline-solar">交给阳光</span>和数据。</h1><p class="welcome-description">查询、预测、导入、复盘。用一句自然语言开始一项真实的运营任务。</p></div>
          <div class="shortcut-grid">
            <button class="shortcut-card" @click="useShortcut('查询英杰站今天的实际发电量')"><span class="shortcut-kicker">实际数据</span><strong>查询发电量</strong><span>按站点和日期获取真实记录</span><span class="shortcut-arrow">↗</span></button>
            <button class="shortcut-card accent" @click="useShortcut('预测英杰站明天的发电量')"><span class="shortcut-kicker">预测任务</span><strong>预测未来发电</strong><span>确认条件后运行预测模型</span><span class="shortcut-arrow">↗</span></button>
            <button class="shortcut-card" @click="useShortcut('导入这个发电量文件')"><span class="shortcut-kicker">数据资产</span><strong>导入数据文件</strong><span>预览、清洗并确认入库</span><span class="shortcut-arrow">↗</span></button>
          </div>
        </section>

        <section v-else class="conversation-view">
          <div class="conversation-header"><div><p class="eyebrow">当前会话</p><h2>预测英杰站明日发电</h2></div><div class="view-switcher"><button :class="{ selected: activeView === 'conversation' }" @click="activeView = 'conversation'">对话</button><button :class="{ selected: activeView === 'details' }" @click="activeView = 'details'">任务详情</button></div></div>
          <div ref="messageList" class="message-list">
            <article v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
              <div v-if="message.role === 'assistant'" class="assistant-avatar">S</div>
              <div class="message-body"><div class="message-meta"><span>{{ message.role === 'assistant' ? 'SolarAgent' : '你' }}</span><span>{{ message.time }}</span></div>
                <div class="message-card" :class="{ 'result-card': message.status === 'complete' }"><p>{{ message.content }}</p>
                  <div v-if="message.status === 'waiting' && showConfirmation" class="confirmation-card"><div class="confirmation-heading"><span class="confirmation-mark">!</span><div><strong>请确认预测条件</strong><span>确认后才会调用预测模型</span></div></div><div class="condition-grid"><div><span>站点</span><strong>英杰站</strong></div><div><span>日期</span><strong>2026-07-16</strong></div><div><span>动作</span><strong>预测当日发电量</strong></div></div><div class="confirmation-actions"><button class="primary-button" @click="confirmPrediction">确认并开始预测</button><button class="secondary-button" @click="modifyPrediction">修改条件</button></div></div>
                  <div v-if="message.status === 'streaming'" class="execution-track"><div class="execution-item done"><span></span>解析站点和日期</div><div class="execution-item active"><span></span>正在获取气象数据</div><div class="execution-item"><span></span>等待预测模型</div></div>
                  <div v-if="message.status === 'complete'" class="prediction-result"><div class="result-topline"><span>预测完成</span><span>2026-07-16</span></div><div class="metric-row"><div><span>预计总发电量</span><strong>8,426.5 <em>kWh</em></strong></div><div><span>预计峰值</span><strong>512.8 <em>kW</em></strong></div><div><span>天气类型</span><strong>多云</strong></div></div><div class="chart-shell"><div class="chart-grid-lines"><span></span><span></span><span></span><span></span></div><svg viewBox="0 0 600 150" preserveAspectRatio="none" aria-label="逐时预测发电曲线"><defs><linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#65e6cf" stop-opacity="0.35"/><stop offset="100%" stop-color="#65e6cf" stop-opacity="0"/></linearGradient></defs><path d="M0 145 L32 143 L64 138 L96 125 L128 92 L160 52 L192 40 L224 44 L256 58 L288 74 L320 90 L352 102 L384 116 L416 127 L448 136 L480 141 L512 144 L544 145 L576 145 L600 145 L600 150 L0 150 Z" fill="url(#areaFill)"/><path d="M0 145 L32 143 L64 138 L96 125 L128 92 L160 52 L192 40 L224 44 L256 58 L288 74 L320 90 L352 102 L384 116 L416 127 L448 136 L480 141 L512 144 L544 145 L576 145 L600 145" fill="none" stroke="#65e6cf" stroke-width="3"/></svg><div class="chart-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div></div><div class="result-actions"><button class="text-button" @click="toolDetailsOpen = !toolDetailsOpen">{{ toolDetailsOpen ? '收起执行详情' : '查看执行详情' }} ↘</button><button class="export-button" @click="showToast('导出功能将在 API 接入后启用')">导出预测结果</button></div><div v-if="toolDetailsOpen" class="tool-details">get_power_chart_data · chart_type=line · 24 个时段数据</div></div>
                </div>
              </div>
            </article>
          </div>
        </section>

        <div class="composer-wrap"><div class="composer-hint"><span class="hint-pulse"></span>试试：预测英杰站明天的发电量，或查询哲丰一车间五月一日的实际数据</div><div class="composer"><button class="composer-add" aria-label="添加文件" @click="showToast('文件导入将在 API 接入后启用')">+</button><input v-model="input" type="text" placeholder="告诉我你想完成的光伏任务" @keydown.enter="sendMessage"/><button v-if="isStreaming" class="stop-button" @click="isStreaming = false">停止</button><button v-else class="send-button" aria-label="发送消息" @click="sendMessage">↗</button></div><div class="composer-foot"><span>SolarAgent 可能需要你确认关键业务条件</span><span>Enter 发送</span></div></div>
      </main>
    </div>
    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>