<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  question: { type: String, default: '' },
  answer: { type: String, default: '' },
  status: { type: String, default: 'waiting' },
  submittedAnswer: { type: String, default: '' },
  error: { type: String, default: '' },
})

const emit = defineEmits(['update:answer', 'submit'])
const replyInput = ref(props.answer)

const isSubmitting = computed(() => props.status === 'submitting')
const isSubmitted = computed(() => props.status === 'submitted')
const canSubmit = computed(() => Boolean(replyInput.value.trim()) && !isSubmitting.value && !isSubmitted.value)

watch(() => props.answer, (value) => {
  if (value !== replyInput.value) replyInput.value = value || ''
})

watch(() => props.question, () => {
  replyInput.value = props.answer || ''
})

function updateAnswer(event) {
  replyInput.value = event.target.value
  emit('update:answer', replyInput.value)
}

function submit() {
  if (!canSubmit.value) return
  emit('submit', replyInput.value.trim())
}
</script>

<template>
  <section class="ask-user-card" :class="{ 'is-submitted': isSubmitted, 'has-error': error }" role="dialog" aria-live="polite" aria-label="等待用户回复">
    <div class="ask-user-heading">
      <span class="ask-user-icon" aria-hidden="true">?</span>
      <div class="ask-user-heading-copy">
        <strong>{{ isSubmitted ? '回复已提交' : '需要你的确认' }}</strong>
        <span>{{ isSubmitted ? 'Agent 正在继续执行当前任务' : '补充信息后任务才会继续执行' }}</span>
      </div>
      <span class="ask-user-status" :class="status">
        <span class="ask-user-status-dot"></span>
        {{ isSubmitted ? '处理中' : isSubmitting ? '发送中' : '等待回复' }}
      </span>
    </div>

    <p class="ask-user-question">{{ question }}</p>

    <template v-if="isSubmitted">
      <div class="ask-user-submitted"><span>你的回复</span><strong>{{ submittedAnswer }}</strong></div>
    </template>
    <form v-else class="ask-user-form" @submit.prevent="submit">
      <textarea
        :value="replyInput"
        class="ask-user-input"
        rows="2"
        maxlength="1000"
        placeholder="输入确认、站点名称、日期或补充条件"
        :disabled="isSubmitting"
        @input="updateAnswer"
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <div class="ask-user-actions">
        <span class="ask-user-hint">Enter 发送 · Shift + Enter 换行</span>
        <button class="ask-user-submit" type="submit" :disabled="!canSubmit">
          {{ isSubmitting ? '正在发送…' : '确认并继续' }}
          <span aria-hidden="true">↗</span>
        </button>
      </div>
    </form>
    <p v-if="error" class="ask-user-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.ask-user-card {
  margin-top: 16px;
  padding: 17px 18px 16px;
  color: var(--task-ink);
  border: 1px solid color-mix(in srgb, var(--accent) 32%, var(--task-border));
  border-radius: 10px;
  background: color-mix(in srgb, var(--task-bg) 94%, var(--accent));
  box-shadow: 0 10px 26px color-mix(in srgb, var(--shadow) 72%, transparent);
}
.ask-user-card.is-submitted {
  border-color: color-mix(in srgb, var(--accent) 50%, var(--task-border));
  background: color-mix(in srgb, var(--task-bg) 88%, var(--accent));
}
.ask-user-heading { display: flex; align-items: center; gap: 11px; }
.ask-user-icon {
  width: 27px;
  height: 27px;
  display: grid;
  flex: 0 0 27px;
  place-items: center;
  color: var(--task-bg);
  border-radius: 50%;
  background: var(--warning);
  font-family: 'Space Mono', monospace;
  font-size: 14px;
  font-weight: 700;
}
.ask-user-heading-copy { min-width: 0; flex: 1; }
.ask-user-heading-copy strong { display: block; font-size: 15px; font-weight: 700; }
.ask-user-heading-copy span { display: block; margin-top: 3px; color: var(--task-muted); font-size: 12px; }
.ask-user-status { display: inline-flex; align-items: center; gap: 6px; flex: 0 0 auto; color: var(--task-muted); font-family: 'Space Mono', monospace; font-size: 10px; white-space: nowrap; }
.ask-user-status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--warning); box-shadow: 0 0 0 4px color-mix(in srgb, var(--warning) 13%, transparent); }
.ask-user-status.submitted .ask-user-status-dot { background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 13%, transparent); }
.ask-user-question { margin: 15px 0 13px; color: var(--task-ink); white-space: pre-wrap; font-size: 14px; line-height: 1.65; }
.ask-user-form { display: flex; flex-direction: column; gap: 9px; }
.ask-user-input { width: 100%; min-height: 56px; resize: vertical; padding: 10px 11px; color: var(--task-ink); border: 1px solid var(--task-border); border-radius: 7px; outline: none; background: color-mix(in srgb, var(--task-bg) 82%, var(--task-ink)); font: inherit; font-size: 13px; line-height: 1.55; }
.ask-user-input::placeholder { color: var(--task-muted); }
.ask-user-input:focus { border-color: var(--primary-action); box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-action) 15%, transparent); }
.ask-user-input:disabled { cursor: wait; opacity: .68; }
.ask-user-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ask-user-hint { color: var(--task-muted); font-size: 11px; }
.ask-user-submit { display: inline-flex; align-items: center; gap: 8px; padding: 9px 13px; color: var(--primary-action-ink); border-radius: 6px; background: var(--primary-action); box-shadow: 0 6px 15px color-mix(in srgb, var(--primary-action) 22%, transparent); font-size: 13px; font-weight: 700; }
.ask-user-submit:hover:not(:disabled) { background: var(--primary-action-hover); }
.ask-user-submit:disabled { cursor: wait; opacity: .52; }
.ask-user-submitted { display: flex; align-items: baseline; gap: 10px; padding: 10px 11px; border: 1px solid color-mix(in srgb, var(--accent) 26%, var(--task-border)); border-radius: 7px; background: color-mix(in srgb, var(--task-bg) 80%, var(--accent)); }
.ask-user-submitted span { color: var(--task-muted); font-size: 11px; }
.ask-user-submitted strong { color: var(--task-ink); font-size: 13px; font-weight: 600; white-space: pre-wrap; }
.ask-user-error { margin: 9px 0 0; color: var(--danger); font-size: 12px; line-height: 1.5; }
@media (max-width: 640px) {
  .ask-user-card { padding: 14px; }
  .ask-user-heading { align-items: flex-start; }
  .ask-user-status { margin-left: auto; }
  .ask-user-actions { align-items: stretch; flex-direction: column; }
  .ask-user-submit { justify-content: center; }
  .ask-user-hint { order: 2; }
  .ask-user-submitted { align-items: flex-start; flex-direction: column; gap: 4px; }
}
</style>