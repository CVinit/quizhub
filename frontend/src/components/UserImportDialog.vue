<template>
  <el-dialog v-model="visible" title="批量导入用户" width="640px" @close="onClose">
    <el-steps :active="step" align-center finish-status="success">
      <el-step title="下载模板填写" />
      <el-step title="上传预览" />
      <el-step title="确认导入" />
    </el-steps>
    <div class="import-body">
      <!-- 步骤1：下载模板 + 选择文件 -->
      <template v-if="step === 0">
        <el-alert type="info" :closable="false" show-icon style="margin: 16px 0">
          请先下载模板，按格式填写用户信息后上传。
        </el-alert>
        <el-button type="primary" @click="onDownloadTemplate">下载用户导入模板</el-button>
        <div style="margin-top: 24px">
          <el-upload drag :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="onFileChange">
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽 .xlsx 文件到此处，或<em>点击上传</em></div>
          </el-upload>
        </div>
      </template>

      <!-- 步骤2：预览校验结果 -->
      <template v-if="step === 1 && preview">
        <el-alert
          :type="preview.valid_count > 0 ? 'success' : 'warning'"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
        >
          共解析 {{ preview.total }} 行，有效 {{ preview.valid_count }} 行。
        </el-alert>
        <el-table :data="preview.rows" border size="small" style="margin-top: 8px" max-height="320">
          <el-table-column prop="row_index" label="行" width="60" />
          <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
          <el-table-column prop="name" label="姓名" width="100" />
          <el-table-column label="角色" width="100">
            <template #default="{ row }">{{ roleLabel(String(row.role)) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">{{ statusLabel(String(row.status)) }}</template>
          </el-table-column>
          <el-table-column label="分组" width="100">
            <template #default="{ row }">{{ (row.group_ids || []).join(',') || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态校验" width="90">
            <template #default="{ row }">
              <el-tag :type="row.valid ? 'success' : 'danger'" size="small">{{ row.valid ? '有效' : '错误' }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-collapse v-if="preview.errors.length" style="margin-top: 12px">
          <el-collapse-item :title="`错误清单（${preview.errors.length}）`">
            <el-table :data="preview.errors" border size="small">
              <el-table-column prop="row" label="行号" width="70" />
              <el-table-column prop="email" label="邮箱" min-width="160" />
              <el-table-column prop="error" label="错误" />
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </template>

      <!-- 步骤3：导入结果 -->
      <template v-if="step === 2 && result">
        <el-result
          :icon="result.failed === 0 ? 'success' : 'warning'"
          :title="`导入完成：成功 ${result.success}，失败 ${result.failed}`"
        >
        </el-result>
        <el-collapse v-if="result.errors.length" style="margin-top: 12px">
          <el-collapse-item :title="`失败清单（${result.errors.length}）`">
            <el-table :data="result.errors" border size="small">
              <el-table-column prop="row" label="行号" width="70" />
              <el-table-column prop="email" label="邮箱" min-width="160" />
              <el-table-column prop="error" label="错误" />
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </template>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button v-if="step === 1" @click="reset">重新上传</el-button>
      <el-button
        v-if="step === 1"
        type="primary"
        :loading="importing"
        :disabled="!preview || preview.valid_count === 0"
        @click="onDoImport"
      >
        确认导入 {{ preview?.valid_count || 0 }} 个用户
      </el-button>
      <el-button v-if="step === 2" type="primary" @click="reset">继续导入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 批量导入用户（三步：下载模板 → 上传预览 → 确认导入）。
 *
 * 弹窗自带全部导入状态：父页只需 `v-model` 控制开关，
 * 并在 `imported` 时刷新列表，避免把导入流程的状态与用户列表状态混在一起。
 */
import { ref, watch } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { userApi, type UserImportPreview, type UserImportResult } from '@/api/user'
import { roleLabel, statusLabel } from '@/constants/user'

const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ imported: [] }>()

const step = ref(0)
const preview = ref<UserImportPreview | null>(null)
const result = ref<UserImportResult | null>(null)
const importing = ref(false)

/** 关闭或重新开始时清空上轮结果，避免下次打开看到旧数据。 */
const reset = () => {
  step.value = 0
  preview.value = null
  result.value = null
}

const onClose = () => reset()

// 打开时重置（关闭动画期间 el-dialog 仍会渲染，故用 watch 而不是 onMounted）
watch(visible, (v) => {
  if (v) reset()
})

const onDownloadTemplate = async () => {
  try {
    await userApi.downloadImportTemplate()
    ElMessage.success('模板已开始下载')
  } catch {
    /* http 拦截器已提示 */
  }
}

/**
 * 上传前校验：accept 属性只是浏览器建议，必须显式校验。
 * 体积上限由后端系统设置 `upload_max_size_mb` 判定（可配置，默认 10MB），前端不硬编码。
 */
const isValidImportFile = (file: File): boolean => {
  if (!/\.xlsx$/i.test(file.name)) {
    ElMessage.warning('仅支持 .xlsx 文件')
    return false
  }
  if (file.size === 0) {
    ElMessage.warning('文件内容为空')
    return false
  }
  return true
}

/**
 * 预览请求序号：解析耗时随文件变化，先选的大文件可能后返回并覆盖后选文件的预览与
 * confirm_token（界面不显示文件名，管理员察觉不到），点「确认导入」会导入错的人。
 */
let previewSeq = 0

const onFileChange = async (file: UploadFile) => {
  // 先清空上一次的预览与 token：旧值残留会让「确认导入」在解析失败/换文件后仍然可用
  preview.value = null
  if (!file.raw || !isValidImportFile(file.raw)) return
  const seq = ++previewSeq
  importing.value = true
  try {
    const res = await userApi.importPreview(file.raw)
    if (seq !== previewSeq) return
    preview.value = res
    step.value = 1
    ElMessage.success('解析完成')
  } catch {
    // el-upload 的 on-change 调用不经过 Vue 的事件错误包装，必须在此吞掉 rejection
    // （否则会产生真正的 unhandledrejection）；http 拦截器已提示原因
  } finally {
    importing.value = false
  }
}

const onDoImport = async () => {
  if (!preview.value) return
  importing.value = true
  try {
    result.value = await userApi.doImport(preview.value.confirm_token)
    step.value = 2
    ElMessage.success('导入完成')
    emit('imported')
  } catch {
    // 解析失败：http 拦截器已提示；停留在预览步骤，允许重试
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.import-body {
  min-height: 220px;
  margin-top: 8px;
}
</style>
