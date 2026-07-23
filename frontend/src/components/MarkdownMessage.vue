<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps({
  content: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

function removeGeneratedFileLinks(source) {
  return String(source || '')
    // 删除“文件路径/下载链接”整行，避免旧消息继续展示服务器路径。
    .replace(/^\s*(?:[-*]\s*)?(?:文件路径|下载链接|下载地址|文件地址)\s*[:：].*(?:\r?\n|$)/gim, '')
    // 删除指向受保护文件接口的 Markdown 链接和裸 URL。
    .replace(/\[[^\]]*(?:点击下载|下载文件|下载)[^\]]*\]\((?:https?:\/\/[^)]*)?\/api\/files\/[^)]*\)/gi, '')
    .replace(/(?:https?:\/\/[^\s)]+)?\/api\/files\/[^\s)]+/gi, '')
    // 删除旧实现可能泄露的本地生成路径。
    .replace(/`?(?:[A-Za-z]:[\\/]|\/)(?:temp|uploads?)[\\/][^`\s)]+`?/gi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

const renderedContent = computed(() => {
  const source = removeGeneratedFileLinks(props.content)
  if (!source) return ''

  const html = markdown.render(source)
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['style', 'onerror', 'onclick', 'onload', 'onmouseover'],
    ALLOW_UNKNOWN_PROTOCOLS: false,
  })
})
</script>

<template>
  <div class="markdown-message" :class="{ 'is-streaming': streaming }" v-html="renderedContent"></div>
</template>
