<template>
  <div class="placeholder">
    <el-empty :description="`「${name}」功能开发中`">
      <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
    </el-empty>
  </div>
</template>

<script setup lang="ts">
defineProps<{ name?: string }>()
</script>

<style scoped>
.placeholder { display: flex; justify-content: center; padding: 60px 0; }
</style>
