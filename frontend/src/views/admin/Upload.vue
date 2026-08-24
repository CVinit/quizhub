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
                <el-option v-for="b in banks" :key="b.id" :label="`${b.name}（${b.question_count} 题）`" :value="b.id" />
              </el-select>
              <div class="field-tip">选择已有题库则追加；留空则新建上方命名的题库</div>
            </el-form-item>
            <el-form-item label="所属分组">
              <el-tree-select v-model="form.groupId" :data="groupTree" node-key="id"
                :props="{ label: 'name', children: 'children' }" clearable check-strictly
                placeholder="留空则题目不归属分组" style="width: 100%" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="step = 1">下一步</el-button>
            </el-form-item>
          </el-form>
        </template>

        <!-- 步骤2：上传预览 -->
        <template v-if="step === 1">
          <el-upload
            drag :auto-upload="false" :show-file-list="false" accept=".xlsx"
            :on-change="onFileChange">
            <el-icon class="el-icon--upload"><upload-filled /></el-icon>
            <div class="el-upload__text">拖拽 .xlsx 文件到此处，或<em>点击上传</em></div>
          </el-upload>

          <div v-if="previewData" class="preview-box">
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
                  <el-tag :type="row.valid ? 'success' : 'danger'" size="small">{{ row.valid ? '有效' : '错误' }}</el-tag>
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
              <el-button @click="step = 0">上一步</el-button>
              <el-button type="primary" :loading="importing" :disabled="previewData.valid_count === 0" @click="onImport">
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
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadApi, questionApi, type QuestionBank } from '@/api/question'
import { groupApi, type GroupNode } from '@/api/group'

const step = ref(0)
const groupTree = ref<GroupNode[]>([])
const banks = ref<QuestionBank[]>([])
const form = reactive({ groupId: null as number | null, bankId: null as number | null, bankName: '' })
const previewData = ref<any>(null)
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

const onFileChange = async (file: any) => {
  if (!file.raw) return
  try {
    previewData.value = await uploadApi.preview(file.raw, form.groupId, form.bankId, form.bankName)
    ElMessage.success('解析完成')
  } catch {
    /* http 拦截器已提示 */
  }
}

const onImport = async () => {
  importing.value = true
  try {
    const res = await uploadApi.doImport(previewData.value.confirm_token)
    resultIcon.value = 'success'
    resultTitle.value = '导入完成'
    resultSub.value = `成功 ${res.success} 题，失败 ${res.failed} 题`
    step.value = 2
  } catch {
    resultIcon.value = 'error'
    resultTitle.value = '导入失败'
    resultSub.value = '请检查文件后重试'
    step.value = 2
  } finally {
    importing.value = false
  }
}

const onReset = () => {
  step.value = 0
  previewData.value = null
  form.groupId = null
  form.bankId = null
  form.bankName = ''
}

const shortAnswer = (a: any) => {
  if (a == null) return ''
  if (typeof a === 'string') return a.length > 20 ? a.slice(0, 20) + '…' : a
  if (Array.isArray(a)) return JSON.stringify(a).slice(0, 20) + '…'
  return JSON.stringify(a).slice(0, 20) + '…'
}

onMounted(async () => {
  groupTree.value = await groupApi.tree()
  banks.value = await questionApi.listBanks()
})
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.step-body { padding: 24px 0; min-height: 280px; }
.preview-box { margin-top: 24px; }
.dist { margin-top: 8px; }
.step-actions { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 767px) {
  .step-body { padding: 16px 0; }
  .step-actions .el-button { flex: 1; }
}
</style>
