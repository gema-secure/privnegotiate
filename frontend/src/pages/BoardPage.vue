<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import { api, errorMessage } from "../api";
import { statusLabel, statusTone, yuan } from "../format";
import { TERMINAL_STATUSES, usePolling } from "../polling";
import { detectScenario, iconForCategory, paletteFor, SCENARIO_VOICE } from "../scenario";
import { loadSession, session } from "../session";
import type { MyHistoryEntry, MyState, PublicNegotiation } from "../types";

const route = useRoute();
const id = Number(route.params.id);
loadSession(id);

const pub = ref<PublicNegotiation | null>(null);
const me = ref<MyState | null>(null);
const history = ref<MyHistoryEntry[]>([]);
const loading = ref(true);
const error = ref("");
const busy = ref(false);
const tab = ref<"recommended" | "all">("recommended");

const token = computed(() => session.current?.token ?? null);
const isInitiator = computed(() => session.current?.role === "initiator");
const invited = computed(() => !!session.current);

const recommendation = computed(() => pub.value?.recommendation ?? null);
// 场景自适应语气：按公开的分类与候选名识别聚餐/出游/通用，标题随之切换
const voice = computed(() => {
  const candidates = pub.value?.candidates ?? [];
  return SCENARIO_VOICE[
    detectScenario(
      candidates.map((item) => item.category),
      candidates.map((item) => item.name),
    )
  ];
});
const recommendedIds = computed(() => new Set(recommendation.value?.candidate_ids ?? []));
const candidateMap = computed(
  () => new Map((pub.value?.candidates ?? []).map((item) => [item.candidate_id, item])),
);
const visibleCandidates = computed(() => {
  const all = pub.value?.candidates ?? [];
  if (tab.value === "all" || !recommendation.value) return all;
  return all.filter((item) => recommendedIds.value.has(item.candidate_id));
});
const recommendedCandidates = computed(() =>
  (recommendation.value?.candidate_ids ?? [])
    .map((cid) => candidateMap.value.get(cid))
    .filter((item): item is NonNullable<typeof item> => item !== undefined),
);
const remaining = computed(() => {
  if (!recommendation.value || !pub.value) return null;
  return pub.value.conditions.budget_cents - recommendation.value.total_cost_cents;
});

// 费用数字滚动：新组合出现时从旧值缓动到新值；reduced-motion 直接落位。
// 单一动画句柄：新目标到达先取消进行中的动画，避免竞态跳变。
const animatedCost = ref<number | null>(null);
let costRaf = 0;
watch(
  () => pub.value?.recommendation?.total_cost_cents ?? null,
  (target) => {
    if (target == null) {
      cancelAnimationFrame(costRaf);
      costRaf = 0;
      animatedCost.value = null;
      return;
    }
    if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      cancelAnimationFrame(costRaf);
      costRaf = 0;
      animatedCost.value = target;
      return;
    }
    cancelAnimationFrame(costRaf);
    const from = animatedCost.value ?? 0;
    const start = performance.now();
    const duration = 700;
    const step = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      animatedCost.value = Math.round(from + (target - from) * eased);
      if (progress < 1) {
        costRaf = requestAnimationFrame(step);
      } else {
        costRaf = 0;
      }
    };
    costRaf = requestAnimationFrame(step);
  },
  { immediate: true },
);
onUnmounted(() => cancelAnimationFrame(costRaf));

const answeredCurrent = computed(() => {
  const round = pub.value?.round_no;
  if (round === undefined) return false;
  return history.value.some((entry) => entry.round_no === round && entry.answer !== null);
});

const progressIndex = computed(() => {
  const status = pub.value?.status;
  if (!status) return 0;
  if (
    ["ASKING", "ANSWERING", "CLOSING", "COMPUTING", "UPDATING"].includes(status)
  ) {
    return 1;
  }
  if (["PRIVATE_CONFIRM", "BEST_EFFORT", "CONCERN_COLLECTING"].includes(status)) {
    return 2;
  }
  if (["FINALIZED", "CLOSED_WITH_CONCERN", "CLOSED_UNCONFIRMED"].includes(status)) {
    return 3;
  }
  return 0;
});

const progressSteps = computed(() => [
  {
    name: "确认底线",
    desc:
      pub.value?.status === "COLLECTING_BOTTOMLINE"
        ? "正在收集，仅供本人确认"
        : "已按成员确认的条件执行",
  },
  {
    name: "独立作答",
    desc:
      progressIndex.value === 1
        ? `第 ${pub.value?.round_no ?? 1} 轮进行中`
        : progressIndex.value > 1
          ? "本轮已完成"
          : "等待可行性检查",
  },
  {
    name: "私下确认",
    desc: progressIndex.value === 2 ? "等待成员明确表态" : "稳定不等于同意",
  },
  {
    name: "公布结果",
    desc: progressIndex.value === 3 ? "已形成结束状态" : "全员确认后完成",
  },
]);

const stageActive = computed(() => {
  const status = pub.value?.status;
  return !!status && !TERMINAL_STATUSES.has(status);
});

interface NextAction {
  title: string;
  text: string;
  label?: string;
  to?: string;
  kind?: "" | "amber" | "red";
  icon: string;
}

const nextAction = computed<NextAction | null>(() => {
  const p = pub.value;
  if (!p) return null;
  if (!invited.value) {
    return {
      title: "用你的独立邀请进入",
      text: "公共看板可以匿名查看。要参与作答或确认，请使用发起者发给你的邀请链接。",
      label: "使用邀请链接加入",
      to: "/",
      icon: "link",
    };
  }

  const base = `/n/${id}`;
  switch (p.status) {
    case "COLLECTING_BOTTOMLINE":
      if (me.value?.bottomline_status === "pending") {
        return {
          title: "先确认你的参与底线",
          text: "区分整组避开与仅自己不参与；确认后其他人看不到你的私人条件。",
          label: "确认我的底线",
          to: `${base}/me/bottomlines`,
          icon: "shield",
        };
      }
      return {
        title: "等待其他成员确认底线",
        text: "全员底线就绪后才会检查可行性并开始作答。",
        label: "查看我的底线",
        to: `${base}/me/bottomlines`,
        icon: "clock",
      };
    case "CHECKING_FEASIBILITY":
    case "CLOSING":
    case "COMPUTING":
    case "UPDATING":
      return {
        title: "正在计算当前方案",
        text: "计算完成后会更新公共组合；已经提交的条件和作答不会丢失。",
        icon: "clock",
      };
    case "INFEASIBLE":
      return {
        title: "当前条件下，暂时没有可行组合",
        text: isInitiator.value
          ? "可以调整公共预算、数量或结束协商。个人底线不会被自动放宽。"
          : "发起者会调整公共条件；你的个人底线会保留，不会被自动放宽。",
        label: isInitiator.value ? "调整公共条件" : "查看共同条件",
        to: `${base}/manage`,
        kind: "red",
        icon: "warning",
      };
    case "RETRY_OR_REVIEW":
      return {
        title: "计算暂未完成",
        text: "目前还不能判断条件是否有解。已确认的条件会保留，不会写为“无解”。",
        label: isInitiator.value ? "去重试计算" : "查看共同条件",
        to: `${base}/manage`,
        kind: "amber",
        icon: "retry",
      };
    case "ADJUSTING":
      return {
        title: "发起者正在调整公共条件",
        text: "调整后需要重新计算，旧版本的确认会失效。",
        label: isInitiator.value ? "去调整条件" : "查看当前条件",
        to: `${base}/manage`,
        kind: "amber",
        icon: "settings",
      };
    case "ASKING":
    case "ANSWERING":
      if (me.value?.question_batch && !answeredCurrent.value) {
        return {
          title: "轮到你，悄悄说说喜欢什么",
          text: `本轮只看 ${me.value.question_batch.items.length} 个候选。你的选择不会展示给其他成员。`,
          label: "私下作答",
          to: `${base}/me/answer`,
          icon: "edit",
        };
      }
      return {
        title: answeredCurrent.value ? "你的选择已收到，等待本轮结束" : "等待本轮题目",
        text: "本轮收齐后，发起者会关闭本轮并更新组合。",
        label: "查看我的选择",
        to: `${base}/me/history`,
        icon: "clock",
      };
    case "PRIVATE_CONFIRM":
    case "BEST_EFFORT":
      if (!me.value?.confirmation) {
        return {
          title:
            p.status === "BEST_EFFORT"
              ? "已到轮数上限，当前方案仍待确认"
              : "方案连续两轮未变，请你私下确认",
          text: "稳定不等于同意。你的确认只针对当前版本，也可以先提出顾虑。",
          label: "查看并私下确认",
          to: `${base}/me/confirm`,
          kind: p.status === "BEST_EFFORT" ? "amber" : "",
          icon: "shield",
        };
      }
      return {
        title: "你已确认，等待其余成员",
        text: "每个人都明确接受后，协商才会完成。",
        label: "查看我的确认",
        to: `${base}/me/confirm`,
        icon: "check",
      };
    case "CONCERN_COLLECTING":
      return isInitiator.value
        ? {
            title: "有成员提交了补充顾虑",
            text: "仅显示明确告知会交给你的本次内容，不附带成员身份与历史回答。",
            label: "处理补充顾虑",
            to: `${base}/manage`,
            kind: "amber",
            icon: "chat",
          }
        : {
            title: "你的顾虑已提交，等待发起者处理",
            text: "发起者会决定继续协商，或保留异议结束。",
            label: "查看我的记录",
            to: `${base}/me/history`,
            kind: "amber",
            icon: "chat",
          };
    case "FINALIZED":
      return {
        title: "大家都明确接受了这份方案",
        text: "全员针对同一版本完成确认，可以查看最终方案。",
        label: "查看最终方案",
        to: `${base}/result`,
        icon: "check",
      };
    case "CLOSED_WITH_CONCERN":
      return {
        title: "协商已结束，存在未解决顾虑",
        text: "当前组合与异议记录会被保留，这次结果不代表全员同意。",
        label: "查看结束记录",
        to: `${base}/result`,
        kind: "amber",
        icon: "info",
      };
    case "CLOSED_UNCONFIRMED":
      return {
        title: "协商已结束，未获全员确认",
        text: "结果按结束状态记录，不标记为全员同意。",
        label: "查看结束记录",
        to: `${base}/result`,
        kind: "red",
        icon: "info",
      };
    default:
      return {
        title: statusLabel(p.status),
        text: "请查看当前协商进度。",
        icon: "info",
      };
  }
});

const pendingReasonText = computed(() => {
  const p = pub.value;
  if (!p) return "";
  if (p.status === "INFEASIBLE") return "当前条件冲突，暂不展示可执行方案。";
  if (p.status === "RETRY_OR_REVIEW") return "计算尚未完成，暂不展示可执行方案。";
  if (p.recommendation) return "组合随每轮作答自动更新；暂未入选不等于淘汰。";
  switch (p.recommendation_pending_reason) {
    case "collecting":
      return "等待成员确认底线或完成本轮作答，再生成推荐组合。";
    case "computing":
      return "方案计算中，完成后会自动更新。";
    case "no_preference_evidence":
      return "还没有足够的偏好证据，先完成你的本轮作答。";
    default:
      return "当前没有可对外展示的推荐组合。";
  }
});

const canCloseRound = computed(
  () =>
    isInitiator.value &&
    !!pub.value &&
    ["ASKING", "ANSWERING"].includes(pub.value.status),
);

async function refresh() {
  error.value = "";
  // 公共视图先行：即使个人令牌失效/过期，看板与轮询仍持续工作
  try {
    pub.value = await api.publicView(id);
  } catch (err) {
    error.value = errorMessage(err);
    loading.value = false;
    return;
  }
  if (!token.value) {
    loading.value = false;
    return;
  }
  try {
    const [myState, historyEntries] = await Promise.all([
      api.myState(id, token.value),
      api.history(id, token.value),
    ]);
    me.value = myState;
    history.value = historyEntries;
  } catch {
    // 个人态拉取失败不阻塞公共数据；下一轮轮询自动重试
  } finally {
    loading.value = false;
  }
}

// 活动态自动轮询：封轮计算完成、他人作答/确认后，页面无需手动刷新
const polling = usePolling(refresh, () => {
  const status = pub.value?.status;
  return !!status && !TERMINAL_STATUSES.has(status);
});

async function closeRound() {
  if (!pub.value || !token.value || busy.value) return;
  try {
    await showConfirmDialog({
      title: "结束本轮并计算",
      message: "关闭后本轮不再接收作答，应用会按已确认条件与当前偏好计算新组合。",
      confirmButtonText: "结束并计算",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    const result = await api.closeRound(id, pub.value.round_no, token.value);
    showToast(result.message || "本轮已处理");
    await refresh();
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  void refresh();
  polling.start();
});
</script>

<template>
  <div v-if="loading" class="stage-page loading-state"><p>正在读取协商状态…</p></div>

  <div v-else-if="error && !pub" class="stage-page error-state">
    <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
    <h3>暂时无法读取协商</h3>
    <p>{{ error }}</p>
    <div class="top-actions mt16" style="justify-content: center">
      <button type="button" class="btn soft" @click="refresh">重试</button>
      <router-link to="/" class="btn ghost">返回首页</router-link>
    </div>
  </div>

  <template v-else-if="pub">
    <div class="stage-page">
      <div class="stage-strip">
        <span>{{ pub.title }} · 第 {{ pub.round_no }}/{{ pub.conditions.round_limit }} 轮</span>
        <span v-if="stageActive" class="live"><i></i>{{ statusLabel(pub.status) }}</span>
        <span v-else class="badge" :class="statusTone(pub.status)">{{ statusLabel(pub.status) }}</span>
        <button
          v-if="canCloseRound"
          type="button"
          class="btn soft small close-btn"
          :disabled="busy"
          @click="closeRound"
        >
          <AppIcon name="retry" :size="13" />
          {{ busy ? "处理中…" : "结束本轮并计算" }}
        </button>
      </div>

      <div class="stage-hero">
        <h1>{{ pub.status === "FINALIZED" ? voice.finalTitle : voice.proposalTitle }}</h1>
        <p class="sub">{{ pendingReasonText }}</p>
      </div>

      <section v-if="nextAction" class="stage-banner" :class="nextAction.kind">
        <span class="ic"><AppIcon :name="nextAction.icon" :size="20" /></span>
        <div>
          <h3>{{ nextAction.title }}</h3>
          <p>{{ nextAction.text }}</p>
        </div>
        <router-link v-if="nextAction.to && nextAction.label" :to="nextAction.to" class="btn primary">
          {{ nextAction.label }}
        </router-link>
      </section>

      <div class="stage-stats">
        <div class="stage-stat hero-stat">
          <div class="v">
            <template v-if="recommendation">{{ yuan(animatedCost ?? recommendation.total_cost_cents) }}</template>
            <template v-else>—</template>
            <small>/ ¥{{ yuan(pub.conditions.budget_cents) }}</small>
          </div>
          <div class="l"><AppIcon name="wallet" :size="13" /> 组合费用</div>
          <div v-if="remaining !== null" class="d">预算内，余 ¥{{ yuan(remaining) }}</div>
        </div>
        <div class="stage-stat mid-stat">
          <div class="v">
            {{ recommendation ? recommendation.candidate_ids.length : "—" }}
            <small>/ {{ pub.conditions.exact_count }} 项</small>
          </div>
          <div class="l"><AppIcon name="plate" :size="13" /> 已选</div>
        </div>
        <div class="stage-stat mid-stat">
          <div class="v">{{ pub.round_no }}<small>/ {{ pub.conditions.round_limit }} 轮</small></div>
          <div class="l"><AppIcon name="clock" :size="13" /> 进程</div>
        </div>
      </div>

      <div class="stage-rail-zone">
        <div class="stage-rail-head">
          <span class="cap">候 选 长 廊</span>
          <div class="stage-seg" role="tablist" aria-label="候选范围">
            <button
              type="button"
              role="tab"
              :class="{ active: tab === 'recommended' }"
              :aria-selected="tab === 'recommended'"
              @click="tab = 'recommended'"
            >
              当前推荐 {{ recommendedCandidates.length }}
            </button>
            <button
              type="button"
              role="tab"
              :class="{ active: tab === 'all' }"
              :aria-selected="tab === 'all'"
              @click="tab = 'all'"
            >
              全部 {{ pub.candidates.length }}
            </button>
          </div>
          <span class="hint-r">横滑浏览 →</span>
        </div>

        <div v-if="visibleCandidates.length" class="stage-rail">
          <div
            v-for="candidate in visibleCandidates"
            :key="candidate.candidate_id"
            class="stage-poster"
            :class="{ rec: recommendedIds.has(candidate.candidate_id) }"
            :style="{
              '--dish-tint': paletteFor(candidate.category, candidate.name).bg,
              '--glyph-ink': paletteFor(candidate.category, candidate.name).accent,
            }"
          >
            <span v-if="recommendedIds.has(candidate.candidate_id)" class="flag">推荐</span>
            <span class="g">
              <AppIcon :name="iconForCategory(candidate.category, candidate.name)" :size="30" />
            </span>
            <div class="n">{{ candidate.name }}</div>
            <div class="cat">{{ candidate.category }}</div>
            <div class="p">{{ yuan(candidate.cost_cents) }}</div>
          </div>
        </div>
        <div v-else class="stage-empty-rail">
          暂时没有可展示的推荐——{{ pendingReasonText }}
        </div>
      </div>

      <div class="stage-timeline">
        <template v-for="(step, index) in progressSteps" :key="step.name">
          <div
            class="stage-tl-dot"
            :class="{ done: index < progressIndex, now: index === progressIndex }"
          >
            <i></i>
            {{ step.name }}
          </div>
          <div
            v-if="index < progressSteps.length - 1"
            class="stage-tl-line"
            :class="{ done: index < progressIndex }"
          />
        </template>
      </div>

      <div class="stage-chips">
        <span class="stage-chip">预算 <b>¥{{ yuan(pub.conditions.budget_cents) }}</b></span>
        <span class="stage-chip">恰选 <b>{{ pub.conditions.exact_count }} 项</b></span>
        <span class="stage-chip">轮数上限 <b>{{ pub.conditions.round_limit }}</b></span>
        <span v-for="(limit, category) in pub.conditions.category_limits" :key="category" class="stage-chip">
          {{ category }}上限 <b>{{ limit }}</b>
        </span>
        <span class="stage-chip">条件版本 <b>v{{ pub.conditions.condition_version }}</b></span>
      </div>

      <div class="stage-footnote">
        <span>暂未入选不等于淘汰；未评价也不代表不喜欢。后续回答仍可能让组合变化。</span>
        <span class="ver" v-if="recommendation">方案 v{{ recommendation.result_version }}</span>
      </div>
    </div>
  </template>
</template>
