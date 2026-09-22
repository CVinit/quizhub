import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import http, { errorDetailOf, extractErrorDetail, httpStatusOf, resetSessionExpired } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

describe('extractErrorDetail', () => {
  it('字符串直接返回（后端 HTTPException detail）', () => {
    expect(extractErrorDetail('邮箱或密码错误')).toBe('邮箱或密码错误')
  })

  it('FastAPI 422 数组：带上字段路径并去掉 body 前缀', () => {
    expect(extractErrorDetail([{ loc: ['body', 'email'], msg: '不是有效邮箱' }])).toBe('email: 不是有效邮箱')
  })

  it('多个校验错误用分号连接', () => {
    const detail = [
      { loc: ['body', 'a'], msg: '甲' },
      { loc: ['body', 'b'], msg: '乙' },
    ]
    expect(extractErrorDetail(detail)).toBe('a: 甲；b: 乙')
  })

  it('数组元素为字符串时直接使用', () => {
    expect(extractErrorDetail(['出错了'])).toBe('出错了')
  })

  it('空数组给兜底文案，而不是空字符串', () => {
    expect(extractErrorDetail([])).toBe('请求参数不合法')
  })

  it('对象形态取 msg', () => {
    expect(extractErrorDetail({ msg: '出错了' })).toBe('出错了')
  })

  it('未知形态兜底为「请求失败」', () => {
    expect(extractErrorDetail(undefined)).toBe('请求失败')
    expect(extractErrorDetail(null)).toBe('请求失败')
    expect(extractErrorDetail(42)).toBe('请求失败')
  })
})

describe('httpStatusOf / errorDetailOf', () => {
  it('读取 axios 错误的状态码与 detail', () => {
    const err = { response: { status: 409, data: { detail: { code: 'exam_reset_required' } } } }
    expect(httpStatusOf(err)).toBe(409)
    expect(errorDetailOf(err)).toEqual({ code: 'exam_reset_required' })
  })

  it('非 axios 错误安全返回 undefined（不抛异常）', () => {
    expect(httpStatusOf(new Error('boom'))).toBeUndefined()
    expect(httpStatusOf(undefined)).toBeUndefined()
    expect(errorDetailOf('nope')).toBeUndefined()
    expect(errorDetailOf(null)).toBeUndefined()
  })
})

/**
 * 响应拦截器：401 分支与 blob 错误解析。
 *
 * 这两条正是本模块存在的理由（此前 401 一律按"会话过期"处理、blob 请求失败只能显示
 * "请求失败"），但原先没有任何用例覆盖，回归会静默上线。
 */
describe('http 响应拦截器', () => {
  /** 捕获拦截器弹出的提示文案，避免依赖 Element Plus 的 DOM 渲染。 */
  let messages: string[] = []

  beforeEach(async () => {
    setActivePinia(createPinia())
    // 复位模块级的"已提示过期"标记，否则用例之间会互相影响
    resetSessionExpired()
    messages = []
    const { ElMessage } = await import('element-plus')
    vi.spyOn(ElMessage, 'error').mockImplementation(((opts: unknown) => {
      messages.push(typeof opts === 'string' ? opts : String((opts as { message?: string })?.message ?? ''))
      return {} as never
    }) as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  /** 让 http 的适配器以给定的错误响应 reject。 */
  const failWith = (err: unknown) => {
    http.defaults.adapter = vi.fn().mockRejectedValue(err)
    return http.get('/whatever').catch((e: unknown) => e)
  }

  it('登录接口的 401 按凭据错误处理：展示后端原因，不清空会话、不跳转', async () => {
    const auth = useAuthStore()
    auth.token = 'tk'
    const pushSpy = vi.spyOn(router, 'push').mockResolvedValue(undefined as never)

    await failWith({ config: { url: '/auth/login' }, response: { status: 401, data: { detail: '邮箱或密码错误' } } })

    expect(auth.token).toBe('tk')
    expect(pushSpy).not.toHaveBeenCalled()
    expect(messages).toEqual(['邮箱或密码错误'])
  })

  it('其它接口的 401 清空会话、跳转登录页并提示', async () => {
    const auth = useAuthStore()
    auth.token = 'tk'
    const pushSpy = vi.spyOn(router, 'push').mockResolvedValue(undefined as never)

    await failWith({ config: { url: '/auth/me' }, response: { status: 401, data: { detail: '未认证' } } })

    expect(auth.token).toBe('')
    expect(pushSpy).toHaveBeenCalledWith('/login')
    expect(messages).toEqual(['登录已过期，请重新登录'])
  })

  it('同一会话内并发 401 只提示与跳转一次', async () => {
    const auth = useAuthStore()
    auth.token = 'tk'
    const pushSpy = vi.spyOn(router, 'push').mockResolvedValue(undefined as never)
    http.defaults.adapter = vi.fn().mockRejectedValue({
      config: { url: '/exams/available' },
      response: { status: 401, data: {} },
    })

    await Promise.allSettled([http.get('/a'), http.get('/b'), http.get('/c')])

    expect(pushSpy).toHaveBeenCalledTimes(1)
    expect(messages).toEqual(['登录已过期，请重新登录'])
  })

  it('blob 请求失败时把响应体里的 detail 提示出来，而不是"请求失败"', async () => {
    // axios 对 responseType:'blob' 不会把响应体解析成 JSON，data 是 Blob；
    // 拦截器必须先把 Blob 还原成文本，否则模板下载失败只能显示"请求失败"。
    const blob = new Blob([JSON.stringify({ detail: '模板生成失败' })], { type: 'application/json' })
    http.defaults.adapter = vi.fn().mockRejectedValue({
      config: { url: '/admin/users/import-template' },
      response: { status: 500, data: blob },
    })

    await http.get('/admin/users/import-template', { responseType: 'blob' }).catch(() => undefined)

    expect(messages).toEqual(['模板生成失败'])
  })

  it('非 blob 的普通错误按 detail 字符串提示', async () => {
    await failWith({ config: { url: '/x' }, response: { status: 400, data: { detail: '名称不能为空' } } })
    expect(messages).toEqual(['名称不能为空'])
  })
})
