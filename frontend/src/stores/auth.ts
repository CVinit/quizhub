import { defineStore } from 'pinia'
import http, { resetSessionExpired } from '@/api/http'

export interface UserInfo {
  id: number
  email: string
  name: string
  role: 'user' | 'dept_admin' | 'super_admin'
  status: 'active' | 'pending' | 'disabled'
  email_verified: boolean
}

interface LoginResp {
  access_token: string
  token_type: string
  user: UserInfo
}

const TOKEN_KEY = 'training_token'
const USER_KEY = 'training_user'

const ROLES: readonly UserInfo['role'][] = ['user', 'dept_admin', 'super_admin']
const STATUSES: readonly UserInfo['status'][] = ['active', 'pending', 'disabled']

/** 只用到这三个方法，便于在 Storage 不可用时替换为内存实现。 */
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

/** Storage 不可用时的内存兜底：本次会话内登录态仍可用，只是刷新后丢失。 */
const memory = new Map<string, string>()

/**
 * 受保护的 Storage 访问。
 *
 * 站点数据被禁用时（Safari「阻止所有 Cookie」、Firefox `dom.storage.enabled=false`、
 * 沙箱 iframe），连 `getItem` 都会抛 SecurityError/QuotaExceededError。此前 state 工厂里
 * 直接调用 localStorage：一旦抛出，异常会逃出 Pinia 的 state 工厂，App.vue setup 中的
 * useAuthStore() 随之抛错，应用永不挂载（白屏，用户无法自救）；catch 分支里的 removeItem
 * 还会二次抛出。登录路径同理——setItem 抛错会让 router.push 不执行且被静默吞掉。
 *
 * 这里逐次 try/catch（而非模块加载时探测一次）：写入时同步进内存，Storage 不可用时读回
 * 内存值；可用时以 Storage 为准。探测放在模块顶层会在测试 stub 全局 localStorage 之前
 * 执行，导致拿不到替身实现。
 */
const safeStorage: StorageLike = {
  getItem: (key: string) => {
    try {
      return localStorage.getItem(key)
    } catch {
      return memory.get(key) ?? null
    }
  },
  setItem: (key: string, value: string) => {
    memory.set(key, value)
    try {
      localStorage.setItem(key, value)
    } catch {
      /* 站点数据被禁用：忽略，值已写入内存兜底 */
    }
  },
  removeItem: (key: string) => {
    memory.delete(key)
    try {
      localStorage.removeItem(key)
    } catch {
      /* 同上 */
    }
  },
}

/** 校验缓存对象的结构与取值域：localStorage 可被任意脚本改写，不能被信任。 */
const isValidUser = (value: unknown): value is UserInfo => {
  if (!value || typeof value !== 'object') return false
  const u = value as Partial<Record<keyof UserInfo, unknown>>
  return (
    typeof u.id === 'number' &&
    typeof u.email === 'string' &&
    typeof u.name === 'string' &&
    typeof u.email_verified === 'boolean' &&
    typeof u.role === 'string' &&
    ROLES.includes(u.role as UserInfo['role']) &&
    typeof u.status === 'string' &&
    STATUSES.includes(u.status as UserInfo['status'])
  )
}

/**
 * 读取本地缓存的用户信息。
 *
 * localStorage 里的值可能被截断、被篡改或来自旧版本结构；直接 JSON.parse 会抛异常，
 * 而本函数运行在 Pinia 的 state 工厂中——一处脏数据就会导致整个应用挂载失败（白屏，
 * 用户无法通过界面自救）。因此失败时清掉缓存并回退为未登录。
 *
 * 这里还校验字段与 role/status 取值域：缓存里的 role 会驱动 isAdmin（以及前端路由的
 * 管理端守卫），未校验的 `{"role":"super_admin"}` 就能让任意人看到管理界面。
 */
const readStoredUser = (): UserInfo | null => {
  try {
    const raw = safeStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (isValidUser(parsed)) return parsed
    safeStorage.removeItem(USER_KEY)
    return null
  } catch {
    safeStorage.removeItem(USER_KEY)
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: safeStorage.getItem(TOKEN_KEY) || '',
    user: readStoredUser(),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'dept_admin' || s.user?.role === 'super_admin',
    isSuper: (s) => s.user?.role === 'super_admin',
  },
  actions: {
    async login(email: string, password: string) {
      const data = await http.post<unknown, LoginResp>('/auth/login', { username: email, password })
      this.token = data.access_token
      this.user = data.user
      safeStorage.setItem(TOKEN_KEY, this.token)
      safeStorage.setItem(USER_KEY, JSON.stringify(this.user))
      // 重新登录成功后复位「会话已过期」标记，否则本次会话后续再过期将不再提示与跳转
      resetSessionExpired()
    },
    async fetchMe() {
      const u = await http.get<unknown, UserInfo>('/auth/me')
      // 停用/待审批账号即使持有旧 token 也不应继续停留在已登录界面
      if (u.status !== 'active') {
        this.clear()
        return
      }
      this.user = u
      safeStorage.setItem(USER_KEY, JSON.stringify(u))
    },
    clear() {
      this.token = ''
      this.user = null
      safeStorage.removeItem(TOKEN_KEY)
      safeStorage.removeItem(USER_KEY)
    },
    /** 就地替换本地凭据：用于服务端**重新签发** token 的场景（如改密后旧 token 立即失效）。 */
    setToken(token: string) {
      this.token = token
      safeStorage.setItem(TOKEN_KEY, token)
    },
    async logout() {
      this.clear()
    },
  },
})
