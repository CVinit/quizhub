import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    /** 为 true 时响应拦截器不弹全局错误 toast，由调用方自行提示。 */
    skipErrorToast?: boolean
  }
}
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const http = axios.create({
  baseURL: '/api',
  timeout: 20000,
})

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

/** 从错误响应中提取可读信息。
 *
 * FastAPI 的校验错误（422）detail 是数组（[{loc, msg, type}]），
 * 旧实现只处理字符串，导致 422 一律显示"请求失败"、无法定位字段。
 */
export const extractErrorDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) => {
        if (typeof item === 'string') return item
        const loc = Array.isArray((item as { loc?: unknown[] })?.loc)
          ? (item as { loc: unknown[] }).loc.filter((x) => x !== 'body').join('.')
          : ''
        const msg = (item as { msg?: string })?.msg
        if (!msg) return ''
        return loc ? `${loc}: ${msg}` : msg
      })
      .filter(Boolean)
    return msgs.length ? msgs.join('；') : '请求参数不合法'
  }
  if (detail && typeof detail === 'object' && 'msg' in (detail as object)) {
    return String((detail as { msg: unknown }).msg)
  }
  return '请求失败'
}

/** 读取错误响应体里的可读信息。
 *
 * `responseType: 'blob'` 的请求（模板下载）失败时，axios 不会把响应体解析成 JSON，
 * `data.detail` 取不到、只能显示"请求失败"；这里先把 Blob 还原成文本再交给
 * extractErrorDetail 处理。
 */
const readErrorDetail = async (err: unknown): Promise<string> => {
  const data = (err as { response?: { data?: unknown } })?.response?.data
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text()) as { detail?: unknown }
      return extractErrorDetail(parsed?.detail)
    } catch {
      return '请求失败'
    }
  }
  return extractErrorDetail((data as { detail?: unknown } | undefined)?.detail)
}

/** 登录接口的 401 表示"凭据错误"，不是会话过期，不能按过期逻辑处理。 */
const CREDENTIAL_ENDPOINTS = ['/auth/login']

/**
 * 会话过期只处理一次。
 *
 * 页面常并行发多个请求（考试会话/详情、管理端列表），token 过期时它们会在同一 tick 内
 * 全部 401：此前每个请求都会弹一条相同的 toast 并再 push 一次 /login（后一次会取消前一次
 * 的跳转）。这里用模块级标记短路，登录成功后由 resetSessionExpired() 复位。
 */
let sessionExpired = false

/** 登录成功后调用：允许下一次会话过期重新提示与跳转。 */
export const resetSessionExpired = (): void => {
  sessionExpired = false
}

const handleSessionExpired = () => {
  if (sessionExpired) return
  sessionExpired = true
  const auth = useAuthStore()
  auth.clear()
  // 已在登录页时不再重复跳转（避免覆盖用户正在填写的表单）
  if (router.currentRoute.value.name !== 'login') router.push('/login')
  ElMessage.error('登录已过期，请重新登录')
}

http.interceptors.response.use(
  (res) => res.data,
  async (err) => {
    const status = err.response?.status
    const url: string = err.config?.url || ''
    const isCredentialError = CREDENTIAL_ENDPOINTS.some((p) => url.includes(p))
    if (status === 401 && !isCredentialError) {
      handleSessionExpired()
    } else if (!err.config?.skipErrorToast) {
      // 调用方声明自行提示时跳过：否则与业务层的「将在交卷前重试」叠成两条互相矛盾的 toast
      ElMessage.error(await readErrorDetail(err))
    }
    return Promise.reject(err)
  },
)

export default http

/** 读取 axios 错误的 HTTP 状态码（避免业务代码里 `catch (err: any)`）。 */
export const httpStatusOf = (err: unknown): number | undefined => {
  const status = (err as { response?: { status?: unknown } })?.response?.status
  return typeof status === 'number' ? status : undefined
}

/** 读取 axios 错误响应体里的 `detail`，由调用方按需收窄结构。 */
export const errorDetailOf = (err: unknown): unknown =>
  (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail

// 便捷封装：返回业务数据（已由拦截器剥离 .data）。
// 泛型默认 unknown（而不是 any）：未标注返回类型的调用点会保持“未知”，
// 需要读取字段时必须显式传入响应类型，避免 any 悄悄扩散到业务代码。
/** 本次请求失败时不弹全局 toast，由调用方自己说明原因与后续动作。 */
export const skipErrorToast = (): AxiosRequestConfig => ({ skipErrorToast: true })

export const api = {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => http.get<unknown, T>(url, config),
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    http.post<unknown, T>(url, data, config),
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    http.put<unknown, T>(url, data, config),
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => http.delete<unknown, T>(url, config),
}

/**
 * 下载后端生成的二进制文件（Excel 模板等）。
 *
 * 不能用 `window.open`/裸 `<a href>`：这些接口需要 Authorization 头，走 axios 取回 Blob
 * 再触发保存。此处集中实现，避免各 api 模块重复 createObjectURL/revoke 流程而修一处漏一处。
 */
export const downloadBlob = async (url: string, filename: string): Promise<void> => {
  const blob = await api.get<Blob>(url, { responseType: 'blob' })
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 立刻 revoke 在部分浏览器会中断下载，延后一拍释放更稳
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
}
