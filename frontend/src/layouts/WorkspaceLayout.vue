<script setup>
import AppHeader from '../components/layout/AppHeader.vue'
import SessionSidebar from '../components/layout/SessionSidebar.vue'

defineProps({
  statusLabel: String,
  displayName: String,
  theme: String,
  sessions: {
    type: Array,
    default: () => [],
  },
  sessionTotal: Number,
  isLoadingSessions: Boolean,
  isLoadingMoreSessions: Boolean,
  hasMoreSessions: Boolean,
})

defineEmits([
  'import-data',
  'toggle-theme',
  'open-settings',
  'go-home',
  'select-session',
  'rename-session',
  'remove-session',
  'load-more',
  'show-result-history',
  'logout',
])
</script>

<template>
  <!--
    工作台外壳保留原有 CSS Grid。
    不使用 a-layout 嵌套，避免其默认 flex、背景色和高度规则破坏既定原型。
  -->
  <div class="workspace-layout">
    <AppHeader
      :status-label="statusLabel"
      :display-name="displayName"
      :theme="theme"
      @import-data="$emit('import-data')"
      @toggle-theme="$emit('toggle-theme')"
      @open-settings="$emit('open-settings')"
    />
    <div class="workspace">
      <SessionSidebar
        :sessions="sessions"
        :session-total="sessionTotal"
        :is-loading-sessions="isLoadingSessions"
        :is-loading-more-sessions="isLoadingMoreSessions"
        :has-more-sessions="hasMoreSessions"
        @go-home="$emit('go-home')"
        @select-session="$emit('select-session', $event)"
        @rename-session="$emit('rename-session', $event)"
        @remove-session="$emit('remove-session', $event)"
        @load-more="$emit('load-more')"
        @show-result-history="$emit('show-result-history')"
        @logout="$emit('logout')"
      />
      <slot />
    </div>
  </div>
</template>

<style scoped>
.workspace-layout {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  width: 100%;
  background: transparent;
  overflow: hidden;
}

.workspace {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
}
</style>
