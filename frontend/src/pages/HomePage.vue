<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import PublicHeader from "../components/PublicHeader.vue";
import AppIcon from "../components/AppIcon.vue";
import { api, errorMessage } from "../api";
import { statusLabel } from "../format";
import { SCENARIO_PRESETS } from "../scenario";
import { getSession } from "../session";
import type { PublicNegotiation } from "../types";

const router = useRouter();
const link = ref("");
const error = ref("");

interface SessionRow {
  negotiationId: number;
  displayName: string;
  role: string;
  pub: PublicNegotiation | null;
}

const sessions = ref<SessionRow[]>([]);

function collectSessions(): SessionRow[] {
  const rows: SessionRow[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    const matched = key?.match(/^hey-i\.member\.(\d+)$/);
    if (!matched) continue;
    const info = getSession(Number(matched[1]));
    if (!info) continue;
    rows.push({
      negotiationId: Number(matched[1]),
      displayName: info.displayName,
      role: info.role,
      pub: null,
    });
  }
  return rows.sort((left, right) => right.negotiationId - left.negotiationId);
}

async function loadSessions() {
  sessions.value = collectSessions();
  await Promise.all(
    sessions.value.map(async (row) => {
      try {
        row.pub = await api.publicView(row.negotiationId);
      } catch {
        row.pub = null;
      }
    }),
  );
}

const presets = SCENARIO_PRESETS;

function openInvite() {
  error.value = "";
  const matched = link.value
    .trim()
    .match(/\/join\/(\d+)\/([A-Za-z0-9_-]+)/);
  if (!matched) {
    error.value = "请粘贴发起者给你的完整邀请链接，例如 https://…/join/12/xxxx";
    return;
  }
  router.push(`/join/${matched[1]}/${matched[2]}`);
}

onMounted(() => {
  void loadSessions();
});
</script>

<template>
  <div class="home-wrap">
    <PublicHeader />

    <main class="home-main">
      <section class="home-hero">
        <span class="eyebrow">TALK IT OUT, PRIVATELY</span>
        <h1 class="mt16">和「小议」聊几句，<br />把这次共同决定定下来。</h1>
        <p>
          小议是你们的中立仲裁者：它私下问每个人想要什么，守住预算与底线，
          算出大家都接受的方案——成员之间互相看不到回答。
        </p>
        <div class="new-chat-row">
          <router-link
            v-for="preset in presets"
            :key="preset.key"
            :to="`/negotiations/new?scenario=${preset.key}`"
            class="btn primary"
          >
            {{ preset.emoji }} 发起{{ preset.label }}协商
          </router-link>
          <router-link to="/negotiations/new" class="btn">自定义发起</router-link>
        </div>
      </section>

      <section id="join" class="panel mt24">
        <div class="between">
          <div>
            <h2>收到邀请了？</h2>
            <p class="muted mt8" style="font-size: 12px">
              粘贴发起者发给你的链接，开始你和小议的私聊。请勿使用他人转发的入口。
            </p>
          </div>
          <span class="badge outline">独立身份</span>
        </div>
        <div class="join-box">
          <input
            v-model="link"
            class="input"
            type="text"
            placeholder="粘贴邀请链接，例如 https://…/join/12/xxxx"
            aria-label="邀请链接"
            @keyup.enter="openInvite"
          />
          <button type="button" class="btn primary" @click="openInvite">进入对话</button>
        </div>
        <div v-if="error" class="error-box" role="alert">{{ error }}</div>
      </section>

      <section class="mt24">
        <div class="between" style="margin-bottom: 4px;">
          <h2>我的对话</h2>
          <span class="small-label">本设备上的协商</span>
        </div>
        <div v-if="sessions.length" class="session-list">
          <router-link
            v-for="row in sessions"
            :key="row.negotiationId"
            :to="`/n/${row.negotiationId}`"
            class="session-item"
          >
            <div class="avatar-lg">议</div>
            <div class="mid">
              <div class="t">{{ row.pub?.title ?? `协商 #${row.negotiationId}` }}</div>
              <div class="d">
                {{ row.pub ? `${statusLabel(row.pub.status)} · 第 ${row.pub.round_no} 轮` : "暂无法读取" }}
              </div>
            </div>
            <div class="r">
              <div class="round">{{ row.role === "initiator" ? "发起者" : "参与者" }}</div>
              <div class="round" style="margin-top: 3px;">{{ row.displayName }}</div>
            </div>
          </router-link>
        </div>
        <div v-else class="empty-state" style="padding: 30px 20px;">
          <p>还没有进行中的对话。发起一次，或用邀请链接加入。</p>
        </div>
      </section>

      <div class="home-grid">
        <article class="feature-card">
          <span class="badge green">私下表达</span>
          <h3 class="mt8">个人回答不公开</h3>
          <p>小议只转交公共结论；发起者看不到你的完整回答、理由和逐项偏好。</p>
        </article>
        <article class="feature-card">
          <span class="badge green">可收敛</span>
          <h3 class="mt8">逐轮收窄，不无限追问</h3>
          <p>组合连续两轮不变或达到轮数上限即进入确认，每轮只问少量候选。</p>
        </article>
        <article class="feature-card">
          <span class="badge green">明确确认</span>
          <h3 class="mt8">稳定不等于同意</h3>
          <p>必须每位成员针对同一版本明确接受，结果才标记为全员确认。</p>
        </article>
      </div>

      <p class="home-foot">和议 HEYI · 听见每一种偏好，让相聚刚刚好。</p>
    </main>
  </div>
</template>
