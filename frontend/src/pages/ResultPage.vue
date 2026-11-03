<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import CandidateCard from "../components/CandidateCard.vue";
import { api, errorMessage } from "../api";
import { statusLabel, yuan } from "../format";
import { detectScenario, SCENARIO_VOICE } from "../scenario";
import { shareOrDownloadCard } from "../shareCard";
import { getSession, loadSession } from "../session";
import type { PublicNegotiation } from "../types";

const route = useRoute();
const id = Number(route.params.id);
loadSession(id);

const pub = ref<PublicNegotiation | null>(null);
const loading = ref(true);
const error = ref("");
const fallbackText = ref("");

const recommendation = computed(() => pub.value?.recommendation ?? null);
const voice = computed(() => {
  const candidates = pub.value?.candidates ?? [];
  return SCENARIO_VOICE[
    detectScenario(
      candidates.map((item) => item.category),
      candidates.map((item) => item.name),
    )
  ];
});
const resultCandidates = computed(() => {
  const ids = recommendation.value?.candidate_ids ?? [];
  return ids
    .map((cid) => pub.value?.candidates.find((item) => item.candidate_id === cid))
    .filter((item): item is NonNullable<typeof item> => item !== undefined);
});
const finalState = computed(() => {
  const status = pub.value?.status;
  if (status === "FINALIZED") {
    return {
      tone: "",
      icon: "check",
      title: "大家，都点了头。",
      desc: "所有成员已针对同一版本明确接受。这不是系统替你们做的决定。",
      label: "全员已确认",
      badge: "green" as const,
    };
  }
  if (status === "CLOSED_WITH_CONCERN") {
    return {
      tone: "warn",
      icon: "chat",
      title: "这次，有顾虑被留下了。",
      desc: "协商已结束，公开组合保留，但存在未解决的顾虑，不代表全员同意。",
      label: "已结束，存在未解决顾虑",
      badge: "amber" as const,
    };
  }
  if (status === "CLOSED_UNCONFIRMED") {
    return {
      tone: "bad",
      icon: "info",
      title: "这次没有走到全员确认。",
      desc: "协商已结束，结果按未确认状态记录，不标记为全员同意。",
      label: "已结束，未获全员确认",
      badge: "red" as const,
    };
  }
  return null;
});
const planText = computed(() => {
  if (!pub.value) return "";
  const names = resultCandidates.value.map((item) => item.name).join("、");
  const cost = recommendation.value ? `¥${yuan(recommendation.value.total_cost_cents)}` : "—";
  const budget = `¥${yuan(pub.value.conditions.budget_cents)}`;
  return [
    `【和议】${pub.value.title}`,
    recommendation.value ? `方案 v${recommendation.value.result_version}：${names}` : "方案：暂未形成",
    `总费用：${cost} / 预算 ${budget}`,
    `结果：${statusLabel(pub.value.status)}`,
  ].join("\n");
});

async function refresh() {
  error.value = "";
  try {
    const identity = getSession(id);
    if (!identity) {
      pub.value = null;
      fallbackText.value = "";
      error.value = "协商结果仅本协商成员可见，请使用自己的成员身份进入。";
      return;
    }
    pub.value = await api.publicView(id, identity.token);
  } catch (err) {
    pub.value = null;
    fallbackText.value = "";
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function copyPlan() {
  try {
    await navigator.clipboard.writeText(planText.value);
    fallbackText.value = "";
    showToast("公开方案已复制");
  } catch {
    fallbackText.value = planText.value;
    showToast("复制受限，请在下方手动选择");
  }
}

const sharing = ref(false);

async function shareCard() {
  if (!pub.value || !recommendation.value || sharing.value) return;
  sharing.value = true;
  try {
    const mode = await shareOrDownloadCard({
      title: pub.value.title,
      statusLabel: statusLabel(pub.value.status),
      items: resultCandidates.value.map((item) => ({
        name: item.name,
        costCents: item.cost_cents,
      })),
      totalCents: recommendation.value.total_cost_cents,
      budgetCents: pub.value.conditions.budget_cents,
      banner: voice.value.shareBanner,
    });
    showToast(mode === "shared" ? "已调起分享" : "分享图已生成");
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    sharing.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取协商结果…</p></div>

    <div v-else-if="error" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取结果</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <div v-else-if="!finalState" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="clock" :size="22" /></div>
      <h3>结果还在路上</h3>
      <p>
        当前状态为「{{ pub ? statusLabel(pub.status) : "未知" }}」。协商尚未结束，
        不会把进行中的推荐当作最终结果。
      </p>
      <router-link :to="`/n/${id}`" class="btn primary mt16">回到公共看板</router-link>
    </div>

    <template v-else-if="pub">
      <section class="panel">
        <div class="result-top">
          <div class="result-seal" :class="finalState.tone">
            <AppIcon :name="finalState.icon" :size="26" />
          </div>
          <span class="badge" :class="finalState.badge">{{ finalState.label }}</span>
          <h1>{{ finalState.title }}</h1>
          <p>{{ finalState.desc }}</p>
        </div>

        <div v-if="recommendation" class="summary mt24">
          <div class="metric">
            <div class="metric-label"><AppIcon name="wallet" :size="15" /> 总费用</div>
            <div class="metric-value">
              ¥{{ yuan(recommendation.total_cost_cents) }}
              <small>/ ¥{{ yuan(pub.conditions.budget_cents) }}</small>
            </div>
            <p class="metric-desc">方案版本 v{{ recommendation.result_version }}</p>
          </div>
          <div class="metric">
            <div class="metric-label"><AppIcon name="plate" :size="15" /> 组合数量</div>
            <div class="metric-value">
              {{ recommendation.candidate_ids.length }}
              <small>/ {{ pub.conditions.exact_count }}</small>
            </div>
            <p class="metric-desc">{{ recommendation.source === "optimal" ? "最优推荐" : "可行推荐" }}</p>
          </div>
          <div class="metric">
            <div class="metric-label"><AppIcon name="clock" :size="15" /> 协商轮次</div>
            <div class="metric-value">{{ pub.round_no }} <small>轮</small></div>
            <p class="metric-desc">条件 v{{ pub.conditions.condition_version }}</p>
          </div>
        </div>

        <div v-if="resultCandidates.length" class="dish-grid mt24">
          <CandidateCard
            v-for="candidate in resultCandidates"
            :key="candidate.candidate_id"
            :candidate="candidate"
            :badge="pub.status === 'FINALIZED' ? '全员确认' : '公开组合'"
          />
        </div>

        <div v-else class="empty-state">
          <p>本次没有留下公开组合。</p>
        </div>

        <div class="panel-foot between">
          <span class="small-label">
            <AppIcon name="shield" :size="14" />
            公开副本只包含组合、费用、版本与结果状态
          </span>
          <div class="template-actions">
            <button type="button" class="btn soft small" :disabled="sharing" @click="shareCard">
              <AppIcon name="share" :size="14" />
              {{ sharing ? "生成中…" : "生成分享图" }}
            </button>
            <button type="button" class="btn soft small" @click="copyPlan">
              <AppIcon name="copy" :size="14" />
              复制公开方案
            </button>
          </div>
        </div>
        <div v-if="fallbackText" class="copy-fallback">{{ fallbackText }}</div>

        <p class="hint mt16">
          复制内容不含任何成员姓名、个人回答、理由或底线来源。
        </p>

        <div class="form-actions">
          <p>和议 HEYI · 听见每一种偏好，让相聚刚刚好。</p>
          <div class="actions">
            <router-link to="/" class="btn ghost">返回首页</router-link>
            <router-link :to="`/negotiations/new?from=${id}`" class="btn soft">
              再约一次
            </router-link>
            <router-link :to="`/n/${id}`" class="btn soft">再看一眼方案</router-link>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
