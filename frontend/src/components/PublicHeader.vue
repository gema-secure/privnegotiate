<script setup lang="ts">
import AppIcon from "./AppIcon.vue";
</script>

<template>
  <header class="home-header">
    <router-link to="/" class="brand" aria-label="和议，返回首页">
      <div class="brandmark"><AppIcon name="chat" :size="26" /></div>
      <div>
        <div class="brand-name">和议</div>
        <small>私密协商器 · HEYI</small>
      </div>
    </router-link>
    <router-link to="/" class="btn ghost">
      <AppIcon name="arrow" :size="16" style="transform: rotate(180deg)" />
      返回首页
    </router-link>
  </header>
</template>
