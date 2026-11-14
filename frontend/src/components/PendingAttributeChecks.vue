<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import AttributeChips from "./AttributeChips.vue";
import type { Candidate, MyPendingAttributeCheck } from "../types";

const props = defineProps<{
  checks: MyPendingAttributeCheck[];
  candidates: Candidate[];
}>();

const candidateMap = computed(
  () => new Map(props.candidates.map((item) => [item.candidate_id, item])),
);

function requirementText(check: MyPendingAttributeCheck): string {
  const parts: string[] = [];
  if (check.category) parts.push(`类别「${check.category}」`);
  if (check.attribute_keys.length) parts.push(`属性「${check.attribute_keys.join("、")}」`);
  return parts.length ? parts.join("且") : "你设定的替代条件";
}

function unconfirmed(check: MyPendingAttributeCheck): Candidate[] {
  return check.unconfirmed_candidate_ids
    .map((id) => candidateMap.value.get(id))
    .filter((item): item is Candidate => item !== undefined);
}
</script>

<template>
  <section v-if="checks.length" class="pending-checks" aria-label="待确认的属性事实">
    <div class="between pending-head">
      <div>
        <h2>属性待确认 · {{ checks.length }} 项</h2>
        <p class="hint">
          这里只显示你自己的替代要求；其他成员的约束与属性需求不会出现在这里。
        </p>
      </div>
      <span class="badge amber">未知 = 待确认</span>
    </div>

    <article v-for="check in checks" :key="check.constraint_id" class="pending-check">
      <div class="between">
        <strong>替代要求：至少 {{ check.required_count }} 项满足{{ requirementText(check) }}</strong>
        <span class="small-label">已确认 {{ check.confirmed_count }} / {{ check.required_count }} 项</span>
      </div>

      <p class="pc-explain">
        目前只有 {{ check.confirmed_count }} 项能确认满足，另有
        {{ check.unconfirmed_candidate_ids.length }} 项的相关属性还没有事实依据。
        需要确认的是：下列候选的「{{ check.attribute_keys.length ? check.attribute_keys.join("、") : "相关" }}」属性
        究竟是<b>含</b>还是<b>不含</b>。未知会按未知保留——既不算满足，也不会被当作不含。
      </p>

      <div class="pc-candidates">
        <div v-for="item in unconfirmed(check)" :key="item.candidate_id" class="pc-candidate">
          <span class="pc-name">
            <AppIcon name="warning" :size="12" />
            {{ item.name }}
          </span>
          <AttributeChips :attributes="item.attributes" compact :max="3" />
        </div>
      </div>

      <p class="pc-next">
        待发起者在管理中补充或修正属性事实后，这里会自动更新；在此之前不会用猜测替代事实。
      </p>
    </article>

    <p class="hint pc-foot">
      若这些属性确实无法确认，你可以回到「我的底线」调整替代要求，或保留现状等待补充。
    </p>
  </section>
</template>
