/**
 * 展示格式化工具。
 *
 * 此前 `fmt` 在 Exams.vue / Audit.vue / ExamList.vue 各写一份、Review.vue 又内联一份，
 * 其中若干份缺少「非法日期回退原串」的保护；统一收敛到这里。
 */

/**
 * ISO-8601 时间串 → `YYYY/M/D HH:mm:ss`（本地时区）。
 *
 * 无偏移的字符串（如管理端日期选择器提交的考试时段）按本地时间解析：
 * 后端 `_parse_time` 对无偏移值同样按业务本地时区（TRAINING_TZ）解析，两侧口径一致。
 * 纯日期串（`2024-01-02`）按规范会被当成 UTC 零点，这里补上本地时间再解析，
 * 否则在东八区会显示成当天 08:00，与「按本地时间解析」的约定不符。
 * 无法解析时原样返回，避免显示 `Invalid Date`。
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso
  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 题库下拉标签：`名称（N 题）`。
 *
 * 抽题/组卷/导入等多处都要展示同一口径；`question_count` 在接口里是可选字段，
 * 缺失时只显示名称，避免渲染出 `undefined 题`。
 */
export function bankLabel(bank: { name: string; question_count?: number | null }): string {
  return typeof bank.question_count === 'number' ? `${bank.name}（${bank.question_count} 题）` : bank.name
}
