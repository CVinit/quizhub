import { createApp } from 'vue'
import { createPinia } from 'pinia'
// 组件由 unplugin 按需引入（见 vite.config.ts）；
// setupElementPlus 挂中文 locale 并注入指令式 API 的 app 上下文（_context）。
import { setupElementPlus } from './element-plus-config'
import App from './App.vue'
import router from './router'
import './assets/main.css'
// 指令式 API（ElMessage/ElMessageBox/ElNotification/ElLoading）以函数方式调用，
// unplugin resolver 无法为其注入样式，必须手动引入（否则确认框/提示无遮罩无样式）
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/notification/style/css'
import 'element-plus/es/components/loading/style/css'

const app = createApp(App)
setupElementPlus(app)
app.use(createPinia())
app.use(router)
app.mount('#app')
