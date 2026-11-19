<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import AttributeChips from "../components/AttributeChips.vue";
import QrCode from "../components/QrCode.vue";
import { ApiError, api, errorMessage } from "../api";
import { parseYuan, shortDate, statusLabel, yuan } from "../format";
import { session } from "../session";
import { useNegotiationState } from "../useNegotiationState";
import type {
  AttributeState,
  Candidate,
  CandidateAttributeInput,
  OrganizerConcern,
  OrganizerDecisionResult,
} from "../types";

const route = useRoute();
const id = Number(route.params.id);
const { pub, me, history, loading, error, token, isInitiator, polling, refresh: refreshState } = useNegotiationState(id, {});

const concerns = ref<OrganizerConcern[]>([]);
const busy = ref(false);

const action = ref<"continue" | "stop">("continue");
const extraRounds = ref(1);
const budgetInput = ref("");
let budgetInitialized = false;

const decisionNotice = ref<{
  tone: "info" | "warn";
  title: string;
  text: string;
} | null>(null);

const openConcerns = computed(() => concerns.value.filter((item) => item.status === "open"));
const decidable = computed(
  () =>
    !!pub.value &&
    [
      "CONCERN_COLLECTING",
      "PRIVATE_CONFIRM",
      "BEST_EFFORT",
      "INFEASIBLE",
      "RETRY_OR_REVIEW",
      "ADJUSTING",
    ].includes(pub.value.status),
);
const canRetry = computed(() => ["RETRY_OR_REVIEW", "CLOSING", "CHECKING_FEASIBILITY"].includes(pub.value?.status ?? ""));
const inviteLink = computed(() => {
  const invite = session.current?.inviteToken;
  if (!invite) return "";
  return `${window.location.origin}/join/${id}/${invite}`;
});

/* ---------- 候选属性事实（发起者人工修正） ---------- */
const editingCandidateId = ref<string | null>(null);
const attrEdits = ref<CandidateAttributeInput[]>([]);
const newAttrKey = ref("");
const newAttrState = ref<AttributeState>("present");
const attrReason = ref("");
const attrBusy = ref(false);

const ATTR_OPTIONS: Array<{ value: AttributeState; label: string }> = [
  { value: "present", label: "已知含" },
  { value: "absent", label: "已知不含" },
  { value: "unknown", label: "未知" },
];

const markedCount = computed(
  () => (pub.value?.candidates ?? []).filter((item) => item.attributes.length > 0).length,
);

/* ---------- 顾虑摘要（发起者人工修正） ---------- */
const editingConcernId = ref<number | null>(null);
const summaryDraft = ref("");
const summaryBusy = ref(false);

const SUMMARY_SOURCE_LABEL: Record<OrganizerConcern["summary_source"], string> = {
  llm: "AI 摘要",
  manual: "人工修正摘要",
  none: "无摘要",
};

const CONCERN_REASON: Record<string, string> = {
  llm_disabled: "AI 文本能力未启用",
  llm_unavailable: "AI 服务暂时不可用",
  llm_bad_output: "AI 输出不合规",
  empty_input: "输入为空",
};

function concernDegradedText(concern: OrganizerConcern): string {
  const reason = concern.summary_degraded_reason;
  const text = reason ? CONCERN_REASON[reason] ?? reason : "";
  return text ? `（${text}）` : "";
}

function startEditSummary(concern: OrganizerConcern) {
  if (editingConcernId.value === concern.concern_id) {
    editingConcernId.value = null;
    return;
  }
  editingConcernId.value = concern.concern_id;
  summaryDraft.value = concern.summary ?? "";
}

async function saveSummary(concern: OrganizerConcern) {
  if (!token.value || summaryBusy.value) return;
  const summary = summaryDraft.value.trim();
  if (!summary) {
    showToast("摘要不能为空");
    return;
  }
  summaryBusy.value = true;
  try {
    const updated = await api.correctConcernSummary(
      id,
      concern.concern_id,
      token.value,
      summary,
    );
    const index = concerns.value.findIndex((item) => item.concern_id === concern.concern_id);
    if (index >= 0) concerns.value[index] = updated;
    editingConcernId.value = null;
    showToast("摘要已更新，来源记为人工");
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    summaryBusy.value = false;
  }
}

function startEdit(candidate: Candidate) {
  if (editingCandidateId.value === candidate.candidate_id) {
    editingCandidateId.value = null;
    return;
  }
  editingCandidateId.value = candidate.candidate_id;
  attrEdits.value = candidate.attributes.map((attr) => ({
    key: attr.key,
    state: attr.state,
    source: attr.source,
  }));
  newAttrKey.value = "";
  newAttrState.value = "present";
  attrReason.value = "";
}

function addEditAttribute() {
  const key = newAttrKey.value.trim();
  if (!key) {
    showToast("请填写属性名");
    return;
  }
  if (key.length > 64) {
    showToast("属性名最多 64 个字符");
    return;
  }
  if (attrEdits.value.some((item) => item.key === key)) {
    showToast(`已有属性「${key}」`);
    return;
  }
  attrEdits.value.push({ key, state: newAttrState.value, source: "correction" });
  newAttrKey.value = "";
}

async function saveAttributes() {
  const candidate = pub.value?.candidates.find(
    (item) => item.candidate_id === editingCandidateId.value,
  );
  if (!candidate || !token.value || attrBusy.value) return;
  const original = new Map(candidate.attributes.map((attr) => [attr.key, attr.state]));
  const changed = attrEdits.value
    .filter((attr) => original.get(attr.key) !== attr.state)
    .map((attr) => ({
      key: attr.key,
      state: attr.state,
      source: "correction" as const,
    }));
  if (!changed.length) {
    showToast("没有需要保存的修改");
    return;
  }
  attrBusy.value = true;
  try {
    await api.correctCandidateAttributes(id, candidate.candidate_id, token.value, {
      attributes: changed,
      reason: attrReason.value.trim() || null,
    });
    showToast("属性事实已更新，相关判定会重新计算");
    editingCandidateId.value = null;
    await refresh();
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    attrBusy.value = false;
  }
}

async function copyInvite() {
  if (!inviteLink.value) return;
  try {
    await navigator.clipboard.writeText(inviteLink.value);
    showToast("邀请链接已复制");
  } catch {
    showToast("复制受限，请手动选择链接复制");
  }
}

function initializeBudget() {
  if (budgetInitialized || !pub.value) return;
  budgetInitialized = true;
  budgetInput.value = yuan(pub.value.conditions.budget_cents);
}

async function refresh() {
  await refreshState();
  if (isInitiator.value && token.value && !error.value) { try { concerns.value = await api.organizerConcerns(id, token.value); } catch (err) { error.value = errorMessage(err); } } initializeBudget();
}

async function retryCompute() {
  if (!token.value || !pub.value || busy.value) return;
  busy.value = true;
  try {
    const result = await api.closeRound(id, pub.value.round_no, token.value);
    showToast(result.message || "已发起重试");
    await refresh();
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    busy.value = false;
  }
}

function continueDecisionNotice(result: OrganizerDecisionResult) {
  const text = result.message || "";
  switch (result.outcome) {
    case "round_issued":
      return null;
    case "no_new_questions":
      return {
        tone: "warn" as const,
        title: "条件已生效，但本轮没有新增作答",
        text: text || "公共条件已更新，但没有新的可问问题；本轮未发出新的作答请求。",
      };
    case "infeasible":
      return {
        tone: "warn" as const,
        title: "条件已生效，但当前仍找不到可行组合",
        text: text || "公共条件已更新，但本次计算没有可行方案，请继续调整条件。",
      };
    case "retry":
      return {
        tone: "info" as const,
        title: "条件已生效，但本轮尚未发出新题",
        text: text || "公共条件已更新；本轮计算等待重试，还没有新的作答请求。",
      };
    default:
      return {
        tone: "info" as const,
        title: "公共条件已生效",
        text: text || "操作已完成，请以当前页面状态为准。",
      };
  }
}

async function submitDecision() {
  if (!token.value || !pub.value || busy.value) return;
  error.value = "";

  let adjustment: { budget_cents: number } | null = null;
  if (action.value === "continue") {
    const cents = parseYuan(budgetInput.value);
    if (!Number.isFinite(cents) || cents <= 0) {
      error.value = "请填写有效的总预算。";
      return;
    }
    if (cents !== pub.value.conditions.budget_cents) {
      adjustment = { budget_cents: cents };
    }
    if (!Number.isInteger(extraRounds.value) || extraRounds.value < 1 || extraRounds.value > 10) {
      error.value = "追加轮数需为 1–10 的整数。";
      return;
    }
  } else if (!openConcerns.value.length) {
    error.value = "当前没有待处理的顾虑。如需重新计算，请使用「重试计算」。";
    return;
  }

  try {
    await showConfirmDialog({
      title: action.value === "continue" ? "调整并继续协商" : "保留异议并结束",
      message:
        action.value === "continue"
          ? "公共条件调整后旧版本的确认会失效，应用会重新计算并再次请求成员确认；若没有可问的问题会明确提示，不会静默增加轮数。"
          : "本次协商将以「存在未解决顾虑」结束，公开方案保留，但不会标记为全员同意。",
      confirmButtonText: action.value === "continue" ? "继续协商" : "结束本次协商",
    });
  } catch {
    return;
  }

  busy.value = true;
  try {
    if (action.value === "continue") {
      const result = await api.organizerDecision(id, token.value, {
        action: "continue",
        concern_id: openConcerns.value[0]?.concern_id ?? null,
        extra_rounds: extraRounds.value,
        public_adjustment: adjustment,
      });
      decisionNotice.value = continueDecisionNotice(result);
      if (result.outcome === "round_issued") {
        showToast(`已进入第 ${result.round_no} 轮，等待成员作答`);
      }
    } else {
      await api.organizerDecision(id, token.value, {
        action: "stop",
        concern_id: openConcerns.value[0]?.concern_id ?? null,
      });
      decisionNotice.value = null;
      showToast("已结束，异议记录已保留");
    }
    action.value = "continue";
    extraRounds.value = 1;
    await refresh();
  } catch (err) {
    if (err instanceof ApiError && err.code === "NO_QUESTIONS_TO_ASK") {
      decisionNotice.value = null;
      error.value = `没有可问的问题，条件未变更。${err.message}`;
    } else {
      error.value = errorMessage(err);
    }
  } finally {
    busy.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取协商设置…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要发起者身份</h3>
      <p>请使用创建协商时保存的身份进入。</p>
      <router-link to="/" class="btn soft mt16">返回首页</router-link>
    </div>

    <div v-else-if="!isInitiator" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="shield" :size="22" /></div>
      <h3>这里只对发起者开放</h3>
      <p>成员可以查看公共条件，但无权处理补充顾虑或调整公共规则。</p>
      <router-link :to="`/n/${id}`" class="btn soft mt16">回到公共看板</router-link>
    </div>

    <div v-else-if="error && !pub" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <template v-else-if="pub && me">
      <div class="page-intro">
        <span class="eyebrow">FOR THE WHOLE TABLE</span>
        <h1>主持协商，但不替别人决定。</h1>
        <p>你可以调整公共条件、处理获准分享的顾虑。个人底线和完整回答始终归成员本人。</p>
      </div>

      <section v-if="inviteLink && !pub.membership?.frozen" class="panel">
        <div class="between panel-header">
          <div>
            <h2>邀请成员</h2>
            <p class="hint mt8">当面聚餐时让对方直接扫二维码，或把链接发到群里。</p>
          </div>
          <button type="button" class="btn soft small" @click="copyInvite">
            <AppIcon name="copy" :size="14" />
            复制链接
          </button>
        </div>
        <div class="invite-qr">
          <QrCode :value="inviteLink" :size="176" />
          <p class="hint">同一个链接可邀请多位成员，各自获得独立身份</p>
        </div>
      </section>

      <section class="panel mt16">
        <div class="between panel-header">
          <div>
            <h2>公共条件</h2>
            <p class="hint mt8">当前状态「{{ statusLabel(pub.status) }}」 · 第 {{ pub.round_no }} / {{ pub.conditions.round_limit }} 轮</p>
          </div>
          <span class="badge outline">条件 v{{ pub.conditions.condition_version }}</span>
        </div>

        <div class="summary">
          <div class="metric">
            <div class="metric-label"><AppIcon name="wallet" :size="15" /> 总预算</div>
            <div class="metric-value">¥{{ yuan(pub.conditions.budget_cents) }}</div>
            <p class="metric-desc">全组总额，不是人均</p>
          </div>
          <div class="metric">
            <div class="metric-label"><AppIcon name="plate" :size="15" /> 目标数量</div>
            <div class="metric-value">{{ pub.conditions.exact_count }} <small>项</small></div>
            <p class="metric-desc">{{ pub.candidates.length }} 个候选</p>
          </div>
          <div class="metric">
            <div class="metric-label"><AppIcon name="clock" :size="15" /> 轮数上限</div>
            <div class="metric-value">{{ pub.conditions.round_limit }} <small>轮</small></div>
            <p class="metric-desc">达到上限仍需全员确认</p>
          </div>
          <div class="metric">
            <div class="metric-label"><AppIcon name="users" :size="15" /> 小组人数</div>
            <div class="metric-value">{{ pub.conditions.member_count }} <small>人</small></div>
            <p class="metric-desc">创建时声明的规模</p>
          </div>
        </div>

        <div class="notice mt24">
          <AppIcon name="info" :size="15" />
          <span>
            公共条件只在处理顾虑或继续协商时调整；任何实质修改都会使旧版本的成员确认失效，
            并且只影响公共约束，不会放宽个人底线。
          </span>
        </div>

        <div v-if="canRetry" class="form-actions">
          <p>上次计算未完成，可基于同一批已确认条件重试。</p>
          <div class="actions">
            <button type="button" class="btn primary" :disabled="busy" @click="retryCompute">
              <AppIcon name="retry" :size="16" />
              {{ busy ? "重试中…" : "重试计算" }}
            </button>
          </div>
        </div>
      </section>

      <section class="panel mt16">
        <div class="between panel-header">
          <div>
            <h2>候选属性事实</h2>
            <p class="hint mt8">
              三态：已知含 / 已知不含 / 未知。人工修正只改事实，不会改动任何成员的底线，也不会替成员做判断。
            </p>
          </div>
          <span class="badge outline">{{ markedCount }} / {{ pub.candidates.length }} 已标注</span>
        </div>

        <div class="attr-fact-list">
          <div v-for="candidate in pub.candidates" :key="candidate.candidate_id" class="attr-fact">
            <div class="attr-fact-main">
              <div class="attr-fact-name">
                <strong>{{ candidate.name }}</strong>
                <span class="hint"> · {{ candidate.category }} · ¥{{ yuan(candidate.cost_cents) }}</span>
              </div>
              <AttributeChips :attributes="candidate.attributes" show-source compact />
            </div>
            <button
              v-if="isInitiator"
              type="button"
              class="btn ghost small"
              @click="startEdit(candidate)"
            >
              {{ editingCandidateId === candidate.candidate_id ? "收起" : "修正属性" }}
            </button>
          </div>
        </div>

        <div v-if="editingCandidateId" class="attr-edit-panel">
          <p class="hint">当前编辑的修改会以「修正」来源留痕；未知可以保留，不必强行填成不含。</p>
          <div class="attr-edit-grid">
            <div v-for="(attr, index) in attrEdits" :key="attr.key" class="attr-edit-row">
              <b>{{ attr.key }}</b>
              <select v-model="attrEdits[index].state" :aria-label="`${attr.key} 的状态`">
                <option v-for="option in ATTR_OPTIONS" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
            </div>
            <div class="attr-edit-row">
              <input v-model="newAttrKey" maxlength="64" placeholder="新增属性名" />
              <select v-model="newAttrState" aria-label="新增属性状态">
                <option v-for="option in ATTR_OPTIONS" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
              <button type="button" class="btn soft small" @click="addEditAttribute">添加</button>
            </div>
          </div>
          <input
            v-model="attrReason"
            maxlength="500"
            placeholder="修正原因（可选，仅用于留痕）"
            class="attr-reason"
          />
          <div class="actions mt8">
            <button type="button" class="btn ghost" @click="editingCandidateId = null">取消</button>
            <button type="button" class="btn primary" :disabled="attrBusy" @click="saveAttributes">
              {{ attrBusy ? "保存中…" : "保存属性事实" }}
            </button>
          </div>
        </div>
      </section>

      <section class="panel mt16">
        <div class="between panel-header">
          <div>
            <h2>获准分享的补充顾虑</h2>
            <p class="hint mt8">仅显示成员明确同意交给你的本次内容，不附带身份与历史回答。</p>
          </div>
          <span class="badge" :class="openConcerns.length ? 'amber' : ''">
            {{ openConcerns.length }} 条待处理
          </span>
        </div>

        <div v-if="!concerns.length" class="empty-state">
          <div class="empty-icon"><AppIcon name="chat" :size="22" /></div>
          <h3>还没有人补充顾虑</h3>
          <p>成员在私下确认页选择「我还有顾虑」并提交后，内容会出现在这里。</p>
        </div>

        <div v-else>
          <article v-for="concern in concerns" :key="concern.concern_id" class="concern-card">
            <div class="between">
              <span class="concern-meta">
                <span class="badge" :class="concern.status === 'open' ? 'amber' : 'green'">
                  {{ concern.status === "open" ? "待处理" : "已处理" }}
                </span>
                方案 v{{ concern.result_version }} · {{ shortDate(concern.created_at) }}
              </span>
              <span class="small-label">不附身份</span>
            </div>
            <blockquote>{{ concern.text }}</blockquote>
            <div class="concern-summary">
              <div class="between">
                <span class="small-label">摘要</span>
                <span
                  class="badge"
                  :class="concern.summary_source === 'llm' ? 'green' : 'outline'"
                >
                  {{ SUMMARY_SOURCE_LABEL[concern.summary_source] }}
                </span>
              </div>
              <template v-if="editingConcernId === concern.concern_id">
                <textarea
                  v-model="summaryDraft"
                  rows="3"
                  maxlength="200"
                  class="summary-input"
                  placeholder="改写摘要；原文不会被修改"
                ></textarea>
                <div class="actions mt8">
                  <button type="button" class="btn ghost small" @click="editingConcernId = null">
                    取消
                  </button>
                  <button
                    type="button"
                    class="btn primary small"
                    :disabled="summaryBusy"
                    @click="saveSummary(concern)"
                  >
                    {{ summaryBusy ? "保存中…" : "保存摘要" }}
                  </button>
                </div>
              </template>
              <p v-else-if="concern.summary">{{ concern.summary }}</p>
              <p v-else class="hint">没有摘要，仅展示原文；可手工补充。</p>
              <p v-if="concern.summary_degraded && concern.summary_source !== 'manual'" class="hint">
                AI 摘要不可用{{ concernDegradedText(concern) }}，可手工修正。
              </p>
            </div>
            <button
              v-if="isInitiator && editingConcernId !== concern.concern_id"
              type="button"
              class="link-btn"
              @click="startEditSummary(concern)"
            >
              修正摘要
            </button>
            <p class="hint">原文完整保留，未以摘要替换。</p>
          </article>
        </div>

        <div v-if="!decidable" class="notice mt16">
          <AppIcon name="clock" :size="15" />
          <span>当前状态不接受发起者决策。等待成员确认或本轮计算结束后再处理。</span>
        </div>

        <template v-else>
          <div class="divider" />
          <h3>如何处理</h3>
          <div class="inline-options">
            <label>
              <input v-model="action" type="radio" name="decision" value="continue" />
              调整条件并继续
            </label>
            <label>
              <input v-model="action" type="radio" name="decision" value="stop" />
              保留异议并结束
            </label>
          </div>

          <div v-if="action === 'continue'" class="field-grid mt16">
            <div class="field">
              <label for="manage-budget">总预算 / 元</label>
              <input id="manage-budget" v-model="budgetInput" type="number" min="1" step="0.01" />
              <span class="hint">修改后将重新计算，并清除旧版本确认。</span>
            </div>
            <div class="field">
              <label for="manage-rounds">追加轮数</label>
              <input id="manage-rounds" v-model.number="extraRounds" type="number" min="1" max="10" />
              <span class="hint">1–10 轮。继续协商必须追加轮数或调整公共条件。</span>
            </div>
          </div>
          <div v-else class="notice amber mt16">
            <AppIcon name="warning" :size="15" />
            <span>
              结束时公开方案会保留，但结果标记为「存在未解决顾虑」，不会显示为全员同意。
            </span>
          </div>

          <div v-if="action === 'continue'" class="notice mt16">
            <AppIcon name="shield" :size="15" />
            <span>涉及个人限制的修改仍回到本人确认；发起者不能代替成员放宽底线。</span>
          </div>

          <div
            v-if="decisionNotice"
            class="notice decision-notice mt16"
            :class="decisionNotice.tone === 'warn' ? 'amber' : ''"
            role="status"
          >
            <AppIcon
              :name="decisionNotice.tone === 'warn' ? 'warning' : 'info'"
              :size="15"
            />
            <span>
              <b>{{ decisionNotice.title }}</b>
              <span>{{ decisionNotice.text }}</span>
            </span>
          </div>

          <div v-if="error" class="error-box" role="alert">{{ error }}</div>

          <div class="form-actions">
            <p>你的操作会写入协商事件记录。</p>
            <div class="actions">
              <button type="button" class="btn soft" @click="refresh">刷新</button>
              <button
                type="button"
                class="btn"
                :class="action === 'stop' ? 'danger' : 'primary'"
                :disabled="busy"
                @click="submitDecision"
              >
                {{ busy ? "处理中…" : action === "continue" ? "继续协商" : "结束并保留异议" }}
              </button>
            </div>
          </div>
        </template>
      </section>
    </template>
  </div>
</template>
