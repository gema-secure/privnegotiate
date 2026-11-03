<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import { api, errorMessage } from "../api";
import { yuan } from "../format";
import { loadSession, session } from "../session";
import type { MyState, PublicNegotiation } from "../types";

const route = useRoute();
const router = useRouter();
const id = Number(route.params.id);
loadSession(id);

const me = ref<MyState | null>(null);
const pub = ref<PublicNegotiation | null>(null);
const loading = ref(true);
const error = ref("");
const submitting = ref(false);

const exclude = ref<string[]>([]);
const optOut = ref<string[]>([]);
const wantAlternative = ref(false);
const altCategory = ref("");
const altCount = ref(1);
const noExtra = ref(false);
let initialized = false;

const token = computed(() => session.current?.token ?? null);
const categories = computed(() => {
  const values = new Set((pub.value?.candidates ?? []).map((item) => item.category));
  return [...values];
});
const conflict = computed(() => exclude.value.filter((cid) => optOut.value.includes(cid)));
const hasConditions = computed(
  () =>
    exclude.value.length > 0 ||
    optOut.value.length > 0 ||
    (wantAlternative.value && !!altCategory.value),
);

function candidateName(cid: string): string {
  return pub.value?.candidates.find((item) => item.candidate_id === cid)?.name ?? cid;
}

function initialize() {
  if (initialized || !me.value) return;
  initialized = true;
  for (const constraint of me.value.constraints) {
    if (constraint.kind === "exclude_item") exclude.value.push(...constraint.candidate_ids);
    if (constraint.kind === "opt_out") optOut.value.push(...constraint.candidate_ids);
    if (constraint.kind === "require_count") {
      wantAlternative.value = true;
      altCategory.value = constraint.category ?? "";
      altCount.value = constraint.min_count ?? 1;
    }
  }
  exclude.value = [...new Set(exclude.value)];
  optOut.value = [...new Set(optOut.value)];
  noExtra.value =
    me.value.bottomline_status === "no_extra" &&
    exclude.value.length === 0 &&
    optOut.value.length === 0 &&
    !wantAlternative.value;
}

function toggle(list: "exclude" | "optOut", cid: string) {
  noExtra.value = false;
  const target = list === "exclude" ? exclude : optOut;
  target.value = target.value.includes(cid)
    ? target.value.filter((item) => item !== cid)
    : [...target.value, cid];
}

function selectNoExtra() {
  noExtra.value = true;
  exclude.value = [];
  optOut.value = [];
  wantAlternative.value = false;
}

async function refresh() {
  if (!token.value) {
    loading.value = false;
    return;
  }
  error.value = "";
  try {
    const [myState, publicView] = await Promise.all([
      api.myState(id, token.value),
      api.publicView(id, token.value),
    ]);
    me.value = myState;
    pub.value = publicView;
    initialize();
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!me.value || !token.value || submitting.value) return;
  error.value = "";
  if (conflict.value.length) {
    error.value = "同一个候选不能同时设置为「整组避开」和「仅我不参与」，请确认作用范围。";
    return;
  }
  if (wantAlternative.value && !altCategory.value) {
    error.value = "替代要求需要选择一个类别，例如「素菜」或「交通」。";
    return;
  }
  if (!hasConditions.value && !noExtra.value) {
    error.value = "请勾选你的底线，或明确选择「我没有任何额外底线」。";
    return;
  }

  const constraints: Array<{
    kind: "exclude_item" | "opt_out" | "require_count";
    candidate_ids: string[];
    category?: string;
    min_count?: number;
  }> = [];
  if (exclude.value.length) {
    constraints.push({ kind: "exclude_item", candidate_ids: exclude.value });
  }
  if (optOut.value.length) {
    constraints.push({ kind: "opt_out", candidate_ids: optOut.value });
  }
  if (wantAlternative.value) {
    constraints.push({
      kind: "require_count",
      candidate_ids: [],
      category: altCategory.value,
      min_count: Math.max(1, altCount.value),
    });
  }

  submitting.value = true;
  try {
    me.value = await api.confirmConstraints(id, token.value, {
      constraints,
      no_extra_bottomline: noExtra.value,
      expected_preference_version: me.value.preference_version,
    });
    showToast("底线已确认，范围只对你本人生效");
    router.push(`/n/${id}`);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    submitting.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取你的底线…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要你的独立参与入口</h3>
      <p>请使用发起者发给你的邀请链接加入本次协商，再确认底线。</p>
      <router-link to="/" class="btn soft mt16">返回首页</router-link>
    </div>

    <div v-else-if="error && !me" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <template v-else-if="me && pub">
      <div class="page-intro">
        <span class="private-label"><AppIcon name="lock" :size="14" /> 只有你和应用可见</span>
        <h1>先说清楚，你的底线。</h1>
        <p>请区分「大家都需要避开」和「我不参与即可」，应用会按你确认的范围处理。</p>
      </div>

      <section class="panel">
        <div class="between panel-header">
          <h2>01 · 整组必须避开</h2>
          <span class="badge outline">{{ exclude.length }} 项</span>
        </div>
        <p class="hint">这些候选不能出现在最终组合中。只勾选你明确需要全组遵守的底线。</p>
        <div class="choice-grid mt16">
          <label
            v-for="candidate in pub.candidates"
            :key="candidate.candidate_id"
            class="choice"
            :class="{ selected: exclude.includes(candidate.candidate_id) }"
          >
            <input
              type="checkbox"
              :checked="exclude.includes(candidate.candidate_id)"
              @change="toggle('exclude', candidate.candidate_id)"
            />
            <span>
              {{ candidate.name }}
              <small>¥{{ yuan(candidate.cost_cents) }} · {{ candidate.category }}</small>
            </span>
          </label>
        </div>

        <div class="divider" />

        <div class="between panel-header">
          <h2>02 · 只要我不参与即可</h2>
          <span class="badge outline">{{ optOut.length }} 项</span>
        </div>
        <p class="hint">其他人仍可选择这些候选；只有你个人不参与。</p>
        <div class="choice-grid mt16">
          <label
            v-for="candidate in pub.candidates"
            :key="candidate.candidate_id"
            class="choice"
            :class="{ selected: optOut.includes(candidate.candidate_id) }"
          >
            <input
              type="checkbox"
              :checked="optOut.includes(candidate.candidate_id)"
              @change="toggle('optOut', candidate.candidate_id)"
            />
            <span>
              {{ candidate.name }}
              <small>¥{{ yuan(candidate.cost_cents) }} · {{ candidate.category }}</small>
            </span>
          </label>
        </div>

        <div v-if="conflict.length" class="error-box" role="alert">
          {{ conflict.map(candidateName).join("、") }} 同时出现在两组中，请只保留一种作用范围。
        </div>

        <div class="divider" />

        <div class="between panel-header">
          <h2>03 · 替代要求（可选）</h2>
        </div>
        <label class="accept-line">
          <input v-model="wantAlternative" type="checkbox" @change="noExtra = false" />
          <span>组合中至少要有若干项满足我的替代要求</span>
        </label>
        <div v-if="wantAlternative" class="field-grid mt16">
          <div class="field">
            <label for="alt-category">要求类别</label>
            <select id="alt-category" v-model="altCategory">
              <option value="">请选择类别</option>
              <option v-for="category in categories" :key="category" :value="category">
                {{ category }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="alt-count">至少几项</label>
            <input id="alt-count" v-model.number="altCount" type="number" min="1" max="30" />
            <span class="hint">例如「至少 1 项素菜」「至少 2 项户外」，只在满足条件时生效。</span>
          </div>
        </div>

        <div class="notice mt24">
          <AppIcon name="shield" :size="15" />
          <span>
            这里只确认你自己的条件。发起者可以调整公共预算，但不能替你放宽个人底线；
            公共页面不会展示是谁提出了某条私人限制。
          </span>
        </div>

        <div class="mt24">
          <label class="accept-line">
            <input v-model="noExtra" type="checkbox" @change="noExtra && selectNoExtra()" />
            <span>我没有任何额外底线，可以按公共条件参与。</span>
          </label>
          <p class="hint">
            当前状态：{{ me.bottomline_status === "pending" ? "尚未确认" : me.bottomline_status === "no_extra" ? "已声明无额外底线" : "已声明底线" }}
            · 偏好版本 v{{ me.preference_version }}
          </p>
        </div>

        <div v-if="error" class="error-box" role="alert">{{ error }}</div>

        <div class="form-actions">
          <p>确认后会重新检查可行性，并清除旧版本的结果确认。</p>
          <div class="actions">
            <router-link :to="`/n/${id}`" class="btn soft">返回看板</router-link>
            <button type="button" class="btn primary" :disabled="submitting" @click="submit">
              {{ submitting ? "正在确认…" : "确认我的底线" }}
              <AppIcon name="check" :size="16" />
            </button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
