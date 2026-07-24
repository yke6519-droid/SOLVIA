<script setup>
import {
  BarChartOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  HomeOutlined,
  LogoutOutlined,
} from '@ant-design/icons-vue'

defineProps({
  sessions: {
    type: Array,
    default: () => [],
  },
  sessionTotal: {
    type: Number,
    default: 0,
  },
  isLoadingSessions: Boolean,
  isLoadingMoreSessions: Boolean,
  hasMoreSessions: Boolean,
})

defineEmits([
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
  <!-- 侧栏宽度由工作台 Grid 统一控制，Ant 组件只负责内部交互控件。 -->
  <aside class="sidebar">
    <div class="sidebar-heading">
      <span>会话</span>
      <span class="session-count">{{ sessionTotal || sessions.length }}</span>
    </div>

    <a-button class="new-session" type="primary" block @click="$emit('go-home')">
      <template #icon><HomeOutlined /></template>
      回到首页
    </a-button>

    <div class="session-list">
      <div v-if="isLoadingSessions" class="session-empty">正在加载会话…</div>
      <div v-else-if="sessions.length === 0" class="session-empty">还没有会话</div>
      <div
        v-for="session in sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.active }"
        role="button"
        tabindex="0"
        @click="$emit('select-session', session)"
        @keydown.enter="$emit('select-session', session)"
      >
        <span class="session-signal"></span>
        <span class="session-copy">
          <span class="session-title">{{ session.title }}</span>
          <span class="session-time">{{ session.time }}</span>
        </span>
        <span class="session-actions" @click.stop>
          <a-button
            class="session-rename"
            type="text"
            size="small"
            title="重命名会话"
            @click="$emit('rename-session', session)"
          >
            <template #icon><EditOutlined /></template>
          </a-button>
          <a-popconfirm
            title="确定删除这个会话吗？"
            ok-text="删除"
            cancel-text="取消"
            placement="right"
            @confirm="$emit('remove-session', session)"
          >
            <a-button
              class="session-more"
              type="text"
              size="small"
              title="删除会话"
            >
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </a-popconfirm>
        </span>
      </div>

      <a-button
        v-if="!isLoadingSessions && hasMoreSessions"
        class="load-more-sessions"
        block
        :loading="isLoadingMoreSessions"
        @click="$emit('load-more')"
      >
        加载更多会话
      </a-button>
    </div>

    <div class="sidebar-section-title">最近结果</div>
    <a-button class="result-link" type="text" block @click="$emit('show-result-history')">
      <template #icon><BarChartOutlined /></template>
      <span>英杰站预测曲线</span>
      <span class="result-arrow">↗</span>
    </a-button>
    <a-button class="result-link" type="text" block @click="$emit('show-result-history')">
      <template #icon><FileTextOutlined /></template>
      <span>哲丰站五月报告</span>
      <span class="result-arrow">↗</span>
    </a-button>

    <div class="sidebar-footer">
      <div class="footer-line"><span class="footer-dot"></span>数据服务正常</div>
      <a-button class="logout-button" type="text" size="small" @click="$emit('logout')">
        <template #icon><LogoutOutlined /></template>
        退出登录
      </a-button>
    </div>
  </aside>
</template>
