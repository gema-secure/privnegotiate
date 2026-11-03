<script setup lang="ts">
import { computed } from "vue";
import { ICON_PATHS } from "../icons";

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
const path = computed(() => ICON_PATHS[props.name] ?? ICON_PATHS.info);
</script>

<template>
  <svg
    class="icon"
    :style="{ width: `${size}px`, height: `${size}px` }"
    viewBox="0 0 24 24"
    aria-hidden="true"
  >
    <path :d="path" />
  </svg>
</template>
