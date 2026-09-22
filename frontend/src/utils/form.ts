/**
 * Element Plus 表单校验的 Promise 封装。
 *
 * `FormInstance.validate()` 在未传入回调时校验失败会 reject。若直接 `await` 且不 catch，
 * 正常的“用户漏填”路径会冒泡成事件处理器异常（控制台报错）。这里统一折算为布尔值，
 * 调用方用 `if (!ok) return` 提前返回即可（字段级错误提示仍由 el-form-item 展示）。
 */
import type { FormInstance } from 'element-plus'

/** 校验表单：通过返回 true；未通过或无表单实例返回 false（不抛异常）。 */
export async function validateForm(form: FormInstance | undefined): Promise<boolean> {
  if (!form) return false
  try {
    await form.validate()
    return true
  } catch {
    return false
  }
}
