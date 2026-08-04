<script setup>
import { ref } from 'vue'
import { ArrowRightOutlined, LockOutlined, UserOutlined } from '@ant-design/icons-vue'
import { login, saveAuth } from '../api'
import { useRouter } from '../router'

const props = defineProps({
  theme: { type: String, required: true },
})
const emit = defineEmits(['authenticated'])
const router = useRouter()
const loginForm = ref({ username: '', password: '' })
const loginError = ref('')
const isLoggingIn = ref(false)

async function submitLogin() {
  const username = loginForm.value.username.trim()
  const password = loginForm.value.password
  if (!username || !password) {
    loginError.value = '请输入用户名和密码'
    return
  }
  isLoggingIn.value = true
  loginError.value = ''
  try {
    const auth = await login({ username, password })
    saveAuth(auth)
    emit('authenticated', auth.user)
    router.push('/workspace')
  } catch (error) {
    loginError.value = error.message || '登录失败，请稍后重试'
  } finally {
    isLoggingIn.value = false
  }
}
</script>

<template>
  <section class="login-screen">
    <header class="login-nav">
      <div class="brand-lockup">
        <div class="brand-mark"><span></span><span></span><span></span></div>
        <div><div class="brand-name">SOLVIA</div>
        <div class="brand-caption">光伏运维智能工作台</div>
      </div>
      </div>
      <div class="login-nav-right">
        <!-- <span>光伏运营工作台</span> -->
        <!-- <span class="login-theme-label">{{ props.theme === 'dark' ? '深色工作台' : '浅色工作台' }}</span> -->
      </div>
    </header>

    <main class="login-main">
      <section class="login-hero" aria-labelledby="login-hero-title">
        <p class="login-eyebrow">让每一次判断都有数据依据</p>
        <h1 id="login-hero-title">把光伏现场，<span>变成可执行的判断。</span></h1>
        <!-- <p class="login-lead">从真实发电记录到预测结果，SolarAgent 把运营人员每天要做的查询、预测与复盘，收进一个清晰的工作台。</p> -->
        <div class="login-rail" aria-label="工作台能力">
          <div class="login-rail-item"><span class="login-rail-index">01</span><div><strong>查询真实数据</strong><span>按站点和日期快速定位记录</span></div></div>
          <div class="login-rail-item featured"><span class="login-rail-index">02</span><div><strong>确认预测任务</strong><span>关键条件明确后再运行模型</span></div></div>
          <div class="login-rail-item"><span class="login-rail-index">03</span><div><strong>复盘运营结果</strong><span>用曲线和指标看清变化</span></div></div>
        </div>
      </section>

      <section class="login-card-wrap" aria-label="登录 SOLVIA">
        <div class="login-card">
          <div class="login-card-heading"><p class="login-card-kicker">进入工作台</p><span class="login-card-signal"></span></div>
          <h2>欢迎回来</h2>
          <!-- <p class="login-card-intro">使用你的运营账号登录，继续处理光伏任务。</p> -->
          <a-form class="login-form" :model="loginForm" @finish="submitLogin">
            <label class="login-field">
              <span>用户名</span>
              <a-input
                v-model:value="loginForm.username"
                name="username"
                autocomplete="username"
                placeholder="输入用户名"
                :disabled="isLoggingIn"
                size="large"
              >
                <template #prefix><UserOutlined /></template>
              </a-input>
            </label>
            <label class="login-field">
              <span>密码</span>
              <a-input-password
                v-model:value="loginForm.password"
                name="password"
                autocomplete="current-password"
                placeholder="输入密码"
                :disabled="isLoggingIn"
                size="large"
              >
                <template #prefix><LockOutlined /></template>
              </a-input-password>
            </label>
            <a-alert v-if="loginError" class="login-error" type="error" show-icon :message="loginError" />
            <a-button
              class="login-submit"
              type="primary"
              html-type="submit"
              size="large"
              block
              :loading="isLoggingIn"
            >
              {{ isLoggingIn ? '正在验证…' : '进入 SOLVIA' }}
              <ArrowRightOutlined />
            </a-button>
          </a-form>
          <!-- <div class="login-card-foot"><span>JWT 安全登录</span><span>凭证有效期由服务端控制</span></div> -->
        </div>
        <div class="login-card-shadow" aria-hidden="true"></div>
      </section>
    </main>
  </section>
</template>
