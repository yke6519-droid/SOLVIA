<script setup>
import {
  BulbOutlined,
  DatabaseOutlined,
  EllipsisOutlined,
  EyeOutlined,
} from '@ant-design/icons-vue'

defineProps({
  statusLabel: {
    type: String,
    default: '在线',
  },
  displayName: {
    type: String,
    default: '用户',
  },
  theme: {
    type: String,
    default: 'dark',
  },
})

defineEmits(['import-data', 'toggle-theme', 'open-settings'])
</script>

<template>
  <!-- 页面骨架继续使用语义化 Header，避免 Ant Layout 的高度和背景规则覆盖现有主题。 -->
  <header class="topbar">
    <div class="brand-lockup">
      <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div>
      <div>
        <div class="brand-name">SOLVIA</div>
        <div class="brand-caption">自然语言驱动的光伏运维智能工作台</div>
      </div>
    </div>

    <div class="topbar-center"></div>

    <div class="user-menu">
      <span class="status-dot"></span>
      <span>{{ statusLabel }}</span>
      <a-button class="data-import-button" size="small" @click="$emit('import-data')">
        <template #icon><DatabaseOutlined /></template>
        导入数据
      </a-button>
      <a-button
        class="theme-toggle"
        size="small"
        :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'"
        :aria-pressed="theme === 'light'"
        @click="$emit('toggle-theme')"
      >
        <template #icon>
          <BulbOutlined v-if="theme === 'dark'" />
          <EyeOutlined v-else />
        </template>
        {{ theme === 'dark' ? '浅色' : '深色' }}
      </a-button>
      <span class="user-divider"></span>
      <span class="user-name">{{ displayName }}</span>
      <a-button
        class="icon-button"
        type="text"
        size="small"
        aria-label="更多设置"
        @click="$emit('open-settings')"
      >
        <template #icon><EllipsisOutlined /></template>
      </a-button>
    </div>
  </header>
</template>
