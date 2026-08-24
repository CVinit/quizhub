import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/auth/Login.vue'), meta: { guest: true } },
  { path: '/register', name: 'register', component: () => import('@/views/auth/Register.vue'), meta: { guest: true } },
  { path: '/verify', name: 'verify', component: () => import('@/views/auth/Verify.vue'), meta: { guest: true } },
  {
    path: '/',
    component: () => import('@/layouts/UserLayout.vue'),
    meta: { auth: true },
    children: [
      { path: '', name: 'home', component: () => import('@/views/user/Panel.vue') },
      { path: 'answer', name: 'answer-modes', component: () => import('@/views/user/AnswerModes.vue') },
      { path: 'answer/:mode', name: 'answer', component: () => import('@/views/user/Answer.vue') },
      { path: 'exam', name: 'exam-list', component: () => import('@/views/user/ExamList.vue') },
      { path: 'exam/:id', name: 'exam-taking', component: () => import('@/views/user/ExamTaking.vue') },
      { path: 'wrong', name: 'wrong', component: () => import('@/views/user/Wrong.vue') },
      { path: 'marks', name: 'marks', component: () => import('@/views/user/Marks.vue') },
      { path: 'rank', name: 'rank', component: () => import('@/views/user/Rank.vue') },
      { path: 'profile', name: 'profile', component: () => import('@/views/user/Profile.vue') },
    ],
  },
  {
    path: '/admin',
    component: () => import('@/layouts/AdminLayout.vue'),
    meta: { auth: true, admin: true },
    children: [
      { path: '', name: 'admin-overview', component: () => import('@/views/admin/Overview.vue') },
      { path: 'users', name: 'admin-users', component: () => import('@/views/admin/Users.vue') },
      { path: 'groups', name: 'admin-groups', component: () => import('@/views/admin/Groups.vue') },
      { path: 'questions', name: 'admin-questions', component: () => import('@/views/admin/Questions.vue') },
      { path: 'upload', name: 'admin-upload', component: () => import('@/views/admin/Upload.vue') },
      { path: 'exam-templates', name: 'admin-exam-templates', component: () => import('@/views/admin/ExamTemplates.vue') },
      { path: 'mock-config', name: 'admin-mock-config', component: () => import('@/views/admin/MockConfig.vue') },
      { path: 'exams', name: 'admin-exams', component: () => import('@/views/admin/Exams.vue') },
      { path: 'exam-records', name: 'admin-exam-records', component: () => import('@/views/admin/ExamRecords.vue') },
      { path: 'review', name: 'admin-review', component: () => import('@/views/admin/Review.vue') },
      { path: 'settings', name: 'admin-settings', component: () => import('@/views/admin/Settings.vue') },
      { path: 'audit', name: 'admin-audit', component: () => import('@/views/admin/Audit.vue') },
    ],
  },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFound.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()
  const site = useSiteStore()
  // 排行被后台关闭时，普通用户无法进入排行页（管理员可查看）
  // 路由守卫先取最新可见性（绕过 loaded 缓存，避免页面停留在旧开关状态时直链放行）
  if (to.path === '/rank' && !auth.isAdmin) {
    await site.refresh()
    if (!site.rank_visible) return next({ name: 'home' })
  }
  if (to.meta.guest && auth.isLoggedIn) return next({ name: 'home' })
  if (to.meta.auth && !auth.isLoggedIn) return next({ name: 'login' })
  if (to.meta.admin && !auth.isAdmin) return next({ name: 'home' })
  next()
})

export default router
