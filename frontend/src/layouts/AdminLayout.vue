<template>
  <el-container class="layout">
    <!-- 桌面/平板：固定侧栏 -->
    <el-aside width="220px" class="aside desktop-aside">
      <div class="logo">
        <LogoMark :size="26" />
        <span>{{ site.site_name }} · 管理后台</span>
      </div>
      <el-menu :default-active="activeMenu" router>
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
          <el-menu-item index="/admin/mock-config">模拟考试设置</el-menu-item>
          <el-menu-item index="/admin/exams">正式考试</el-menu-item>
          <el-menu-item index="/admin/exam-records">考试记录</el-menu-item>
          <el-menu-item index="/admin/review">简答复核</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="system">
          <template #title>系统管理</template>
          <el-menu-item index="/admin/settings">基础设置</el-menu-item>
          <el-menu-item index="/admin/audit">审计日志</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <!-- 品牌红饰条 + 白色顶栏（与用户端一致） -->
      <div class="top-bar"></div>
      <el-header class="header">
        <div class="header-left">
          <!-- 手机端：汉堡切换侧栏抽屉 -->
          <el-icon class="menu-trigger" @click="drawer = true"><Menu /></el-icon>
          <el-button text @click="$router.push('/')">← 返回用户端</el-button>
        </div>
        <div class="user">{{ auth.user?.name }}（{{ roleLabel }}）</div>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>

    <!-- 手机端：侧栏抽屉 -->
    <el-drawer v-model="drawer" direction="ltr" :size="260" :with-header="false" class="admin-drawer">
      <div class="logo drawer-logo">
        <LogoMark :size="26" />
        <span>{{ site.site_name }} · 管理后台</span>
      </div>
      <el-menu :default-active="activeMenu" router @select="drawer = false">
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
          <el-menu-item index="/admin/mock-config">模拟考试设置</el-menu-item>
          <el-menu-item index="/admin/exams">正式考试</el-menu-item>
          <el-menu-item index="/admin/exam-records">考试记录</el-menu-item>
          <el-menu-item index="/admin/review">简答复核</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="system">
          <template #title>系统管理</template>
          <el-menu-item index="/admin/settings">基础设置</el-menu-item>
          <el-menu-item index="/admin/audit">审计日志</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-drawer>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Menu } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'

const auth = useAuthStore()
const route = useRoute()
const site = useSiteStore()
const activeMenu = computed(() => route.path)
const roleLabel = computed(() => ({ user: '普通用户', dept_admin: '部门管理员', super_admin: '超级管理员' }[auth.user?.role || 'user']))
const drawer = ref(false)
</script>

<style scoped>
.layout { min-height: 100vh; }
.aside { background: #fff; border-right: 1px solid #e6e8eb; }
.logo { height: 60px; display: flex; align-items: center; justify-content: center; gap: 8px; font-weight: 600; color: var(--brand-primary); border-bottom: 1px solid #f0f0f0; }
.top-bar { height: 4px; background: linear-gradient(90deg, var(--brand-primary-dark), var(--brand-primary), var(--brand-primary-light)); }
.header { background: #fff; border-bottom: 1px solid #e6e8eb; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; }
.header-left { display: flex; align-items: center; gap: 8px; }
.user { color: #606266; font-size: 14px; }
.main { padding: 24px; }
.menu-trigger { display: none; font-size: 22px; cursor: pointer; color: #303133; }
/* 手机端：隐藏固定侧栏，启用抽屉 */
@media (max-width: 767px) {
  .desktop-aside { display: none; }
  .menu-trigger { display: inline-flex; }
  .header { padding: 0 12px; }
  .main { padding: 12px; }
  .user { font-size: 13px; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
</style>
