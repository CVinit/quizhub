<template>
  <div class="profile-page">
    <el-card>
      <h2>个人信息</h2>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="邮箱">{{ auth.user?.email }}</el-descriptions-item>
        <el-descriptions-item label="姓名">{{ auth.user?.name }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ roleLabel }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ auth.user?.status }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
    <el-card style="margin-top: 16px">
      <h2>修改密码</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" style="max-width: 420px">
        <el-form-item label="原密码" prop="old">
          <el-input v-model="form.old" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="new">
          <el-input v-model="form.new" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="onSubmit">提交</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, type FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api/auth'

const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ old: '', new: '' })
const rules = {
  old: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new: [{ required: true, min: 6, message: '新密码至少 6 位', trigger: 'blur' }],
}
const roleLabel = computed(() => ({ user: '普通用户', dept_admin: '部门管理员', super_admin: '超级管理员' }[auth.user?.role || 'user']))

const onSubmit = async () => {
  await formRef.value?.validate()
  loading.value = true
  try {
    await authApi.changePassword(form.old, form.new)
    ElMessage.success('密码修改成功')
    form.old = form.new = ''
  } finally {
    loading.value = false
  }
}
</script>
