<template>
  <el-menu :default-active="active" :mode="mode" :ellipsis="ellipsis" router @select="$emit('navigate')">
    <el-menu-item v-for="item in items" :key="item.path" :index="item.path">
      {{ item.label }}
    </el-menu-item>
    <el-menu-item v-if="showProfile" index="/profile">个人信息</el-menu-item>
    <el-menu-item v-if="isAdmin" index="/admin">管理后台</el-menu-item>
  </el-menu>
</template>

<script setup lang="ts">
/**
 * 用户端导航（仅导航项；退出登录等非导航动作由布局自行渲染）。
 *
 * 桌面水平菜单与手机端抽屉共用同一份菜单定义，差异（个人信息/管理后台
 * 只在抽屉出现）通过 props 显式开关，避免两处手抄导致路由增删后不一致。
 */
withDefaults(
  defineProps<{
    active: string
    isAdmin?: boolean
    /** 抽屉版才展示的次级入口 */
    showProfile?: boolean
    /** 桌面顶栏用 horizontal，抽屉用 vertical */
    mode?: 'horizontal' | 'vertical'
    ellipsis?: boolean
  }>(),
  { isAdmin: false, showProfile: false, mode: 'vertical', ellipsis: true },
)

defineEmits<{ navigate: [] }>()

const items = [
  { path: '/', label: '首页' },
  { path: '/answer', label: '答题' },
  { path: '/exam', label: '考试' },
  { path: '/wrong', label: '错题本' },
  { path: '/marks', label: '我的标记' },
]
</script>
