/**
 * 组卷出题顺序选项（与后端 paper_service.ORDER_MODES 保持一致）。
 *
 * bank    按题库导入顺序 —— 与 Excel 导入的行序一致
 * random  完全随机
 * grouped 按题型分组（单选→多选→判断…）
 */
export const ORDER_MODES = [
  { value: 'bank', label: '按题库导入顺序', hint: '与 Excel 导入的行序一致' },
  { value: 'random', label: '完全随机', hint: '抽中顺序即出题顺序' },
  { value: 'grouped', label: '按题型分组', hint: '同题型连在一起' },
] as const

export type OrderMode = (typeof ORDER_MODES)[number]['value']

export const DEFAULT_ORDER_MODE: OrderMode = 'bank'
