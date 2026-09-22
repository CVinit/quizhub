<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useTheme } from '@/composables/useTheme'
import { useAuthStore } from '@/stores/auth'

useTheme()

const auth = useAuthStore()
// 缓存里的用户信息可能已过期（管理员改了角色/姓名/状态），启动后静默刷新一次。
// 令牌失效时由 http 拦截器统一登出并提示，这里只需吞掉 rejection 避免未处理拒绝。
onMounted(() => {
  if (auth.isLoggedIn) auth.fetchMe().catch(() => {})
})
</script>
