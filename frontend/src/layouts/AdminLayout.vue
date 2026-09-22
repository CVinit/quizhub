<template>
  <el-container class="layout">
    <!-- 桌面/平板：固定侧栏 -->
    <el-aside width="220px" class="aside desktop-aside">
      <div class="logo">
        <LogoMark :size="26" />
        <span>{{ site.site_name }} · 管理后台</span>
      </div>
      <AdminMenu :active="activeMenu" />
    </el-aside>
    <el-container>
      <!-- 品牌红饰条 + 白色顶栏（与用户端一致） -->
      <div class="top-bar"></div>
      <el-header class="header">
        <div class="header-left">
          <!-- 手机端：汉堡切换侧栏抽屉（必须可聚焦且有可访问名，图标本身无法键盘/读屏访问） -->
          <el-button text class="menu-trigger" aria-label="打开导航菜单" @click="drawer = true">
            <el-icon><Menu /></el-icon>
          </el-button>
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
      <AdminMenu :active="activeMenu" @navigate="drawer = false" />
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
import AdminMenu from '@/layouts/components/AdminMenu.vue'

const auth = useAuthStore()
const route = useRoute()
const site = useSiteStore()
const activeMenu = computed(() => route.path)
const roleLabel = computed(
  () => ({ user: '普通用户', dept_admin: '部门管理员', super_admin: '超级管理员' })[auth.user?.role || 'user'],
)
const drawer = ref(false)
</script>

<style scoped>
.layout {
  min-height: 100vh;
}
.aside {
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-weight: 600;
  color: var(--brand-primary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.top-bar {
  height: 4px;
  background: linear-gradient(90deg, var(--brand-primary-dark), var(--brand-primary), var(--brand-primary-light));
}
.header {
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.user {
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.main {
  padding: 24px;
}
.menu-trigger {
  display: none;
  font-size: 22px;
  padding: 4px;
  color: var(--el-text-color-primary);
}
/* 手机端：隐藏固定侧栏，启用抽屉 */
@media (max-width: 767px) {
  .desktop-aside {
    display: none;
  }
  .menu-trigger {
    display: inline-flex;
  }
  .header {
    padding: 0 12px;
  }
  .main {
    padding: 12px;
  }
  .user {
    font-size: 13px;
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
