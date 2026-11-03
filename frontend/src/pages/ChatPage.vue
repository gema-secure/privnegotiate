<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import MessageBubble from "../components/MessageBubble.vue";
import { api, errorMessage } from "../api";
import { statusLabel, yuan } from "../format";
import { TERMINAL_STATUSES, usePolling } from "../polling";
import { deriveMessages } from "../chat/derive";
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

const token = computed(() => session.current?.token ?? null);
const invited = computed(() => !!session.current);
const isInitiator = computed(() => session.current?.role === "initiator");

const messages = computed(() =>
  pub.value ? deriveMessages(pub.value, me.value, history.value) : [],
);

/* ---------- 渲染性能：窗口化 + 滚动不劫持 ---------- */
// 长对话只挂载最近 WINDOW 条；更早的消息按需展开
const WINDOW_SIZE = 40;
const visibleCount = ref(WINDOW_SIZE);
const visibleMessages = computed(() => {
  const all = messages.value;
  return all.length > visibleCount.value
    ? all.slice(all.length - visibleCount.value)
    : all;
});
const hiddenCount = computed(() =>
  Math.max(0, messages.value.length - visibleCount.value),
);

const threadEl = ref<HTMLElement | null>(null);
const nearBottom = ref(true);
const unseenCount = ref(0);
let didInitialScroll = false;

function distanceFromBottom(): number {
  const el = threadEl.value;
  if (!el) return 0;
  return el.scrollHeight - el.scrollTop - el.clientHeight;
}

function scrollToBottom() {
  threadEl.value?.scrollTo({ top: threadEl.value.scrollHeight });
  unseenCount.value = 0;
  nearBottom.value = true;
}

function onThreadScroll() {
  const el = threadEl.value;
  if (!el) return;
  nearBottom.value = distanceFromBottom() <= 140;
  if (nearBottom.value) unseenCount.value = 0;
}

/* 新消息到达：贴近底部才自动滚，否则累计未读并显示回底按钮 */
watch(
  messages,
  async () => {
    await nextTick();
    if (!threadEl.value) return;
    if (!didInitialScroll) {
      didInitialScroll = true;
      scrollToBottom();
      return;
    }
    const wasNear = nearBottom.value || distanceFromBottom() <= 140;
    if (wasNear) {
      scrollToBottom();
    } else {
      unseenCount.value += 1;
    }
  },
);

/* ---------- 应答状态机 ---------- */
type AnswerStep = "multi" | "top";
const answerStep = ref<AnswerStep>("multi");
const picked = ref<string[]>([]);
const topPick = ref<string | null>(null);
const skipped = ref(false);
let pendingRequestId = "";

const batchItems = computed(() => me.value?.question_batch?.items ?? []);
const needAnswer = computed(
  () =>
    !!me.value &&
    !!me.value.question_batch &&
    ["ASKING", "ANSWERING"].includes(pub.value?.status ?? "") &&
    !history.value.some(
      (entry) => entry.round_no === pub.value?.round_no && entry.answer !== null,
    ),
);
const needBottomline = computed(
  () => !!me.value && me.value.bottomline_status === "pending",
);
const needConfirm = computed(
  () =>
    !!me.value &&
    !me.value.confirmation &&
    !!pub.value &&
    ["PRIVATE_CONFIRM", "BEST_EFFORT"].includes(pub.value.status),
);
const myConfirmed = computed(() => me.value?.confirmation?.decision ?? null);
const canCloseRound = computed(
  () =>
    isInitiator.value &&
    !!pub.value &&
    ["ASKING", "ANSWERING"].includes(pub.value.status),
);

function resetAnswer() {
  answerStep.value = "multi";
  picked.value = [];
  topPick.value = null;
  skipped.value = false;
  pendingRequestId = "";
}

function togglePick(candidateId: string) {
  pendingRequestId = "";
  const index = picked.value.indexOf(candidateId);
  if (index >= 0) {
    picked.value = picked.value.filter((item) => item !== candidateId);
    if (topPick.value === candidateId) topPick.value = null;
  } else {
    picked.value = [...picked.value, candidateId];
  }
}

function chooseSkip(mode: "none" | "skip") {
  pendingRequestId = "";
  picked.value = [];
  topPick.value = null;
  skipped.value = true;
  void submitAnswer(mode);
}

async function submitAnswer(mode: "explicit" | "none" | "skip") {
  const batch = me.value?.question_batch;
  if (!batch || !token.value || busy.value) return;
  if (mode === "explicit" && picked.value.length === 0) {
    showToast("先点选能接受的候选，或选择「都不接受/跳过」");
    return;
  }
  if (mode === "explicit" && !topPick.value) {
    showToast("再选一个本轮最想要的");
    return;
  }
  busy.value = true;
  try {
    const payload = {
      question_batch_id: batch.question_batch_id,
      request_id: pendingRequestId || (pendingRequestId = crypto.randomUUID()),
      mode: mode === "skip" ? ("skip" as const) : ("explicit" as const),
      evaluated_ids: mode === "skip" ? [] : batchItems.value.map((item) => item.candidate_id),
      acceptable_ids: mode === "explicit" ? picked.value : [],
      top_id: mode === "explicit" ? topPick.value : null,
      reason: null,
    };
    await api.submitAnswer(id, token.value, payload);
    pendingRequestId = "";
    resetAnswer();
    await refresh();
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    busy.value = false;
  }
}

async function acceptProposal() {
  const recommendation = pub.value?.recommendation;
  if (!recommendation || !me.value || !token.value || busy.value) return;
  try {
    await showConfirmDialog({
      title: "接受这一版",
      message: "你的确认只针对当前版本；之后条件或组合变化需要重新确认。",
      confirmButtonText: "我接受",
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.confirmResult(id, recommendation.result_version, token.value, {
      decision_config_version: me.value.decision_config_version,
      accept: true,
      reason: null,
    });
    showToast("已确认接受当前版本");
    await refresh();
  } catch (err) {
    showToast(errorMessage(err));
    await refresh();
  } finally {
    busy.value = false;
  }
}

async function closeRoundFromChat() {
  if (!pub.value || !token.value || busy.value) return;
  try {
    await showConfirmDialog({
      title: "结束本轮并计算",
      message: "关闭后本轮不再接收作答，我会按已确认条件与当前偏好计算新组合。",
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

/* ---------- 数据与轮询 ---------- */
async function refresh() {
  error.value = "";
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
    const [myState, entries] = await Promise.all([
      api.myState(id, token.value),
      api.history(id, token.value),
    ]);
    me.value = myState;
    history.value = entries;
  } catch {
    // 个人态失败不阻塞公共对话
  } finally {
    loading.value = false;
  }
}

const polling = usePolling(refresh, () => {
  const status = pub.value?.status;
  return !!status && !TERMINAL_STATUSES.has(status);
});

const liveStatus = computed(() => {
  const status = pub.value?.status;
  return !!status && !TERMINAL_STATUSES.has(status);
});

onMounted(() => {
  void refresh();
  polling.start();
});
</script>

<template>
  <div v-if="loading" class="stage-page loading-state"><p>正在进入对话…</p></div>

  <div v-else-if="error && !pub" class="stage-page error-state">
    <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
    <h3>暂时无法进入对话</h3>
    <p>{{ error }}</p>
    <button type="button" class="btn soft mt16" @click="refresh">重试</button>
  </div>

  <div v-else-if="pub" class="chat-shell">
    <div class="chat-top">
      <div class="chat-yi-avatar">议</div>
      <div>
        <div class="t">小议 · {{ pub.title }}</div>
        <div class="s">
          <span v-if="liveStatus" class="dot-live"></span>
          {{ statusLabel(pub.status) }} · 第 {{ pub.round_no }}/{{ pub.conditions.round_limit }} 轮
        </div>
      </div>
      <router-link :to="`/n/${id}/board`" class="more" aria-label="查看完整看板">
        <AppIcon name="board" :size="18" />
      </router-link>
    </div>

    <div ref="threadEl" class="chat-thread" aria-live="polite" @scroll.passive="onThreadScroll">
      <span class="chat-day">私 密 协 商 · 只 有 你 和 小 议 可 见</span>

      <button
        v-if="hiddenCount > 0"
        type="button"
        class="chat-older"
        @click="visibleCount += WINDOW_SIZE"
      >
        展开更早的 {{ hiddenCount }} 条
      </button>

      <MessageBubble
        v-for="message in visibleMessages"
        :key="message.id"
        :message="message"
      />
    </div>

    <button
      v-if="unseenCount > 0 && !nearBottom"
      type="button"
      class="chat-unseen"
      @click="scrollToBottom"
    >
      <AppIcon name="arrow" :size="14" style="transform: rotate(90deg)" />
      {{ unseenCount }} 条新消息
    </button>

    <div class="chat-dock">
      <!-- 未加入 -->
      <template v-if="!invited">
        <div class="dock-q"><AppIcon name="lock" :size="13" /> 你正在旁听公共对话</div>
        <div class="chat-chips">
          <router-link to="/#join" class="chat-chip">使用邀请链接加入</router-link>
        </div>
        <div class="chat-dock-hint">加入后小议会私下向你提问，成员之间互相看不到回答。</div>
      </template>

      <!-- 底线 -->
      <template v-else-if="needBottomline">
        <div class="dock-q"><AppIcon name="shield" :size="13" /> 小议需要先和你确认底线</div>
        <div class="chat-chips">
          <router-link :to="`/n/${id}/me/bottomlines`" class="chat-chip">去确认我的底线</router-link>
        </div>
      </template>

      <!-- 作答：多选 -->
      <template v-else-if="needAnswer && answerStep === 'multi'">
        <div class="dock-q">
          <span class="chat-step-tag">第 1 步 · 多选</span>
          哪些你能接受？已选 {{ picked.length }} 项
        </div>
        <div class="chat-chips">
          <button
            v-for="item in batchItems"
            :key="item.candidate_id"
            type="button"
            class="chat-chip"
            :class="{ on: picked.includes(item.candidate_id) }"
            @click="togglePick(item.candidate_id)"
          >
            {{ item.name }} <span class="p">¥{{ yuan(item.cost_cents) }}</span>
          </button>
        </div>
        <div class="chat-dock-actions">
          <button
            type="button"
            class="chat-chip"
            :disabled="busy"
            @click="chooseSkip('none')"
          >这些都不接受</button>
          <button
            type="button"
            class="chat-chip"
            :disabled="busy"
            @click="chooseSkip('skip')"
          >本轮跳过</button>
          <span class="spacer"></span>
          <button
            type="button"
            class="btn primary"
            :disabled="busy || picked.length === 0"
            @click="answerStep = 'top'"
          >
            下一步
            <AppIcon name="arrow" :size="15" />
          </button>
        </div>
      </template>

      <!-- 作答：最想要 -->
      <template v-else-if="needAnswer && answerStep === 'top'">
        <div class="dock-q">
          <span class="chat-step-tag">第 2 步 · 单选</span>
          这些里最想要哪个？
        </div>
        <div class="chat-chips">
          <button
            v-for="candidateId in picked"
            :key="candidateId"
            type="button"
            class="chat-chip"
            :class="{ 'star-on': topPick === candidateId }"
            @click="topPick = candidateId"
          >
            ★ {{ batchItems.find((item) => item.candidate_id === candidateId)?.name }}
          </button>
        </div>
        <div class="chat-dock-actions">
          <button type="button" class="chat-chip" @click="answerStep = 'multi'">返回修改</button>
          <span class="spacer"></span>
          <button
            type="button"
            class="btn primary"
            :disabled="busy || !topPick"
            @click="submitAnswer('explicit')"
          >
            {{ busy ? "发送中…" : "发送给小议" }}
            <AppIcon name="arrow" :size="15" />
          </button>
        </div>
      </template>

      <!-- 确认 -->
      <template v-else-if="needConfirm">
        <div class="dock-q"><AppIcon name="shield" :size="13" /> 小议需要你的明确态度</div>
        <div class="chat-chips">
          <button type="button" class="chat-chip star-on" :disabled="busy" @click="acceptProposal">
            ✓ 接受这一版
          </button>
          <router-link :to="`/n/${id}/me/concern`" class="chat-chip">有顾虑要说</router-link>
        </div>
        <div class="chat-dock-hint">稳定不等于同意；确认只针对当前版本。</div>
      </template>

      <!-- 已确认，等他人 -->
      <template v-else-if="myConfirmed">
        <div class="chat-dock-hint">
          你已{{ myConfirmed === "accept" ? "接受" : "提出顾虑" }}，正在等其他成员表态；页面会自动更新。
        </div>
      </template>

      <template v-else>
        <div class="chat-dock-hint">
          对话会随协商进展自动更新，无需手动刷新。
        </div>
      </template>

      <!-- 发起者附加操作 -->
      <div v-if="canCloseRound" class="chat-dock-actions">
        <span class="chat-dock-hint" style="padding: 0; text-align: left; flex: 1;">
          成员都答完即可结束本轮计算
        </span>
        <button type="button" class="chat-chip" :disabled="busy" @click="closeRoundFromChat">
          <AppIcon name="retry" :size="13" /> 结束本轮并计算
        </button>
      </div>
    </div>
  </div>
</template>
