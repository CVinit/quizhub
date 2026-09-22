// ESLint 扁平配置（ESLint 8.57+ 支持）。
// 此前 package.json 声明了 `lint` 脚本却没有任何配置文件，npm run lint 无法生效。
import js from '@eslint/js'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import vuePlugin from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', '*.d.ts'],
  },
  js.configs.recommended,
  // .ts
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      // TS 已负责标识符解析；基础 no-undef 不认识 DOM/TS 类型，会对
      // KeyboardEvent、setInterval、window 等误报。
      'no-undef': 'off',
      'no-unused-vars': 'off',
      // 生产代码不应残留 console（问题排查请用 ElMessage 或移除）
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  // .vue
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { parser: tsParser, extraFileExtensions: ['.vue'] },
    },
    plugins: { vue: vuePlugin, '@typescript-eslint': tsPlugin },
    rules: {
      // 仅启用能发现真实缺陷的规则；eslint-plugin-vue 的 recommended 里包含大量
      // 纯排版规则（max-attributes-per-line 等），对已有代码会产出 2000+ 噪音，
      // 反而淹没了真正的问题。
      'vue/no-unused-vars': 'error',
      'vue/no-unused-components': 'error',
      'vue/no-dupe-keys': 'error',
      'vue/no-side-effects-in-computed-properties': 'error',
      'vue/no-mutating-props': 'error',
      'vue/require-v-for-key': 'error',
      'vue/no-use-v-if-with-v-for': 'error',
      'vue/valid-v-model': 'error',
      'vue/no-parsing-error': 'error',
      'vue/multi-word-component-names': 'off',
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
]
