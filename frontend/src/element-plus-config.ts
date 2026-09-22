// Element Plus 全局配置（中文 locale）：按需引入模式下不使用 app.use(ElementPlus) 全量注册组件，
// 改用 provideGlobalConfig 只注入配置，避免把未使用组件打进主包。
//
// 关键：指令式 API（ElMessage/ElMessageBox/ElNotification/ElLoading）在“函数式”调用时
// 通过各自的 _context 拿到 app 上下文（见 element-plus 的 withInstallFunction /
// message-box/index.mjs：`_MessageBox._context = app._context`）。该 _context 只由
// `app.use(ElementPlus)` 或显式调用这些组件的 install(app) 赋值；仅调用
// provideGlobalConfig 不会赋值，会导致 _context 为 null —— 确认框/提示脱离 app 上下文渲染
// （拿不到 locale 与全局配置，表现为确认框异常）。因此这里显式安装这四个指令式 API。
import { ElLoading, ElMessage, ElMessageBox, ElNotification, provideGlobalConfig } from 'element-plus'
import type { App } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

/** 指令式 API 集合：每个都带有 install()，用于注入 app 上下文（_context）。 */
const imperativePlugins: { install: (app: App) => void }[] = [ElMessage, ElMessageBox, ElNotification, ElLoading]

/**
 * 安装 Element Plus 全局配置与指令式 API 上下文。
 *
 * 等价于 `app.use(ElementPlus, { locale: zhCn })` 中“配置 + 指令式 API”两部分，
 * 但不全量注册组件（组件仍由 unplugin-vue-components 按需引入）。
 */
export function setupElementPlus(app: App): void {
  provideGlobalConfig({ locale: zhCn }, app, true)
  for (const plugin of imperativePlugins) {
    app.use(plugin)
  }
}
