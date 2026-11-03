<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import CandidateCard from "../components/CandidateCard.vue";
import { api, errorMessage } from "../api";
import { statusLabel, yuan } from "../format";
import { TERMINAL_STATUSES, usePolling } from "../polling";
import { loadSession, session } from "../session";
import type { MyState, PublicNegotiation } from "../types";

const route = useRoute();
const router = useRouter();
const id = Number(route.params.id);
loadSession(id);

const pub = ref<PublicNegotiation | null>(null);
const me = ref<MyState | null>(null);
const loading = ref(true);
const error = ref("");
const busy = ref(false);

const token = computed(() => session.current?.token ?? null);
const recommendation = computed(() => pub.value?.recommendation ?? null);
const resultCandidates = computed(() => {
  const ids = recommendation.value?.candidate_ids ?? [];
  return ids
    .map((cid) => pub.value?.candidates.find((item) => item.candidate_id === cid))
    .filter((item): item is NonNullable<typeof item> => item !== undefined);
});
const remaining = computed(() => {
  if (!recommendation.value || !pub.value) return null;
  return pub.value.conditions.budget_cents - recommendation.value.total_cost_cents;
});
const inConfirmWindow = computed(
  () => !!pub.value && ["PRIVATE_CONFIRM", "BEST_EFFORT"].includes(pub.value.status),
);
const decision = computed(() => me.value?.confirmation?.decision ?? null);

async function refresh() {
  if (!token.value) {
    loading.value = false;
    return;
  }
  error.value = "";
  try {
    const [publicView, myState] = await Promise.all([
      api.publicView(id),
      api.myState(id, token.value),
    ]);
    pub.value = publicView;
    me.value = myState;
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function accept() {
  if (!token.value || !me.value || !recommendation.value || busy.value) return;
  try {
    await showConfirmDialog({
      title: "接受这份组合",
      message: "你的确认只针对当前版本。之后如果公共条件或组合发生变化，需要重新确认。",
      confirmButtonText: "我接受这个组合",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.confirmResult(id, recommendation.value.result_version, token.value, {
      decision_config_version: me.value.decision_config_version,
      accept: true,
      reason: null,
    });
    showToast("已确认接受当前版本");
    await refresh();
  } catch (err) {
    // 先刷新（refresh 内会清空 error），再落错误信息，确保失败原因可见
    await refresh();
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function raiseConcern() {
  if (!token.value || !me.value || !recommendation.value || busy.value) return;
  if (decision.value === "reject") {
    router.push(`/n/${id}/me/concern`);
    return;
  }
  try {
    await showConfirmDialog({
      title: "先表达顾虑",
      message:
        "这将暂停本版本的接受确认，进入补充顾虑阶段。顾虑原文只交给发起者，默认不附身份。",
      confirmButtonText: "继续表达顾虑",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.confirmResult(id, recommendation.value.result_version, token.value, {
      decision_config_version: me.value.decision_config_version,
      accept: false,
      reason: null,
    });
    router.push(`/n/${id}/me/concern`);
  } catch (err) {
    await refresh();
    error.value = errorMessage(err);
  } finally {
    busy.value = false;
  }
}

// 等待其他成员确认时自动更新（全员接受即完成）
const polling = usePolling(refresh, () => {
  const status = pub.value?.status;
  return !!status && !TERMINAL_STATUSES.has(status);
});

onMounted(() => {
  void refresh();
  polling.start();
});
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取待确认方案…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要你的独立参与入口</h3>
      <p>请使用邀请链接加入后，再作出私下确认。</p>
      <router-link to="/" class="btn soft mt16">返回首页</router-link>
    </div>

    <div v-else-if="error && !pub" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <template v-else-if="pub && me">
      <div class="page-intro">
        <span class="private-label"><AppIcon name="shield" :size="14" /> 明确表态，不默认同意</span>
        <h1>方案稳定了，想听听你的确认。</h1>
        <p>稳定不代表同意。只有每位成员针对同一版本明确接受，才标记为全员确认。</p>
      </div>

      <section v-if="!recommendation" class="panel empty-state">
        <div class="empty-icon"><AppIcon name="clock" :size="22" /></div>
        <h3>还没有可确认的方案</h3>
        <p>当前状态为「{{ statusLabel(pub.status) }}」。方案形成后会在这里请求你的确认。</p>
        <router-link :to="`/n/${id}`" class="btn soft mt16">回到公共看板</router-link>
      </section>

      <template v-else>
        <section class="panel">
          <div class="between panel-header">
            <div>
              <h2>{{ pub.title }}</h2>
              <p class="hint mt8">方案版本 v{{ recommendation.result_version }} · 第 {{ pub.round_no }} 轮后</p>
            </div>
            <span class="badge amber">{{ statusLabel(pub.status) }}</span>
          </div>

          <div class="summary">
            <div class="metric">
              <div class="metric-label"><AppIcon name="wallet" :size="15" /> 总费用</div>
              <div class="metric-value">
                ¥{{ yuan(recommendation.total_cost_cents) }}
                <small>/ ¥{{ yuan(pub.conditions.budget_cents) }}</small>
              </div>
              <p class="metric-desc">
                <template v-if="remaining !== null">
                  预算内，还余 <span class="green">¥{{ yuan(remaining) }}</span>
                </template>
              </p>
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
              <div class="metric-label"><AppIcon name="users" :size="15" /> 确认方式</div>
              <div class="metric-value" style="font-size: 16px">逐人明确接受</div>
              <p class="metric-desc">离开页面或超时不视为同意</p>
            </div>
          </div>

          <div class="dish-grid mt24">
            <CandidateCard
              v-for="candidate in resultCandidates"
              :key="candidate.candidate_id"
              :candidate="candidate"
              badge="本版方案"
            />
          </div>

          <div class="receipt-list mt16">
            <div class="receipt-item">
              <span>总预算</span><strong>不超过 ¥{{ yuan(pub.conditions.budget_cents) }}</strong>
            </div>
            <div class="receipt-item">
              <span>目标数量</span><strong>{{ pub.conditions.exact_count }} 项</strong>
            </div>
            <div class="receipt-item">
              <span>公共条件版本</span><strong>v{{ pub.conditions.condition_version }}</strong>
            </div>
          </div>

          <div v-if="me.confirmation" class="notice mt16" :class="{ amber: me.confirmation.decision === 'reject' }">
            <AppIcon name="info" :size="15" />
            <span v-if="me.confirmation.decision === 'accept'">
              你已接受方案 v{{ me.confirmation.result_version }}，等待其他成员明确表态。
            </span>
            <span v-else>
              你已针对方案 v{{ me.confirmation.result_version }} 提出顾虑，等待发起者处理。
            </span>
          </div>

          <div v-if="pub.status === 'CONCERN_COLLECTING' && me.confirmation?.decision === 'accept'" class="notice amber mt16">
            <AppIcon name="chat" :size="15" />
            <span>已有成员提出顾虑，发起者正在处理。你的接受记录会保留；若方案调整，需要重新确认。</span>
          </div>

          <div v-if="pub.status === 'CONCERN_COLLECTING' && !me.confirmation" class="notice amber mt16">
            <AppIcon name="chat" :size="15" />
            <span>本版本已暂停确认，发起者正在处理其他成员的补充顾虑。处理完成后会重新请求确认。</span>
          </div>

          <div v-if="error" class="error-box" role="alert">{{ error }}</div>

          <div class="confirm-actions mt24">
            <button
              type="button"
              class="btn primary full"
              :disabled="!inConfirmWindow || decision === 'accept' || busy"
              @click="accept"
            >
              <AppIcon name="check" :size="16" />
              {{ decision === "accept" ? "已接受当前版本" : "我接受这个组合" }}
            </button>
            <button
              type="button"
              class="btn soft full"
              :disabled="(!inConfirmWindow && decision !== 'reject') || decision === 'accept' || busy"
              @click="raiseConcern"
            >
              <AppIcon name="chat" :size="16" />
              {{ decision === "reject" ? "补充我的顾虑" : "我还有顾虑" }}
            </button>
          </div>
          <p class="hint mt16" style="text-align: center">
            两个动作都必须由你主动选择。页面不会预选接受，也不会把停留时长当作同意。
          </p>
        </section>

        <section v-if="pub.status === 'FINALIZED'" class="panel mt16">
          <div class="between">
            <div>
              <h3>全员已确认</h3>
              <p class="hint mt8">所有成员都接受了方案 v{{ recommendation.result_version }}。</p>
            </div>
            <router-link :to="`/n/${id}/result`" class="btn primary">查看最终方案</router-link>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>
