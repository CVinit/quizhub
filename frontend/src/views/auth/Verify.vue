<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2 class="title">邮箱验证</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" autocomplete="email" />
        </el-form-item>
        <el-form-item label="验证码" prop="code">
          <div class="code-row">
            <el-input v-model="form.code" placeholder="6 位验证码" autocomplete="one-time-code" />
            <el-button :loading="resending" :disabled="cd > 0" @click="onResend">
              {{ cd > 0 ? `${cd}s` : '重新发送' }}
            </el-button>
          </div>
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">激活账号</el-button>
        <div class="links"><router-link to="/login">返回登录</router-link></div>
      </el-form>
      <p class="hint">未配置 SMTP 时，验证码会打印在后端控制台日志。</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'

const route = useRoute()
const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const resending = ref(false)
const cd = ref(0)
const form = reactive({ email: '', code: '' })
const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }],
  code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
}

onMounted(() => {
  if (route.query.email) form.email = String(route.query.email)
})

const startCountdown = () => {
  cd.value = 60
  const t = setInterval(() => {
    cd.value -= 1
    if (cd.value <= 0) clearInterval(t)
  }, 1000)
}

const onResend = async () => {
  resending.value = true
  try {
    await authApi.resend(form.email)
    ElMessage.success('已重新发送验证码')
    startCountdown()
  } finally {
    resending.value = false
  }
}

const onSubmit = async () => {
  await formRef.value?.validate()
  loading.value = true
  try {
    await authApi.verify(form.email, form.code)
    ElMessage.success('验证成功，请登录')
    router.push('/login')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, var(--brand-primary-light-9), #f5f7fa); padding: 16px; }
.login-card { width: 380px; max-width: 100%; padding: 16px; border-top: 4px solid var(--brand-primary); }
.title { text-align: center; margin: 0 0 20px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.code-row .el-input { flex: 1; }
.links { margin-top: 12px; font-size: 13px; }
.links a { color: var(--brand-primary); }
.hint { color: #909399; font-size: 12px; margin: 12px 0 0; text-align: center; }
</style>
