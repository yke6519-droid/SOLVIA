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

const renderedContent = computed(() => {
  const source = String(props.content || '')
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