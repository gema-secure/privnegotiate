<script setup lang="ts">
import { computed } from "vue";
import type { Candidate } from "../types";
import { yuan } from "../format";
import { iconForCategory, paletteFor } from "../scenario";
import AppIcon from "./AppIcon.vue";
import AttributeChips from "./AttributeChips.vue";

const props = withDefaults(
  defineProps<{
    candidate: Candidate;
    badge?: string;
    tone?: "recommended" | "excluded" | "normal";
    selectable?: boolean;
    accepted?: boolean;
    isTop?: boolean;
  }>(),
  {
    badge: "",
    tone: "normal",
    selectable: false,
    accepted: false,
    isTop: false,
  },
);

const emit = defineEmits<{
  (event: "toggle", candidateId: string): void;
  (event: "top", candidateId: string): void;
}>();

const palette = computed(() => paletteFor(props.candidate.category, props.candidate.name));

const optionIcon = computed(() =>
  iconForCategory(props.candidate.category, props.candidate.name),
);
</script>

<template>
  <article
    class="dish"
    :class="{
      excluded: tone === 'excluded',
      accepted: selectable && accepted,
      istop: selectable && isTop,
      selectable,
    }"
    @click="selectable && emit('toggle', candidate.candidate_id)"
  >
    <div class="dish-art" :style="{ '--dish-tint': palette.bg }">
      <span class="option-glyph" :style="{ '--glyph-ink': palette.accent }">
        <AppIcon :name="optionIcon" :size="30" />
      </span>
    </div>
    <span v-if="selectable" class="dish-check" aria-hidden="true">
      <AppIcon name="check" :size="14" />
    </span>
    <span v-if="badge" class="dish-badge">{{ badge }}</span>
    <div class="dish-content">
      <div class="dish-name">{{ candidate.name }}</div>
      <div class="dish-footer">
        <span class="dish-category">{{ candidate.category }}</span>
        <span class="price">{{ yuan(candidate.cost_cents) }}</span>
      </div>
      <AttributeChips :attributes="candidate.attributes" compact :max="3" />
      <template v-if="selectable">
        <label class="accept-line" @click.stop>
          <input
            type="checkbox"
            :checked="accepted"
            @change="emit('toggle', candidate.candidate_id)"
          />
          <span>这个我能接受</span>
        </label>
        <div class="top-choice" @click.stop>
          <label :class="{ off: !accepted }">
            <input
              type="radio"
              name="top-choice"
              :checked="isTop"
              :disabled="!accepted"
              @change="emit('top', candidate.candidate_id)"
            />
            <AppIcon name="star" :size="15" />
            本轮最想要
          </label>
        </div>
      </template>
    </div>
  </article>
</template>
