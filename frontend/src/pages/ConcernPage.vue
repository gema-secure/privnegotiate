<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import { api, errorMessage } from "../api";
import { shortDate } from "../format";
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
const submitting = ref(false);
const acknowledged = ref(false);
const text = ref("");
const submitted = ref(false);

const token = computed(() => session.current?.token ?? null);
const recommendation = computed(() => pub.value?.recommendation ?? null);
const canSubmit = computed(
  () =>
    !!pub.value &&
    !!recommendation.value &&
    pub.value.status === "CONCERN_COLLECTING" &&
    me.value?.confirmation?.decision === "reject" &&
    !submitted.value,
);
const myConcerns = computed(() => me.value?.concerns ?? []);

async function refresh() {
  if (!token.value) {
    loading.value = false;
    return;
  }
  error.value = "";
  try {
    const [publicView, myState] = await Promise.all([
      api.publicView(id, token.value),
      api.myState(id, token.value),
    ]);
    pub.value = publicView;
    me.value = myState;
    submitted.value = myState.concerns.some(
      (item) => item.result_version === publicView.recommendation?.result_version,
    );
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!me.value || !recommendation.value || !token.value || submitting.value) return;
  error.value = "";
  if (!acknowledged.value) {
    error.value = "请先确认了解本次顾虑的分享范围。";
    return;
  }
  if (!text.value.trim()) {
    error.value = "请填写你想说明的顾虑。";
    return;
  }
  submitting.value = true;
  try {
    await api.submitConcern(id, token.value, {
      result_version: recommendation.value.result_version,
      text: text.value.trim(),
      share_acknowledged: true,
    });
    submitted.value = true;
    showToast("顾虑已提交，等待发起者处理");
    await refresh();
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
    <div v-if="loading" class="panel empty-state"><p>正在读取确认状态…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要你的独立参与入口</h3>
      <p>请使用邀请链接加入后，再补充顾虑。</p>
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
        <span class="private-label"><AppIcon name="chat" :size="14" /> 只共享这一次的内容</span>
        <h1>把没被方案照顾到的，写下来。</h1>
        <p>顾虑不会自动变成硬约束，也不会替你接受当前组合。</p>
      </div>

      <section v-if="!recommendation" class="panel empty-state">
        <div class="empty-icon"><AppIcon name="clock" :size="22" /></div>
        <h3>当前没有待说明的方案</h3>
        <p>方案形成后才能补充针对该版本的顾虑。</p>
        <router-link :to="`/n/${id}`" class="btn soft mt16">回到公共看板</router-link>
      </section>

      <section v-else-if="!canSubmit && !submitted" class="panel empty-state">
        <div class="empty-icon"><AppIcon name="info" :size="22" /></div>
        <h3>还没有进入补充顾虑阶段</h3>
        <p>
          请先在私下确认页选择「我还有顾虑」。当前状态为「{{ pub.status }}」。
        </p>
        <router-link :to="`/n/${id}/me/confirm`" class="btn primary mt16">去私下确认</router-link>
      </section>

      <template v-else>
        <section v-if="submitted" class="panel">
          <div class="between panel-header">
            <h2>顾虑已提交</h2>
            <span class="badge amber">等待处理</span>
          </div>
          <p class="hint">
            发起者只能看到你明确提交的这段内容，默认不附身份，也看不到你的历史回答与完整选择链。
          </p>
          <div class="receipt-list">
            <div v-for="concern in myConcerns" :key="concern.concern_id" class="receipt-item">
              <span>v{{ concern.result_version }} · {{ shortDate(concern.created_at) }}</span>
              <strong>{{ concern.status === "open" ? "待处理" : concern.status }}</strong>
            </div>
          </div>
          <div v-for="concern in myConcerns" :key="concern.concern_id" class="concern-card">
            <span class="small-label">方案 v{{ concern.result_version }} · 你提交的原文</span>
            <blockquote>{{ concern.text }}</blockquote>
            <p class="hint">原文完整保留，不被摘要替换。</p>
          </div>
          <div class="form-actions">
            <p>等待发起者决定继续协商或保留异议结束。</p>
            <div class="actions">
              <router-link :to="`/n/${id}`" class="btn soft">回到公共看板</router-link>
              <router-link :to="`/n/${id}/me/history`" class="btn">查看我的选择链</router-link>
            </div>
          </div>
        </section>

        <section v-else class="panel">
          <div class="between panel-header">
            <h2>补充你的顾虑</h2>
            <span class="badge outline">方案 v{{ recommendation.result_version }}</span>
          </div>

          <div class="notice amber">
            <AppIcon name="info" :size="15" />
            <span>
              本次补充顾虑将交给发起者，默认不附身份；历史回答和完整选择链不会共享。
              内容本身可能让人推测身份，请自行决定如何表述。
            </span>
          </div>

          <label class="accept-line mt16">
            <input v-model="acknowledged" type="checkbox" />
            <span>我已了解上述分享范围，并同意将这段内容交给发起者。</span>
          </label>

          <div class="field mt16">
            <label for="concern-text">顾虑原文</label>
            <textarea
              id="concern-text"
              v-model="text"
              maxlength="2000"
              placeholder="例如：这个组合里没有我能吃的主食，希望下一轮能补一项。"
            />
            <span class="hint">{{ text.length }} / 2000 字 · 原文完整保留</span>
          </div>

          <div v-if="error" class="error-box" role="alert">{{ error }}</div>

          <div class="form-actions">
            <p><AppIcon name="lock" :size="14" /> 不分享其他成员的回答</p>
            <div class="actions">
              <button type="button" class="btn soft" @click="router.push(`/n/${id}/me/confirm`)">
                返回确认页
              </button>
              <button type="button" class="btn primary" :disabled="submitting" @click="submit">
                {{ submitting ? "正在提交…" : "提交本次顾虑" }}
                <AppIcon name="arrow" :size="16" />
              </button>
            </div>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>
