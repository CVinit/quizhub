import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/auth/Login.vue'),
    meta: { guest: true, title: '登录' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/auth/Register.vue'),
    meta: { guest: true, title: '注册' },
  },
  {
    path: '/verify',
    name: 'verify',
    component: () => import('@/views/auth/Verify.vue'),
    meta: { guest: true, title: '邮箱验证' },
  },
  {
    path: '/',
    component: () => import('@/layouts/UserLayout.vue'),
    meta: { auth: true },
    children: [
      { path: '', name: 'home', component: () => import('@/views/user/Panel.vue') },
      {
        path: 'answer',
        name: 'answer-modes',
        component: () => import('@/views/user/AnswerModes.vue'),
        meta: { title: '练习模式' },
      },
      {
        path: 'answer/:mode',
        name: 'answer',
        component: () => import('@/views/user/Answer.vue'),
        meta: { title: '练习答题' },
      },
      {
        path: 'exam',
        name: 'exam-list',
        component: () => import('@/views/user/ExamList.vue'),
        meta: { title: '考试中心' },
      },
      {
        path: 'exam/:id',
        name: 'exam-taking',
        component: () => import('@/views/user/ExamTaking.vue'),
        meta: { title: '考试进行中' },
      },
      { path: 'wrong', name: 'wrong', component: () => import('@/views/user/Wrong.vue'), meta: { title: '错题本' } },
      { path: 'marks', name: 'marks', component: () => import('@/views/user/Marks.vue'), meta: { title: '我的标记' } },
      {
        path: 'profile',
        name: 'profile',
        component: () => import('@/views/user/Profile.vue'),
        meta: { title: '个人信息' },
      },
    ],
  },
  {
    path: '/admin',
    component: () => import('@/layouts/AdminLayout.vue'),
    meta: { auth: true, admin: true },
    children: [
      {
        path: '',
        name: 'admin-overview',
        component: () => import('@/views/admin/Overview.vue'),
        meta: { title: '概览面板' },
      },
      {
        path: 'users',
        name: 'admin-users',
        component: () => import('@/views/admin/Users.vue'),
        meta: { title: '用户管理' },
      },
      {
        path: 'groups',
        name: 'admin-groups',
        component: () => import('@/views/admin/Groups.vue'),
        meta: { title: '分组管理' },
      },
      {
        path: 'question-banks',
        name: 'admin-question-banks',
        component: () => import('@/views/admin/QuestionBanks.vue'),
        meta: { title: '题库管理' },
      },
      {
        path: 'questions',
        name: 'admin-questions',
        component: () => import('@/views/admin/Questions.vue'),
        meta: { title: '题目管理' },
      },
      {
        path: 'upload',
        name: 'admin-upload',
        component: () => import('@/views/admin/Upload.vue'),
        meta: { title: '上传题库' },
      },
      {
        path: 'exam-templates',
        name: 'admin-exam-templates',
        component: () => import('@/views/admin/ExamTemplates.vue'),
        meta: { title: '试卷模板' },
      },
      {
        path: 'exams',
        name: 'admin-exams',
        component: () => import('@/views/admin/Exams.vue'),
        meta: { title: '正式考试' },
      },
      {
        path: 'exam-records',
        name: 'admin-exam-records',
        component: () => import('@/views/admin/ExamRecords.vue'),
        meta: { title: '考试记录' },
      },
      {
        path: 'review',
        name: 'admin-review',
        component: () => import('@/views/admin/Review.vue'),
        meta: { title: '简答复核' },
      },
      {
        path: 'settings',
        name: 'admin-settings',
        component: () => import('@/views/admin/Settings.vue'),
        meta: { title: '基础设置' },
      },
      {
        path: 'audit',
        name: 'admin-audit',
        component: () => import('@/views/admin/Audit.vue'),
        meta: { title: '审计日志' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFound.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 路由切换回滚到顶部（长列表页跳转后不再停留在底部）
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()
  if (to.meta.guest && auth.isLoggedIn) return next({ name: 'home' })
  if (to.meta.auth && !auth.isLoggedIn) return next({ name: 'login' })
  // 管理端不以可被改写的 localStorage 角色做最终决定：进入前向服务端核对。
  // fetchMe 会把降权后的真实角色写回，避免人停在后台壳里连打 403。
  if (to.meta.admin) {
    try {
      await auth.fetchMe()
    } catch {
      return next({ name: 'login' })
    }
    if (!auth.isLoggedIn) return next({ name: 'login' })
    if (!auth.isAdmin) return next({ name: 'home' })
  }
  next()
})

// 页面标题：`页面名 · 站点名`（站点名来自站点设置，useTheme 加载完成后会再次同步）
router.afterEach((to) => {
  const site = useSiteStore()
  const page = to.meta.title as string | undefined
  document.title = page ? `${page} · ${site.site_name}` : site.site_name
})

export default router
