<template>
  <div class="login-page">
    <el-card class="login-card">
      <LogoMark :size="48" :radius="10" class="brand-mark" />
      <h2 class="title">登录 {{ site.site_name }}</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">登录</el-button>
        <div class="links">
          <router-link to="/register">注册账号</router-link>
          <router-link to="/verify">重新发送验证码</router-link>
        </div>
      </el-form>
      <p class="hint">默认超管：admin@example.com / admin12345</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'

const router = useRouter()
const auth = useAuthStore()
const site = useSiteStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ email: '', password: '' })
const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const onSubmit = async () => {
  await formRef.value?.validate()
  loading.value = true
  try {
    await auth.login(form.email, form.password)
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--brand-primary-light-9), #f5f7fa);
  padding: 16px;
}
.brand-mark { margin: 0 auto 12px; }
.login-card { width: 380px; max-width: 100%; padding: 16px; border-top: 4px solid var(--brand-primary); }
.title { text-align: center; margin: 0 0 20px; }
.links { display: flex; justify-content: space-between; margin-top: 12px; font-size: 13px; }
.links a { color: var(--brand-primary); }
.hint { color: #909399; font-size: 12px; margin: 12px 0 0; text-align: center; }
</style>
