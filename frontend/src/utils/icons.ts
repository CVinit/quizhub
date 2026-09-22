// 统一图标导出（Element Plus Icons）
// 只保留本模块实际被引用的图标；其余页面直接从 @element-plus/icons-vue 按需引入，
// 避免这里堆积无人使用的再导出（tree-shaking 虽能处理，但会误导后续维护者）。
export { Document, Refresh, Files, Warning, Collection } from '@element-plus/icons-vue'
