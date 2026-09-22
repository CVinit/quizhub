<template>
  <el-menu :default-active="active" router @select="$emit('navigate')">
    <el-menu-item index="/admin">概览面板</el-menu-item>
    <el-sub-menu index="users">
      <template #title>用户管理</template>
      <el-menu-item index="/admin/users">用户列表</el-menu-item>
      <el-menu-item index="/admin/groups">分组管理</el-menu-item>
    </el-sub-menu>
    <el-sub-menu index="question">
      <template #title>题库管理</template>
      <el-menu-item index="/admin/question-banks">题库管理</el-menu-item>
      <el-menu-item index="/admin/questions">题目列表</el-menu-item>
      <el-menu-item index="/admin/upload">上传题库</el-menu-item>
      <el-menu-item index="/admin/exam-templates">试卷模板</el-menu-item>
    </el-sub-menu>
    <el-sub-menu index="exam">
      <template #title>考试管理</template>
      <el-menu-item index="/admin/exams">正式考试</el-menu-item>
      <el-menu-item index="/admin/exam-records">考试记录</el-menu-item>
      <el-menu-item index="/admin/review">简答复核</el-menu-item>
    </el-sub-menu>
    <el-sub-menu v-if="auth.isSuper" index="system">
      <template #title>系统管理</template>
      <el-menu-item index="/admin/settings">基础设置</el-menu-item>
      <el-menu-item index="/admin/audit">审计日志</el-menu-item>
    </el-sub-menu>
  </el-menu>
</template>

<script setup lang="ts">
/**
 * 管理后台侧栏导航。
 *
 * 桌面固定侧栏与手机端抽屉共用同一份菜单定义，避免两处手抄导致路由增删后不一致。
 * 设置与审计接口要求超级管理员，部门管理员不应看到点进去只有 403 的入口。
 */
import { useAuthStore } from '@/stores/auth'

defineProps<{ active: string }>()
defineEmits<{ navigate: [] }>()

const auth = useAuthStore()
</script>
