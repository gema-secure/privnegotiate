<script setup lang="ts">
import { yuan } from "../format";
import type { ChatMessage } from "../chat/derive";

/**
 * 纯消息气泡：只吃 props、无响应式依赖。
 * 组件边界即 memo 边界——props 引用不变时 Vue 跳过整个子树更新，
 * 这是轮询高频刷新下对话区性能的主防线。
 */
defineProps<{ message: ChatMessage }>();
</script>

<template>
  <div class="chat-row" :class="message.side">
    <div v-if="message.side === 'yi'" class="chat-mini-avatar" aria-hidden="true">议</div>
    <div class="chat-bubble" :class="{ notice: message.kind === 'notice' }">
      <span v-if="message.round" class="round-tag">第 {{ message.round }} 轮</span>
      <template v-if="message.pending">
        <span class="chat-typing"><i></i><i></i><i></i></span>
      </template>
      <template v-else>{{ message.text }}</template>

      <div v-if="message.card" class="chat-card">
        <div class="cap">提 案 v{{ message.card.version }}</div>
        <ol>
          <li v-for="(name, index) in message.card.names" :key="index">
            <span><span class="no">{{ String(index + 1).padStart(2, "0") }}</span>{{ name }}</span>
          </li>
        </ol>
        <div class="cost">
          <span>共 {{ message.card.count }} / {{ message.card.exactCount }} 项</span>
          <b>¥{{ yuan(message.card.totalCents) }}</b>
          <span>/ ¥{{ yuan(message.card.budgetCents) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
