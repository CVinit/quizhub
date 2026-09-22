import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

/**
 * 单元测试配置。
 *
 * 与 vite.config.ts 分离：测试只覆盖纯逻辑与组合式函数（不测 SFC 模板），
 * 因此不需要 @vitejs/plugin-vue，别名与 vite.config.ts 保持一致即可。
 */
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.spec.ts'],
  },
})
