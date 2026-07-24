<script setup>
import { computed } from 'vue'
import { theme as antTheme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'

const props = defineProps({
  theme: {
    type: String,
    default: 'dark',
  },
})

// 将 SolarAgent 原有的深浅色变量映射到 Ant Design Vue 的主题算法。
const themeConfig = computed(() => ({
  algorithm: props.theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
  token: {
    colorPrimary: props.theme === 'dark' ? '#65e6cf' : '#4b8e83',
    colorInfo: props.theme === 'dark' ? '#65e6cf' : '#4b8e83',
    colorSuccess: props.theme === 'dark' ? '#65e6cf' : '#4b8e83',
    colorWarning: props.theme === 'dark' ? '#efad60' : '#a9753e',
    colorError: props.theme === 'dark' ? '#ef887d' : '#ad5f56',
    borderRadius: 7,
    fontFamily: "'Outfit', 'Noto Sans SC', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Button: {
      controlHeight: 34,
      borderRadius: 7,
    },
    Layout: {
      bodyBg: 'transparent',
      headerBg: 'transparent',
      siderBg: 'transparent',
    },
  },
}))

function getPopupContainer() {
  return document.querySelector('.app-shell') || document.body
}
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="themeConfig" :get-popup-container="getPopupContainer">
    <a-app class="solar-ant-app">
      <slot />
    </a-app>
  </a-config-provider>
</template>

<style scoped>
.solar-ant-app {
  width: 100%;
  height: 100%;
}
</style>
