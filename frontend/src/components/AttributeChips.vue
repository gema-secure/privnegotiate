<script setup lang="ts">
import { computed } from "vue";
import type { AttributeSource, AttributeState } from "../types";

interface ChipAttribute {
  key: string;
  state: AttributeState;
  source?: AttributeSource;
}

const props = withDefaults(
  defineProps<{
    attributes?: ChipAttribute[] | null;
    showSource?: boolean;
    compact?: boolean;
    max?: number;
  }>(),
  { attributes: null, showSource: false, compact: false, max: 0 },
);

const STATE_LABEL: Record<AttributeState, string> = {
  present: "含",
  absent: "不含",
  unknown: "未知",
};

const SOURCE_LABEL: Record<AttributeSource, string> = {
  import: "导入",
  manual: "人工",
  correction: "修正",
};

const list = computed(() => props.attributes ?? []);
const visible = computed(() =>
  props.max > 0 ? list.value.slice(0, props.max) : list.value,
);
const hidden = computed(() => Math.max(0, list.value.length - visible.value.length));
</script>

<template>
  <div class="attr-chips" :class="{ compact }" :aria-label="list.length ? '菜品三态属性' : '菜品属性未标注'">
    <span
      v-for="attr in visible"
      :key="attr.key"
      class="attr-chip"
      :class="attr.state"
      :title="`${attr.key}：${STATE_LABEL[attr.state]}${attr.source ? ` · 来源 ${SOURCE_LABEL[attr.source]}` : ''}${attr.state === 'unknown' ? '（未知不会被当作不含）' : ''}`"
    >
      <b>{{ attr.key }}</b>
      <i>{{ STATE_LABEL[attr.state] }}</i>
      <em v-if="showSource && attr.source">{{ SOURCE_LABEL[attr.source] }}</em>
    </span>
    <span v-if="hidden > 0" class="attr-chip more">+{{ hidden }}</span>
    <span v-if="!list.length" class="attr-chip unknown empty" title="未标注属性，按未知处理，不会默示为「不含」">
      属性未标注 · 未知
    </span>
  </div>
</template>
