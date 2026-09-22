/**
 * ElMessageBox 的 Promise 封装。
 *
 * `ElMessageBox.confirm/prompt` 在用户点“取消/关闭”时返回 reject 的 Promise。
 * 直接 `await` 会让正常的取消路径冒泡成事件处理器异常（Vue 记录
 * “Unhandled error during execution of native event handler”），污染控制台与监控。
 * 这里统一把取消折算为 `false` / `null`，交由调用方用普通分支处理。
 */
import { ElMessageBox, type ElMessageBoxOptions } from 'element-plus'

/** 确认框：确认返回 true，取消/关闭返回 false（不再抛异常）。 */
export async function confirmBox(message: string, title: string, options: ElMessageBoxOptions = {}): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning', ...options })
    return true
  } catch {
    return false
  }
}

/** 输入框：确认返回输入值，取消/关闭返回 null。 */
export async function promptBox(
  message: string,
  title: string,
  options: ElMessageBoxOptions = {},
): Promise<string | null> {
  try {
    const { value } = await ElMessageBox.prompt(message, title, options)
    return value
  } catch {
    return null
  }
}

/** 提示框：点击确认/关闭按钮或 ESC 均视为“已读”，不再抛异常。 */
export async function alertBox(message: string, title: string, options: ElMessageBoxOptions = {}): Promise<void> {
  try {
    await ElMessageBox.alert(message, title, options)
  } catch {
    /* 用户通过关闭按钮/ESC 关闭，无后续动作 */
  }
}
