<template>
  <el-container class="layout">
    <!-- 10010 营业厅风格：顶部品牌红饰条 -->
    <div class="top-bar"></div>
    <el-header class="header">
      <div class="logo" @click="$router.push('/')">
        <LogoMark :size="30" />
        <span class="site-name">{{ siteName }}</span>
      </div>
      <!-- 桌面/平板：水平菜单 -->
      <el-menu mode="horizontal" :default-active="activeMenu" :ellipsis="false" router class="desktop-menu">
        <el-menu-item index="/">首页</el-menu-item>
        <el-menu-item index="/answer">答题</el-menu-item>
        <el-menu-item index="/exam">考试</el-menu-item>
        <el-menu-item index="/wrong">错题本</el-menu-item>
        <el-menu-item index="/marks">我的标记</el-menu-item>
        <el-menu-item index="/rank" v-if="site.rank_visible">排行</el-menu-item>
      </el-menu>
      <!-- 手机端菜单入口已下沉到底部导航的"更多"标签，顶栏不再放汉堡 -->
      <div class="user">
        <template v-if="auth.isAdmin">
          <el-button text @click="$router.push('/admin')" class="admin-link">管理后台</el-button>
        </template>
        <el-dropdown @command="onCmd">
          <span class="user-name">{{ auth.user?.name || auth.user?.email }} ▾</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">个人信息</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>
    <!-- 手机端抽屉菜单 -->
    <el-drawer v-model="drawer" direction="ltr" :size="240" :with-header="false" class="mobile-drawer">
      <el-menu :default-active="activeMenu" router @select="drawer = false">
        <el-menu-item index="/">首页</el-menu-item>
        <el-menu-item index="/answer">答题</el-menu-item>
        <el-menu-item index="/exam">考试</el-menu-item>
        <el-menu-item index="/wrong">错题本</el-menu-item>
        <el-menu-item index="/marks">我的标记</el-menu-item>
        <el-menu-item index="/rank" v-if="site.rank_visible">排行</el-menu-item>
        <el-menu-item index="/profile">个人信息</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin">管理后台</el-menu-item>
        <el-menu-item @click="onCmd('logout')">退出登录</el-menu-item>
      </el-menu>
    </el-drawer>
    <el-main class="main">
      <router-view />
    </el-main>
    <el-footer class="footer">
      <span>{{ siteName }} · 企业内部培训平台</span>
    </el-footer>

    <!-- 手机端：底部导航栏（主入口，比汉堡更直观） -->
    <nav class="bottom-nav">
      <router-link
        v-for="item in bottomItems" :key="item.path" :to="item.path"
        class="bn-item" :class="{ active: isActive(item.path) }">
        <el-icon class="bn-icon"><component :is="item.icon" /></el-icon>
        <span class="bn-label">{{ item.label }}</span>
      </router-link>
      <!-- "更多"标签：点击打开抽屉，容纳标记/排行/管理后台/退出等次级入口 -->
      <div class="bn-item" :class="{ active: drawer }" @click="drawer = true">
        <el-icon class="bn-icon"><Menu /></el-icon>
        <span class="bn-label">更多</span>
      </div>
    </nav>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Menu, HomeFilled, Document, Files, Warning, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const site = useSiteStore()
const activeMenu = computed(() => route.path)
const siteName = computed(() => site.site_name)
const drawer = ref(false)

// 底部导航主入口（4 个高频页 + 1 个"更多"次级入口）
const bottomItems = [
  { path: '/', label: '首页', icon: HomeFilled },
  { path: '/answer', label: '答题', icon: Document },
  { path: '/exam', label: '考试', icon: Files },
  { path: '/wrong', label: '错题', icon: Warning },
]
const isActive = (p: string) => (p === '/' ? route.path === '/' : route.path.startsWith(p))

const onCmd = (cmd: string) => {
  if (cmd === 'profile') router.push('/profile')
  else if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { min-height: 100vh; }
/* 品牌红饰条（10010 风格） */
.top-bar { height: 4px; background: linear-gradient(90deg, var(--brand-primary-dark), var(--brand-primary), var(--brand-primary-light)); }
.header {
  display: flex; align-items: center; gap: 24px;
  background: #fff; border-bottom: 1px solid #e6e8eb;
  padding: 0 24px;
  position: sticky; top: 0; z-index: 10;
}
.logo { display: flex; align-items: center; gap: 8px; cursor: pointer; flex-shrink: 0; }
.site-name { font-weight: 600; font-size: 18px; color: var(--brand-primary); }
.header :deep(.el-menu) { border-bottom: none; flex: 1; }
/* 激活菜单项：红字 + 底部红条（随主题色生效） */
.header :deep(.el-menu--horizontal > .el-menu-item.is-active) { border-bottom-color: var(--brand-primary); color: var(--brand-primary); }
.user { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.user-name { cursor: pointer; color: #606266; }
.menu-trigger { display: none; }
.main { padding: 24px; max-width: 1200px; margin: 0 auto; width: 100%; flex: 1; }
.footer {
  height: auto; padding: 16px 24px; text-align: center;
  color: #909399; font-size: 12px;
  border-top: 1px solid #e6e8eb; background: #fff;
}
/* 底部导航栏：仅手机端显示 */
.bottom-nav {
  display: none;
  position: fixed; bottom: 0; left: 0; right: 0;
  height: 56px; background: #fff; border-top: 1px solid #e6e8eb;
  z-index: 20;
  justify-content: space-around; align-items: center;
}
.bn-item { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; flex: 1; height: 100%; color: #909399; cursor: pointer; text-decoration: none; }
.bn-item.active { color: var(--brand-primary); }
.bn-icon { font-size: 20px; }
.bn-label { font-size: 11px; }
/* 手机端：隐藏水平菜单，显示底部导航 */
@media (max-width: 767px) {
  .header { padding: 0 12px; gap: 12px; }
  .site-name { font-size: 16px; }
  .desktop-menu { display: none; }
  .admin-link { display: none; }
  .user-name { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .main { padding: 12px 12px 72px; } /* 底部留出导航栏高度 */
  .footer { padding: 12px; }
  .bottom-nav { display: flex; }
}
</style>
