<template>
  <div class="mock-config" v-loading="loading">
    <h2>模拟考试设置</h2>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 20px">
      模拟考试由用户自助发起，套用此处的默认规则即时组卷。修改后立即对新开考的模拟考试生效。
    </el-alert>

    <el-card>
      <el-form :model="config" label-width="120px">
        <el-form-item label="题型配比">
          <TypeQuotaEditor v-model="config.type_quota" :sources="quotaSources" :max-questions="config.max_questions" />
        </el-form-item>
        <el-form-item label="最大题数">
          <el-input-number v-model="config.max_questions" :min="1" :max="500" />
          <span class="tip">各题型配额之和不应超过最大题数</span>
        </el-form-item>
        <el-form-item label="出题顺序">
          <el-select v-model="config.order_mode" style="width: 100%">
            <el-option v-for="m in ORDER_MODES" :key="m.value" :label="m.label" :value="m.value">
              <span>{{ m.label }}</span>
              <span class="tip" style="float: right">{{ m.hint }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="允许重复抽题">
          <el-switch v-model="config.allow_duplicate" />
          <span class="tip">题库题量不足时是否允许重复抽取</span>
        </el-form-item>
        <el-form-item label="来源题库">
          <el-select v-model="config.bank_ids" multiple filterable clearable placeholder="留空则不限（全部题库）" style="width: 100%">
            <el-option v-for="b in banks" :key="b.id" :label="`${b.name}（${b.question_count} 题）`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源分组">
          <el-tree ref="treeRef" :data="groupTree" node-key="id"
            :props="{ label: 'name', children: 'children' }" show-checkbox
            :default-checked-keys="config.group_ids || []" @check="onGroupCheck" />
        </el-form-item>
        <el-form-item label="来源标签">
          <el-input v-model="tagInput" placeholder="逗号分隔，留空则不限" />
        </el-form-item>
      </el-form>
      <div class="actions">
        <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
        <el-button @click="load">重置</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { examApi } from '@/api/exam'
import { groupApi, type GroupNode } from '@/api/group'
import { questionApi, type QuestionBank } from '@/api/question'
import { DEFAULT_ORDER_MODE, ORDER_MODES } from '@/constants/paper'

const loading = ref(false)
const saving = ref(false)
const groupTree = ref<GroupNode[]>([])
const banks = ref<QuestionBank[]>([])
const treeRef = ref()
const tagInput = ref('')
const config = reactive<any>({ type_quota: {}, group_ids: [], bank_ids: [], tags: [], allow_duplicate: false, max_questions: 30, order_mode: DEFAULT_ORDER_MODE })

// 题型配比编辑器的来源条件：题库/分组直接取 config，标签来自输入框（watch 去重由组件负责）
const quotaSources = computed(() => ({
  bank_ids: config.bank_ids || [],
  group_ids: config.group_ids || [],
  tags: tagInput.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
}))

const onGroupCheck = () => {
  config.group_ids = treeRef.value?.getCheckedKeys(false) || []
}

const load = async () => {
  loading.value = true
  try {
    const [d, gt, bks] = await Promise.all([examApi.getMockConfig(), groupApi.tree(), questionApi.listBanks()])
    Object.assign(config, {
      type_quota: d.type_quota || {},
      group_ids: d.group_ids || [],
      bank_ids: d.bank_ids || [],
      tags: d.tags || [],
      allow_duplicate: d.allow_duplicate || false,
      max_questions: d.max_questions || 30,
      order_mode: d.order_mode || DEFAULT_ORDER_MODE,
    })
    tagInput.value = (d.tags || []).join(', ')
    groupTree.value = gt
    banks.value = bks
    treeRef.value?.setCheckedKeys(config.group_ids || [], false)
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const tags = tagInput.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    await examApi.saveMockConfig({ ...config, tags })
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.mock-config { max-width: 720px; }
.mock-config h2 { margin-bottom: 20px; }
.tip { color: #909399; font-size: 12px; margin-left: 8px; }
.actions { margin-top: 12px; }
</style>
