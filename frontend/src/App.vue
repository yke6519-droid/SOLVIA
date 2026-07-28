<script setup>
import { computed, nextTick, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { message as antMessage } from 'ant-design-vue'
import AppProvider from './app/AppProvider.vue'
import WorkspaceLayout from './layouts/WorkspaceLayout.vue'
import LoginView from './views/LoginView.vue'
import WelcomePanel from './views/workspace/WelcomePanel.vue'
import ConversationHeader from './components/conversation/ConversationHeader.vue'
import ConversationMessageList from './components/conversation/ConversationMessageList.vue'
import ConversationComposer from './components/conversation/ConversationComposer.vue'
import RenameSessionDialog from './components/conversation/RenameSessionDialog.vue'
import ImportDataDialog from './components/ImportDataDialog.vue'
import { useRoute, useRouter } from './router'
import { useAuthStore } from './stores/auth'
import { useSessionStore } from './stores/session'
import { useAuthLifecycle } from './composables/useAuthLifecycle'
import { useAttachmentWorkflow } from './composables/useAttachmentWorkflow'
import { useChatStream } from './composables/useChatStream'
import { useImportWorkflow } from './composables/useImportWorkflow'
import { useSessionHistory } from './composables/useSessionHistory'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const sessionStore = useSessionStore()
const { currentUser, authExpiredModalVisible } = storeToRefs(authStore)
const {
  sessions,
  activeSessionId,
  isLoadingSessions,
  isLoadingMoreSessions,
  hasMoreSessions,
  nextSessionCursor,
  sessionTotal,
} = storeToRefs(sessionStore)
const messages = ref([])
const input = ref('')
const isStreaming = ref(false)
const pendingQuestion = ref(null)
const expandedMessageId = ref(null)
const activeView = ref('conversation')
const conversationComposer = ref(null)
const renameDialogVisible = ref(false)
const renameTargetSession = ref(null)
const renameDialogLoading = ref(false)
const pendingAttachments = ref([])
const attachmentUploading = ref(false)
const attachmentError = ref('')
const theme = ref(window.localStorage.getItem('solar-agent-theme') || 'dark')
const messageList = ref(null)
const isLoginRoute = computed(() => route.path === '/login')
const activeSession = computed(() => sessions.value.find((session) => session.id === activeSessionId.value) || null)
const conversationTitle = computed(() => activeSession.value?.title || '新会话')
// 顶部状态只展示面向用户的中文，不把内部英文状态直接暴露到界面。
const statusLabel = computed(() => (pendingQuestion.value ? '等待回复' : isStreaming.value ? '处理中' : '在线'))
const displayName = computed(() => currentUser.value?.display_name || currentUser.value?.username || 'User')

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
  antMessage.info({
    content: String(text || ''),
    duration: 2.4,
  })
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

const {
  importDialogVisible,
  importFile,
  importFilename,
  importPreview,
  importLoading,
  importExecuting,
  importError,
  openImportDialog,
  closeImportDialog,
  handleImportFile,
  confirmImport,
} = useImportWorkflow({
  isStreaming,
  pendingQuestion,
  showToast,
  errorMessage,
})

const {
  isLoadingMessages,
  isLoadingOlderMessages,
  hasOlderMessages,
  nextBeforeMessageId,
  loadSessions,
  selectSession,
  loadOlderMessages,
  createSession,
  renameSession,
  removeSession,
  ensureActiveSession,
  maybeAssignSessionTitleFromFirstMessage,
} = useSessionHistory({
  router,
  currentUser,
  sessions,
  activeSessionId,
  isLoadingSessions,
  isLoadingMoreSessions,
  hasMoreSessions,
  nextSessionCursor,
  sessionTotal,
  messages,
  isStreaming,
  pendingQuestion,
  pendingAttachments,
  attachmentError,
  input,
  expandedMessageId,
  activeView,
  messageList,
  showToast,
  errorMessage,
  scrollToBottom,
})

const {
  handleConversationAttachment,
  removeConversationAttachment,
  clearAttachments,
} = useAttachmentWorkflow({
  pendingAttachments,
  attachmentUploading,
  attachmentError,
  ensureActiveSession,
  showToast,
  errorMessage,
})

const {
  sendMessage,
  handleGeneratedFileDownload,
  submitQuestionReply,
  updatePendingQuestionAnswer,
  stopStreaming,
  toggleToolDetails,
} = useChatStream({
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
})

function useShortcut(text) {
  input.value = text
  nextTick(() => conversationComposer.value?.focus())
}

function openRenameSessionDialog(session) {
  if (isStreaming.value) {
    showToast('当前会话正在处理请求，请稍后再试')
    return
  }
  renameTargetSession.value = session
  renameDialogVisible.value = true
}

function closeRenameSessionDialog() {
  if (renameDialogLoading.value) return
  renameDialogVisible.value = false
  renameTargetSession.value = null
}

async function confirmRenameSession(title) {
  if (!renameTargetSession.value || renameDialogLoading.value) return
  renameDialogLoading.value = true
  try {
    const renamed = await renameSession(renameTargetSession.value, title)
    if (renamed) {
      renameDialogVisible.value = false
      renameTargetSession.value = null
    }
  } finally {
    renameDialogLoading.value = false
  }
}

function scrollToBottom() {
  return messageList.value?.scrollToBottom() || nextTick()
}

function scrollToBottomIfFollowing() {
  return messageList.value?.scrollToBottomIfFollowing?.() || nextTick()
}

function resetWorkspaceState() {
  messages.value = []
  clearAttachments()
}

const {
  handleAuthenticated,
  goToLoginAfterAuthExpired,
  logout,
} = useAuthLifecycle({
  router,
  isLoginRoute,
  currentUser,
  authStore,
  sessionStore,
  authExpiredModalVisible,
  showToast,
  loadSessions,
  stopStreaming,
  resetWorkspace: resetWorkspaceState,
})
</script>

<template>
  <AppProvider :theme="theme">
    <div class="app-shell" :data-theme="theme">
      <LoginView v-if="isLoginRoute" :theme="theme" @authenticated="handleAuthenticated" />

      <WorkspaceLayout
        v-else
        :status-label="statusLabel"
        :display-name="displayName"
        :theme="theme"
        :sessions="sessions"
        :session-total="sessionTotal"
        :is-loading-sessions="isLoadingSessions"
        :is-loading-more-sessions="isLoadingMoreSessions"
        :has-more-sessions="hasMoreSessions"
        @import-data="openImportDialog"
        @toggle-theme="toggleTheme"
        @open-settings="showToast('设置功能将在后续版本开放')"
        @go-home="createSession"
        @select-session="selectSession"
        @rename-session="openRenameSessionDialog"
        @remove-session="removeSession"
        @load-more="loadSessions({ append: true })"
        @show-result-history="showToast('结果历史将在后续版本接入')"
        @logout="logout"
      >
        <main class="main-panel">
          <WelcomePanel
            v-if="!activeSessionId"
            @use-shortcut="useShortcut"
            @import-data="openImportDialog"
          />
          <section v-else class="conversation-view">
            <ConversationHeader
              :title="conversationTitle"
              :active-view="activeView"
              @update:active-view="activeView = $event"
            />
            <ConversationMessageList
              ref="messageList"
              :messages="messages"
              :pending-question="pendingQuestion"
              :is-loading-messages="isLoadingMessages"
              :is-loading-older-messages="isLoadingOlderMessages"
              :has-older-messages="hasOlderMessages"
              :expanded-message-id="expandedMessageId"
              :theme="theme"
              @load-older="loadOlderMessages"
              @update-question-answer="updatePendingQuestionAnswer"
              @submit-question="submitQuestionReply"
              @toggle-tool-details="toggleToolDetails"
              @download-file="handleGeneratedFileDownload"
              @notify="showToast"
            />
          </section>

          <ConversationComposer
            ref="conversationComposer"
            v-model="input"
            :attachments="pendingAttachments"
            :uploading="attachmentUploading"
            :error="attachmentError"
            :streaming="isStreaming"
            :waiting-for-reply="Boolean(pendingQuestion)"
            @select-attachment="handleConversationAttachment"
            @remove-attachment="removeConversationAttachment"
            @send="sendMessage"
            @stop="stopStreaming"
          />
        </main>
      </WorkspaceLayout>
      <ImportDataDialog
        :visible="importDialogVisible"
        :filename="importFilename"
        :preview="importPreview"
        :loading="importLoading"
        :executing="importExecuting"
        :error="importError"
        @close="closeImportDialog"
        @select-file="handleImportFile"
        @confirm="confirmImport"
      />
      <RenameSessionDialog
        :open="renameDialogVisible"
        :session="renameTargetSession"
        :loading="renameDialogLoading"
        @close="closeRenameSessionDialog"
        @confirm="confirmRenameSession"
      />
      <a-modal
        :open="authExpiredModalVisible"
        :width="390"
        :footer="null"
        :closable="false"
        :mask-closable="false"
        :get-container="false"
        wrap-class-name="auth-expired-modal"
      >
        <section class="auth-expired-dialog" aria-labelledby="auth-expired-title">
          <div class="auth-expired-icon" aria-hidden="true">!</div>
          <p class="auth-expired-kicker">登录状态</p>
          <h2 id="auth-expired-title">登录已过期</h2>
          <p class="auth-expired-message">为了保护你的账号和会话数据，请重新登录后继续使用。</p>
          <a-button
            class="auth-expired-confirm"
            type="primary"
            size="large"
            block
            @click="goToLoginAfterAuthExpired"
          >
            重新登录
          </a-button>
        </section>
      </a-modal>
    </div>
  </AppProvider>
</template>
