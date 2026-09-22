<template>
  <div>
    <div class="toolbar">
      <span class="title">上传题库</span>
      <el-button @click="onDownload">下载模板</el-button>
    </div>

    <el-card>
      <el-steps :active="step" align-center finish-status="success">
        <el-step title="选择所属分组/题库" />
        <el-step title="上传并预览" />
        <el-step title="确认导入" />
      </el-steps>

      <div class="step-body">
        <!-- 步骤1：选择分组与题库命名 -->
        <template v-if="step === 0">
          <el-form label-width="100px" style="max-width: 480px; margin-top: 24px">
            <el-form-item label="题库名称">
              <el-input v-model="form.bankName" placeholder="如：新员工入职安全培训题" style="width: 100%" />
              <div class="field-tip">本次上传的题目将自动归入此题库（留空则按时间自动命名）</div>
            </el-form-item>
            <el-form-item label="题库来源">
              <el-select v-model="form.bankId" clearable placeholder="选填：追加到已有题库" style="width: 100%">
                <el-option v-for="b in banks" :key="b.id" :label="bankLabel(b)" :value="b.id" />
              </el-select>
              <div class="field-tip">选择已有题库则追加；留空则新建上方命名的题库</div>
            </el-form-item>
            <el-form-item label="所属分组">
              <el-tree-select
                v-model="form.groupId"
                :data="groupTree"
                node-key="id"
                :props="{ label: 'name', children: 'children' }"
                clearable
                check-strictly
                placeholder="留空则题目不归属分组"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="step = 1">下一步</el-button>
            </el-form-item>
          </el-form>
        </template>

        <!-- 步骤2：上传预览 -->
        <template v-if="step === 1">
          <el-upload drag :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="onFileChange">
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽 .xlsx 文件到此处，或<em>点击上传</em></div>
          </el-upload>

          <div v-if="previewData" class="preview-box">
            <el-alert
              v-if="previewData.truncated"
              type="warning"
              :closable="false"
              show-icon
              style="margin-bottom: 12px"
            >
              文件行数超出单次导入上限，仅解析了前若干行，其余行未解析。
            </el-alert>
            <el-alert type="success" :closable="false" show-icon style="margin-bottom: 12px">
              共解析 {{ previewData.total }} 题，有效 {{ previewData.valid_count }} 题。
            </el-alert>
            <div class="dist">
              <el-tag v-for="(cnt, t) in previewData.type_dist" :key="t" style="margin-right: 8px">
                {{ t }}: {{ cnt }}
              </el-tag>
            </div>

            <el-table :data="previewData.rows" border size="small" style="margin-top: 12px">
              <el-table-column prop="row_index" label="行" width="60" />
              <el-table-column prop="type" label="题型" width="80" />
              <el-table-column prop="question" label="题干" min-width="200" show-overflow-tooltip />
              <el-table-column label="答案" width="120">
                <template #default="{ row }">{{ shortAnswer(row.answer) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.valid ? 'success' : 'danger'" size="small">{{
                    row.valid ? '有效' : '错误'
                  }}</el-tag>
                </template>
              </el-table-column>
            </el-table>

            <el-collapse v-if="previewData.errors.length" style="margin-top: 12px">
              <el-collapse-item :title="`错误清单（${previewData.errors.length}）`">
                <el-table :data="previewData.errors" border size="small">
                  <el-table-column prop="sheet" label="Sheet" width="100" />
                  <el-table-column prop="row" label="行号" width="70" />
                  <el-table-column prop="error" label="错误" />
                </el-table>
              </el-collapse-item>
            </el-collapse>

            <div class="step-actions">
              <el-button @click="onBack">上一步</el-button>
              <el-button
                type="primary"
                :loading="importing"
                :disabled="previewData.valid_count === 0"
                @click="onImport"
              >
                确认导入 {{ previewData.valid_count }} 题
              </el-button>
            </div>
          </div>
        </template>

        <!-- 步骤3：结果 -->
        <template v-if="step === 2">
          <el-result :icon="resultIcon" :title="resultTitle" :sub-title="resultSub">
            <template #extra>
              <el-button type="primary" @click="onReset">继续上传</el-button>
              <el-button @click="$router.push('/admin/questions')">查看题目列表</el-button>
            </template>
          </el-result>
        </template>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadApi, questionApi, type QuestionBank, type UploadPreview } from '@/api/question'
import type { QuestionAnswer } from '@/api/practice'
import { groupApi, type GroupNode } from '@/api/group'
import { bankLabel } from '@/utils/format'

const step = ref(0)
const groupTree = ref<GroupNode[]>([])
const banks = ref<QuestionBank[]>([])
const form = reactive({ groupId: null as number | null, bankId: null as number | null, bankName: '' })
const previewData = ref<UploadPreview | null>(null)
const importing = ref(false)
const resultIcon = ref<'success' | 'error'>('success')
const resultTitle = ref('')
const resultSub = ref('')

const onDownload = async () => {
  try {
    await uploadApi.downloadTemplate()
    ElMessage.success('模板已开始下载')
  } catch {
    /* http 拦截器已提示 */
  }
}

/**
 * 上传前校验：accept 属性只是浏览器建议，必须显式校验。
 * 体积上限由后端系统设置 `upload_max_size_mb` 判定（可配置，默认 10MB），
 * 前端不硬编码，避免管理员调高上限后反而被前端拦下。
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

/** 预览请求序号：解析耗时随文件大小变化，先发的大文件可能后返回并覆盖新文件的预览与
 * confirm_token（界面不显示文件名，管理员无法察觉），点「确认导入」会导入错的文件。 */
let previewSeq = 0

const onFileChange = async (file: UploadFile) => {
  // 先清空上一次的预览：否则重新选了不合法/解析失败的文件时，旧预览与旧 confirm_token
  // 仍然留在界面上（「确认导入」可用），管理员会以为导入的是新文件、实际导入旧文件。
  previewData.value = null
  const seq = ++previewSeq
  if (!file.raw || !isValidImportFile(file.raw)) return
  try {
    const res = await uploadApi.preview(file.raw, form.groupId, form.bankId, form.bankName)
    if (seq !== previewSeq) return
    previewData.value = res
    ElMessage.success('解析完成')
  } catch {
    /* http 拦截器已提示 */
  }
}

const onImport = async () => {
  if (!previewData.value) return
  importing.value = true
  try {
    const res = await uploadApi.doImport(previewData.value.confirm_token)
    resultIcon.value = 'success'
    resultTitle.value = '导入完成'
    resultSub.value = `成功 ${res.success} 题，失败 ${res.failed} 题`
    // 只有真正拿到导入结果才清空预览并进入结果页：网络抖动导致失败时仍可重试同一个
    // confirm_token，否则管理员会重新上传并重复导入。
    previewData.value = null
    step.value = 2
  } catch {
    // 失败停在预览步：confirm_token 还在，管理员可直接重试，不必重新上传再导入一遍
  } finally {
    importing.value = false
  }
}

// 返回上一步必须清空预览：confirm_token 绑定的是预览时选定的分组/题库，
// 否则改了分组/题库再点「下一步」会展示旧预览并导入到旧目标。
const onBack = () => {
  step.value = 0
  previewData.value = null
}

const onReset = () => {
  step.value = 0
  previewData.value = null
  form.groupId = null
  form.bankId = null
  form.bankName = ''
}

const shortAnswer = (a: QuestionAnswer) => {
  if (a == null) return ''
  if (typeof a === 'string') return a.length > 20 ? a.slice(0, 20) + '…' : a
  return JSON.stringify(a).slice(0, 20) + '…'
}

onMounted(async () => {
  try {
    const [tree, bankList] = await Promise.all([groupApi.tree(), questionApi.listBanks()])
    groupTree.value = tree
    banks.value = bankList
  } catch {
    // 下拉数据加载失败：http 拦截器已提示；仍可先选“新建题库”上传
  }
})
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.step-body {
  padding: 24px 0;
  min-height: 280px;
}
.preview-box {
  margin-top: 24px;
}
.dist {
  margin-top: 8px;
}
.step-actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
@media (max-width: 767px) {
  .step-body {
    padding: 16px 0;
  }
  .step-actions .el-button {
    flex: 1;
  }
  /* 100px 固定 label 在手机上挤压输入区，改为上置标签 */
  .step-body :deep(.el-form-item__label) {
    display: block;
    text-align: left;
    width: 100% !important;
    padding-bottom: 4px;
  }
  .step-body :deep(.el-form-item) {
    display: block;
  }
  .step-body :deep(.el-form-item__content) {
    margin-left: 0 !important;
  }
}
</style>
