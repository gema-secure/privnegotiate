<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import AttributeChips from "../components/AttributeChips.vue";
import { api, errorMessage } from "../api";
import { requestId, shortDate } from "../format";
import { iconForCategory, paletteFor } from "../scenario";
import { useNegotiationState } from "../useNegotiationState";
import type { AnswerPayload } from "../types";

const route = useRoute();
const id = Number(route.params.id);
const { pub, me, history, loading, error, token, isInitiator, polling, refresh: refreshState } = useNegotiationState(id, { history: true });

const submitting = ref(false);

const accepted = ref<string[]>([]);
const top = ref<string | null>(null);
const mode = ref<"explicit" | "none" | "skip">("explicit");
const reason = ref("");
let pendingRequestId = "";

const batch = computed(() => me.value?.question_batch ?? null);
const currentEntry = computed(() =>
  history.value.find((entry) => entry.round_no === me.value?.round_no),
);
const answered = computed(() => currentEntry.value?.answer != null);
const canAnswer = computed(
  () =>
    !!batch.value &&
    !answered.value &&
    !!me.value &&
    ["ASKING", "ANSWERING"].includes(me.value.status),
);

const acceptedNames = computed(() =>
  (currentEntry.value?.answer?.acceptable_ids ?? []).map(candidateName),
);
const topName = computed(() => {
  const topId = currentEntry.value?.answer?.top_id;
  return topId ? candidateName(topId) : "";
});

/* ---------- 追问措辞来源（只影响表达，不改变题目与候选） ---------- */
const wordingMode = ref<"auto" | "template">("auto");
const wordingSwitching = ref(false);

const WORDING_REASON: Record<string, string> = {
  llm_disabled: "AI 文本能力未启用",
  llm_unavailable: "AI 服务暂时不可用",
  llm_bad_output: "AI 输出不合规",
  empty_input: "输入为空",
  no_valid_proposals: "没有可用措辞",
};

const wordingLabel = computed(() => {
  const source = batch.value?.wording_source;
  if (source === "llm") return "AI 措辞";
  if (batch.value?.wording_degraded) return "模板措辞 · AI 不可用";
  if (source === "template") return "固定模板措辞";
  return "模板措辞";
});

const wordingReasonText = computed(() => {
  const reason = batch.value?.wording_degraded_reason;
  return reason ? WORDING_REASON[reason] ?? reason : "";
});

async function useTemplateWording() {
  if (!token.value || wordingMode.value === "template" || wordingSwitching.value) return;
  wordingSwitching.value = true;
  try {
    me.value = await api.myState(id, token.value, "template");
    wordingMode.value = "template";
    showToast("已改用固定模板措辞");
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    wordingSwitching.value = false;
  }
}

// 分镜流状态：storyIndex 跟随横滑位置
const storyIndex = ref(0);
const storyItems = computed(() => batch.value?.items ?? []);

function candidateName(candidateId: string): string {
  return (
    pub.value?.candidates.find((item) => item.candidate_id === candidateId)?.name ??
    batch.value?.items.find((item) => item.candidate_id === candidateId)?.name ??
    candidateId
  );
}

function toggleAccept(candidateId: string) {
  pendingRequestId = "";
  mode.value = "explicit";
  if (accepted.value.includes(candidateId)) {
    accepted.value = accepted.value.filter((item) => item !== candidateId);
    if (top.value === candidateId) top.value = null;
  } else {
    accepted.value = [...accepted.value, candidateId];
  }
}

function chooseTop(candidateId: string) {
  pendingRequestId = "";
  if (!accepted.value.includes(candidateId)) return;
  top.value = candidateId;
}

function chooseMode(next: "none" | "skip") {
  pendingRequestId = "";
  mode.value = next;
  accepted.value = [];
  top.value = null;
}

function storyScroll(event: Event) {
  const deck = event.target as HTMLElement;
  const first = deck.firstElementChild as HTMLElement | null;
  if (!first || !deck.clientWidth) return;
  const stride = first.offsetWidth + 16;
  const index = Math.round(deck.scrollLeft / stride);
  storyIndex.value = Math.max(0, Math.min(storyItems.value.length, index));
}

function goToSummary() {
  const deck = document.getElementById("story-deck");
  if (deck) deck.scrollTo({ left: deck.scrollWidth, behavior: "smooth" });
}

async function refresh() {
  await refreshState();

}

async function submit() {
  if (!me.value || !batch.value || !token.value || submitting.value) return;
  error.value = "";
  if (mode.value === "explicit" && accepted.value.length === 0) {
    error.value = "请勾选至少一个能接受的候选，或选择「都不接受 / 暂时跳过」。";
    goToSummary();
    return;
  }
  if (mode.value === "explicit" && accepted.value.length > 0 && !top.value) {
    error.value = "请从能接受的选项中选出本轮最想要的一个。";
    goToSummary();
    return;
  }

  const displayed = batch.value.items.map((item) => item.candidate_id);
  const payload: AnswerPayload = {
    question_batch_id: batch.value.question_batch_id,
    request_id: pendingRequestId || (pendingRequestId = requestId()),
    mode: mode.value === "skip" ? "skip" : "explicit",
    evaluated_ids: mode.value === "skip" ? [] : displayed,
    acceptable_ids: mode.value === "explicit" ? accepted.value : [],
    top_id: mode.value === "explicit" ? top.value : null,
    reason: reason.value.trim() || null,
  };

  submitting.value = true;
  try {
    const result = await api.submitAnswer(id, token.value, payload);
    pendingRequestId = "";
    showToast(result.duplicate ? "已提交过相同回答，显示原回执" : "选择已提交，个人回答不会公开");
    await refresh();
  } catch (err) {
    error.value = errorMessage(err);
    goToSummary();
  } finally {
    submitting.value = false;
  }
}

// 等待本轮被发起者关闭、新题目下发时自动更新

onMounted(() => {
  void refresh();
  polling.start();
});
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取本轮题目…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要你的独立参与入口</h3>
      <p>请使用发起者发给你的邀请链接加入本次协商。</p>
      <router-link to="/" class="btn soft mt16">返回首页</router-link>
    </div>

    <div v-else-if="error && !me" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <template v-else-if="me">
      <div class="page-intro">
        <span class="private-label"><AppIcon name="lock" :size="14" /> 只有你和应用可见</span>
        <h1>这一轮，悄悄告诉它<em>你喜欢什么</em>。</h1>
        <p>一次看一个，横滑切换；点「能接受」再挑一个「最想要」。</p>
      </div>

      <section v-if="answered && currentEntry?.answer" class="panel receipt">
        <div class="between panel-header">
          <h2>本轮已提交</h2>
          <span class="badge green">第 {{ currentEntry.round_no }} 轮</span>
        </div>
        <p class="hint">
          收到于 {{ shortDate(currentEntry.answer.accepted_at) }}。无需重复提交，也不用公开解释你的选择。
        </p>
        <div class="receipt-list">
          <div class="receipt-item">
            <span>作答方式</span>
            <strong>
              {{
                currentEntry.answer.mode === "skip"
                  ? "本轮暂时跳过（未记录否定偏好）"
                  : currentEntry.answer.acceptable_ids.length
                    ? "明确评价"
                    : "这些我都不接受"
              }}
            </strong>
          </div>
          <div class="receipt-item">
            <span>能接受</span>
            <strong>{{ acceptedNames.length ? acceptedNames.join("、") : "无" }}</strong>
          </div>
          <div class="receipt-item">
            <span>本轮最想要</span>
            <strong>{{ topName || "未提供" }}</strong>
          </div>
        </div>
        <div class="form-actions">
          <p>你的回答仅本人和应用可见。</p>
          <div class="actions">
            <router-link :to="`/n/${id}`" class="btn soft">回到公共看板</router-link>
            <router-link :to="`/n/${id}/me/history`" class="btn">查看我的选择链</router-link>
          </div>
        </div>
      </section>

      <template v-else-if="canAnswer && batch">
        <div class="story-wrap" style="padding-top: 0; padding-bottom: 0; max-width: none;">
          <div class="story-head">
            <span class="badge outline">第 {{ batch.round_no }} 轮 · {{ batch.items.length }} 个候选</span>
            <div class="story-dots" aria-hidden="true">
              <i
                v-for="(item, index) in storyItems"
                :key="item.candidate_id"
                :class="{ on: index === storyIndex, done: accepted.includes(item.candidate_id) }"
              />
            </div>
          </div>

          <div class="story-prompt">
            <p v-if="batch.prompt_text">{{ batch.prompt_text }}</p>
            <p v-else>请针对本轮候选逐一作答；候选项与数量由服务端固定，措辞不影响题目。</p>
            <div class="story-prompt-meta">
              <span class="badge" :class="batch.wording_source === 'llm' ? 'green' : 'outline'">
                {{ wordingLabel }}
              </span>
              <button
                v-if="batch.wording_source === 'llm'"
                type="button"
                class="link-btn"
                :disabled="wordingSwitching"
                @click="useTemplateWording"
              >
                {{ wordingSwitching ? "切换中…" : "改用固定模板措辞" }}
              </button>
            </div>
            <p v-if="batch.wording_degraded" class="hint">
              AI 措辞不可用{{ wordingReasonText ? `（${wordingReasonText}）` : "" }}，已使用固定模板。
            </p>
            <p class="hint">措辞只影响表达；题目、候选与问题预算均由确定性逻辑决定。</p>
          </div>

          <div class="story-deck" id="story-deck" @scroll.passive="storyScroll">
            <div
              v-for="item in storyItems"
              :key="item.candidate_id"
              class="story-card"
              :class="{
                accepted: mode === 'explicit' && accepted.includes(item.candidate_id),
                istop: mode === 'explicit' && top === item.candidate_id,
              }"
              :style="{
                '--dish-tint': paletteFor(item.category, item.name).bg,
                '--glyph-ink': paletteFor(item.category, item.name).accent,
              }"
            >
              <span class="g">
                <AppIcon :name="iconForCategory(item.category, item.name)" :size="52" />
              </span>
              <div class="name">{{ item.name }}</div>
              <div class="meta">
                <b>¥{{ (item.cost_cents / 100).toFixed(2).replace(/\.00$/, "") }}</b> · {{ item.category }}
              </div>
              <AttributeChips :attributes="item.attributes" compact :max="3" />
              <div class="acts">
                <button
                  type="button"
                  class="story-btn yes"
                  :class="{ off: !(mode === 'explicit' && accepted.includes(item.candidate_id)) }"
                  @click="toggleAccept(item.candidate_id)"
                >
                  {{ mode === "explicit" && accepted.includes(item.candidate_id) ? "✓ 已接受" : "能接受" }}
                </button>
                <button
                  type="button"
                  class="story-btn star"
                  :class="{ off: !(mode === 'explicit' && accepted.includes(item.candidate_id)) }"
                  :disabled="mode !== 'explicit' || !accepted.includes(item.candidate_id)"
                  @click="chooseTop(item.candidate_id)"
                >
                  ★ 最想要
                </button>
              </div>
            </div>

            <div class="story-card summary">
              <h3>汇总一下，再提交</h3>
              <p class="hint" style="margin-top: -4px;">
                已接受 {{ mode === "explicit" ? accepted.length : 0 }} 项{{ top && mode === "explicit" ? `，最想要「${candidateName(top)}」` : "" }}。
              </p>
              <div class="inline-options" style="border-top: 0; padding-top: 4px; margin-top: 4px;">
                <label>
                  <input
                    type="radio"
                    name="answer-mode"
                    :checked="mode === 'none'"
                    @change="chooseMode('none')"
                  />
                  这些我都不接受
                </label>
                <label>
                  <input
                    type="radio"
                    name="answer-mode"
                    :checked="mode === 'skip'"
                    @change="chooseMode('skip')"
                  />
                  这轮暂时跳过
                </label>
              </div>
              <p class="hint" style="font-size: 10.5px;">跳过会保留"尚不了解"，不会被当成不喜欢。</p>
              <div class="field" style="margin-bottom: 0;">
                <label for="answer-reason">愿意多说一句吗？<span class="muted"> 选填</span></label>
                <textarea
                  id="answer-reason"
                  v-model="reason"
                  maxlength="500"
                  placeholder="比如：今晚想吃清淡一点。也可以什么都不写。"
                  @input="pendingRequestId = ''"
                />
                <span class="hint">理由只记入你的个人记录，不用于猜测你的动机。</span>
              </div>
            </div>
          </div>

          <div class="story-swipe-hint">
            <span>← 滑到上一个</span>
            <span>滑到最后是汇总卡 →</span>
          </div>
        </div>

        <div class="form-actions">
          <span class="picked-count">
            <b>{{ mode === "explicit" ? accepted.length : 0 }}</b>
            <span>项可接受{{ top && mode === "explicit" ? " · 已选最想要" : "" }}</span>
          </span>
          <div class="actions">
            <button type="button" class="btn soft" @click="goToSummary">查看汇总</button>
            <button type="button" class="btn primary" :disabled="submitting" @click="submit">
              {{ submitting ? "正在提交…" : "提交我的选择" }}
              <AppIcon name="arrow" :size="16" />
            </button>
          </div>
        </div>

        <div v-if="error" class="error-box" role="alert">{{ error }}</div>
      </template>

      <section v-else class="panel empty-state">
        <div class="empty-icon"><AppIcon name="check" :size="22" /></div>
        <h3>本轮暂时没有待答问题</h3>
        <p>
          当前状态为「{{ me.status }}」。如果你已提交，无需重复作答；页面会在本轮结束后自动更新。
        </p>
        <div class="top-actions mt16" style="justify-content: center">
          <router-link :to="`/n/${id}`" class="btn soft">回到公共看板</router-link>
          <router-link :to="`/n/${id}/me/history`" class="btn ghost">查看我的选择链</router-link>
        </div>
      </section>
    </template>
  </div>
</template>
