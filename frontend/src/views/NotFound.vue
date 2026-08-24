<template>
  <div class="nf">
    <h1>404</h1>
    <p>页面不存在</p>
    <p class="sub">你访问的页面可能已被移除或地址有误</p>
    <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.nf { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 80vh; gap: 12px; }
.nf h1 { font-size: 64px; color: var(--brand-primary); margin: 0; }
.nf .sub { color: #909399; }
</style>
