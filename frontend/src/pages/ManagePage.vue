<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import QrCode from "../components/QrCode.vue";
import { api, errorMessage } from "../api";
import { parseYuan, shortDate, statusLabel, yuan } from "../format";
import { loadSession, session } from "../session";
import type { MyState, OrganizerConcern, PublicNegotiation } from "../types";

const route = useRoute();
const id = Number(route.params.id);
loadSession(id);

const pub = ref<PublicNegotiation | null>(null);
const me = ref<MyState | null>(null);
const concerns = ref<OrganizerConcern[]>([]);
const loading = ref(true);
const error = ref("");
const busy = ref(false);

const action = ref<"continue" | "stop">("continue");
const extraRounds = ref(1);
const budgetInput = ref("");
let budgetInitialized = false;

const token = computed(() => session.current?.token ?? null);
const isInitiator = computed(() => session.current?.role === "initiator");
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
const canRetry = computed(() => pub.value?.status === "RETRY_OR_REVIEW");
const inviteLink = computed(() => {
  const invite = session.current?.inviteToken;
  if (!invite) return "";
  return `${window.location.origin}/join/${id}/${invite}`;
});

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
    if (isInitiator.value) {
      concerns.value = await api.organizerConcerns(id, token.value);
    }
    initializeBudget();
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
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
          ? "公共条件调整后旧版本的确认会失效，应用会重新计算并再次请求成员确认。"
          : "本次协商将以「存在未解决顾虑」结束，公开方案保留，但不会标记为全员同意。",
      confirmButtonText: action.value === "continue" ? "继续协商" : "结束本次协商",
    });
  } catch {
    return;
  }

  busy.value = true;
  try {
    if (action.value === "continue") {
      await api.organizerDecision(id, token.value, {
        action: "continue",
        concern_id: openConcerns.value[0]?.concern_id ?? null,
        extra_rounds: extraRounds.value,
        public_adjustment: adjustment,
      });
    } else {
      await api.organizerDecision(id, token.value, {
        action: "stop",
        concern_id: openConcerns.value[0]?.concern_id ?? null,
      });
    }
    showToast(
      action.value === "continue" ? "已继续协商，等待重新计算" : "已结束，异议记录已保留",
    );
    action.value = "continue";
    extraRounds.value = 1;
    await refresh();
  } catch (err) {
    error.value = errorMessage(err);
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

      <section v-if="inviteLink" class="panel">
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
