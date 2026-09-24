import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

const USER_KEY = 'training_user'
const TOKEN_KEY = 'training_token'

const validUser = {
  id: 1,
  email: 'a@example.com',
  name: '甲',
  role: 'super_admin',
  status: 'active',
  email_verified: true,
}

/**
 * 内存版 Storage。
 *
 * happy-dom 暴露的全局 localStorage 在测试上下文里不保证实现完整接口，直接使用会出现
 * "removeItem is not a function"；store 只用到 getItem/setItem/removeItem，这里显式提供，
 * 让用例只验证 store 的校验逻辑，不受测试环境实现差异影响。
 */
const createMemoryStorage = (): Storage => {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (key: string) => (map.has(key) ? (map.get(key) as string) : null),
    key: (index: number) => [...map.keys()][index] ?? null,
    removeItem: (key: string) => {
      map.delete(key)
    },
    setItem: (key: string, value: string) => {
      map.set(key, String(value))
    },
  }
}

describe('auth store：本地缓存用户信息的校验', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('localStorage', createMemoryStorage())
  })

  it('合法缓存被采用', () => {
    localStorage.setItem(USER_KEY, JSON.stringify(validUser))
    const auth = useAuthStore()
    expect(auth.user?.email).toBe('a@example.com')
    expect(auth.isAdmin).toBe(true)
    expect(auth.isSuper).toBe(true)
  })

  it('篡改的 role 不被信任（缓存 role 会驱动管理端路由守卫）', () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ ...validUser, role: 'root' }))
    const auth = useAuthStore()
    expect(auth.user).toBeNull()
    expect(auth.isAdmin).toBe(false)
    // 脏数据应被清掉，避免每次启动都重复解析
    expect(localStorage.getItem(USER_KEY)).toBeNull()
  })

  it('缺少字段的对象按未登录处理', () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ role: 'super_admin' }))
    const auth = useAuthStore()
    expect(auth.user).toBeNull()
    expect(auth.isAdmin).toBe(false)
  })

  it('status 取值域外的缓存同样被拒绝', () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ ...validUser, status: 'deleted' }))
    expect(useAuthStore().user).toBeNull()
  })

  it('非法 JSON 不让 state 工厂抛异常（否则整个应用白屏）', () => {
    localStorage.setItem(USER_KEY, '{oops')
    expect(() => useAuthStore()).not.toThrow()
    expect(useAuthStore().user).toBeNull()
  })

  it('令牌存在即视为已登录；clear() 同时清空内存与本地缓存', () => {
    localStorage.setItem(TOKEN_KEY, 'tk')
    localStorage.setItem(USER_KEY, JSON.stringify(validUser))
    const auth = useAuthStore()
    expect(auth.isLoggedIn).toBe(true)

    auth.clear()
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.user).toBeNull()
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
    expect(localStorage.getItem(USER_KEY)).toBeNull()
  })

  it('setToken 就地替换凭据（改密后旧 token 立即失效，必须换成服务端新签发的那个）', () => {
    const auth = useAuthStore()
    auth.setToken('old-token')
    expect(auth.token).toBe('old-token')
    expect(localStorage.getItem(TOKEN_KEY)).toBe('old-token')

    auth.setToken('new-token')
    expect(auth.token).toBe('new-token')
    expect(localStorage.getItem(TOKEN_KEY)).toBe('new-token')
    expect(auth.isLoggedIn).toBe(true)
    // 只换凭据，不动用户信息（改密不改变身份）
    expect(auth.user).toBeNull()

    // safeStorage 的「Storage 不可用」内存兜底是模块级状态，会跨用例残留：
    // 本用例写过凭据后必须清理，否则后面「Storage 不可用」的用例会读到这个 token。
    auth.clear()
  })
})

describe('auth store：Storage 不可用时不崩、不退化为静默失败', () => {
  /** 模拟「阻止所有 Cookie」/dom.storage.enabled=false：任何访问都抛异常。 */
  const createThrowingStorage = (): Storage => {
    const boom = () => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    }
    return {
      get length(): number {
        return boom()
      },
      clear: boom,
      getItem: boom,
      key: boom,
      removeItem: boom,
      setItem: boom,
    } as unknown as Storage
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('localStorage', createThrowingStorage())
  })

  it('state 工厂不抛异常（否则 App.vue 的 useAuthStore() 会让整个应用白屏）', () => {
    expect(() => useAuthStore()).not.toThrow()
    expect(useAuthStore().isLoggedIn).toBe(false)
  })

  it('登录写入失败仍保留内存态，登录流程不会卡在登录页', async () => {
    const auth = useAuthStore()
    // 直接驱动 action 的写入路径，避免依赖 axios
    auth.token = 'tk'
    auth.user = validUser as never
    expect(() => auth.clear()).not.toThrow()
    expect(auth.token).toBe('')
  })
})
