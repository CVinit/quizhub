/**
 * 题型定义（顺序与后端 question_service.QUESTION_TYPES 保持一致）。
 *
 * 此前多处各自硬编码同一份数组（题库管理、答题模式、我的标记等），
 * 新增/调整题型时容易漏改，这里统一收敛。
 */
export const QUESTION_TYPES = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题'] as const

/** 模拟考试题量档位（与后端 exam_service.MOCK_SIZE_PRESETS 一致，仅作首屏兜底）。 */
export const MOCK_SIZE_PRESETS = [10, 20, 30, 50, 100] as const

/** 模拟考试默认题量。 */
export const MOCK_DEFAULT_SIZE = 30
