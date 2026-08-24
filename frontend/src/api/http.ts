import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
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

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    const msg = typeof detail === 'string' ? detail : '请求失败'
    if (status === 401) {
      const auth = useAuthStore()
      auth.clear()
      router.push('/login')
      ElMessage.error('登录已过期，请重新登录')
    } else {
      ElMessage.error(msg)
    }
    return Promise.reject(err)
  },
)

export default http

// 便捷封装：返回业务数据（已由拦截器剥离 .data）
export const api = {
  get: <T = any>(url: string, config?: any) => http.get<any, T>(url, config),
  post: <T = any>(url: string, data?: any, config?: any) => http.post<any, T>(url, data, config),
  put: <T = any>(url: string, data?: any, config?: any) => http.put<any, T>(url, data, config),
  delete: <T = any>(url: string, config?: any) => http.delete<any, T>(url, config),
}
