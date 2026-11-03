<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppIcon from "../components/AppIcon.vue";
import PublicHeader from "../components/PublicHeader.vue";
import { api, errorMessage } from "../api";
import { statusLabel, yuan } from "../format";
import { getSession, saveSession } from "../session";
import type { PublicNegotiation } from "../types";

const route = useRoute();
const router = useRouter();
const negotiationId = Number(route.params.negotiationId);
const inviteToken = String(route.params.inviteToken ?? "");

const pub = ref<PublicNegotiation | null>(null);
const loading = ref(true);
const invalid = ref(false);
const name = ref("");
const error = ref("");
const submitting = ref(false);

const existing = getSession(negotiationId);

onMounted(async () => {
  try {
    pub.value = await api.publicView(negotiationId);
  } catch {
    invalid.value = true;
  } finally {
    loading.value = false;
  }
});

async function join() {
  error.value = "";
  const displayName = name.value.trim();
  if (!displayName || displayName.length > 32) {
    error.value = "请填写 1–32 字的称呼。";
    return;
  }
  submitting.value = true;
  try {
    const result = await api.join(negotiationId, {
      invite_token: inviteToken,
      display_name: displayName,
    });
    saveSession(negotiationId, {
      memberId: result.member_id,
      token: result.member_token,
      displayName: result.display_name,
      role: result.role,
    });
    router.replace(`/n/${negotiationId}`);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="home-wrap">
    <PublicHeader />
    <main class="home-main" style="max-width: 820px">
      <div v-if="loading" class="panel empty-state">
        <p>正在读取协商信息…</p>
      </div>

      <div v-else-if="invalid" class="panel empty-state">
        <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
        <h3>这个参与入口已失效</h3>
        <p>当前链接已过期或被撤回。请联系发起者获取新的独立入口；已有个人记录不会因此公开。</p>
        <router-link to="/" class="btn soft mt16">返回公共入口</router-link>
      </div>

      <template v-else-if="pub">
        <div class="page-intro">
          <span class="eyebrow">A SEAT AT THE TABLE</span>
          <h1>你的一票，先为自己而选。</h1>
          <p>
            发起者邀请你加入「{{ pub.title }}」。先了解共同条件，再确认你的参与范围。
          </p>
        </div>

        <div v-if="existing" class="notice amber mt16">
          <AppIcon name="info" :size="15" />
          <span>
            本设备已有「{{ existing.displayName }}」的参与身份。
            <router-link :to="`/n/${negotiationId}`" class="link-btn">直接进入公共看板</router-link>
          </span>
        </div>

        <section class="panel">
          <div class="between">
            <h2>{{ pub.title }}</h2>
            <span class="badge" :class="{ green: pub.status === 'ASKING' || pub.status === 'ANSWERING' }">
              {{ statusLabel(pub.status) }}
            </span>
          </div>
          <div class="receipt-list">
            <div class="receipt-item">
              <span>总预算</span><strong>不超过 ¥{{ yuan(pub.conditions.budget_cents) }}</strong>
            </div>
            <div class="receipt-item">
              <span>目标数量</span><strong>{{ pub.conditions.exact_count }} 项</strong>
            </div>
            <div class="receipt-item">
              <span>候选数量</span><strong>{{ pub.candidates.length }} 个</strong>
            </div>
            <div class="receipt-item">
              <span>轮数上限</span><strong>{{ pub.conditions.round_limit }} 轮</strong>
            </div>
          </div>

          <div class="field mt24">
            <label for="join-name">你的称呼</label>
            <input id="join-name" v-model="name" maxlength="32" placeholder="大家熟悉的称呼即可" autocomplete="nickname" @keyup.enter="join" />
            <span class="hint">称呼用于参与协商，不会公开你的完整回答。</span>
          </div>

          <div v-if="error" class="error-box" role="alert">{{ error }}</div>

          <div class="form-actions">
            <p><AppIcon name="lock" :size="14" /> 请保管好自己的独立参与入口。</p>
            <div class="actions">
              <button type="button" class="btn primary" :disabled="submitting" @click="join">
                {{ submitting ? "正在加入…" : "加入并确认我的底线" }}
                <AppIcon name="arrow" :size="16" />
              </button>
            </div>
          </div>
        </section>

        <div class="notice mt16">
          <AppIcon name="shield" :size="15" />
          <span>
            加入后先确认两类底线：整组必须避开的候选项，以及只需你本人不参与的候选项。
            发起者无权代替你修改或放宽。
          </span>
        </div>
      </template>
    </main>
  </div>
</template>
