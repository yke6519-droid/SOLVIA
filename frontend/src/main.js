import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { createRouter } from './router'
import 'ant-design-vue/dist/reset.css'
import './styles.css'

const router = createRouter()
const pinia = createPinia()

createApp(App)
  .use(pinia)
  .use(router)
  .mount('#app')
