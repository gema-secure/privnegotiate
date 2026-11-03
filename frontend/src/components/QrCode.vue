<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import QRCode from "qrcode";

const props = withDefaults(defineProps<{ value: string; size?: number }>(), {
  size: 168,
});

const dataUrl = ref("");

async function render() {
  if (!props.value) {
    dataUrl.value = "";
    return;
  }
  try {
    dataUrl.value = await QRCode.toDataURL(props.value, {
      width: props.size,
      margin: 1,
      errorCorrectionLevel: "M",
      color: { dark: "#245E50", light: "#FFFFFF" },
    });
  } catch {
    dataUrl.value = "";
  }
}

watch(() => props.value, render);
onMounted(render);
</script>

<template>
  <img
    v-if="dataUrl"
    class="qr-img"
    :src="dataUrl"
    :width="size"
    :height="size"
    alt="邀请二维码"
  />
</template>
