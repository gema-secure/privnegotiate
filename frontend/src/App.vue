<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute } from "vue-router";
import { showToast } from "vant";
import AppIcon from "./components/AppIcon.vue";
import { loadSession, session } from "./session";

const route = useRoute();

const negotiationId = computed(() => {
  const value = Number(route.params.id);
  return Number.isFinite(value) && value > 0 ? value : null;
});

watch(
  negotiationId,
  (id) => {
    if (id !== null) loadSession(id);
  },
  { immediate: true },
);

interface NavItem {
  key: string;
  label: string;
  icon: string;
  to: string;
}

const navItems = computed<NavItem[]>(() => {
  const id = negotiationId.value;
  if (id === null) return [];
  const role = session.current?.role;
  const items: NavItem[] = [
    { key: "board", label: "协商对话", icon: "chat", to: `/n/${id}` },
    { key: "answer", label: "私下作答", icon: "edit", to: `/n/${id}/me/answer` },
    { key: "history", label: "我的选择链", icon: "clock", to: `/n/${id}/me/history` },
    { key: "bottomlines", label: "我的底线", icon: "shield", to: `/n/${id}/me/bottomlines` },
  ];
  if (role === "initiator") {
    items.push({ key: "manage", label: "协商设置", icon: "settings", to: `/n/${id}/manage` });
  }
  items.push({ key: "result", label: "协商结果", icon: "check", to: `/n/${id}/result` });
  return items;
});

const currentKey = computed(() => String(route.name ?? ""));
const currentLabel = computed(
  () => navItems.value.find((item) => item.key === currentKey.value)?.label ?? "协商",
);

const member = computed(() => session.current);
const inviteLink = computed(() => {
  const id = negotiationId.value;
  const invite = session.current?.inviteToken;
  if (id === null || !invite) return "";
  return `${window.location.origin}/join/${id}/${invite}`;
});

// 移动端底栏第二格按角色直达：发起者→协商设置，成员→我的底线
const secondaryNav = computed(() => {
  const id = negotiationId.value;
  if (id === null) return null;
  if (session.current?.role === "initiator") {
    return { to: `/n/${id}/manage`, icon: "settings", label: "协商设置", key: "manage" };
  }
  return { to: `/n/${id}/me/bottomlines`, icon: "shield", label: "我的底线", key: "bottomlines" };
});

async function copyInvite() {
  if (!inviteLink.value) return;
  try {
    await navigator.clipboard.writeText(inviteLink.value);
    showToast("邀请链接已复制，请发给每位成员");
  } catch {
    showToast("复制受限，请手动选择邀请链接复制");
  }
}
</script>

<template>
  <template v-if="negotiationId !== null">
    <header class="stage-top">
      <router-link to="/" class="brand" aria-label="和议，返回首页">
        <div class="brandmark"><AppIcon name="chat" :size="20" /></div>
        <div>
          <div class="brand-name">和议</div>
          <small>私密协商器</small>
        </div>
      </router-link>
      <nav class="stage-nav" aria-label="主要导航">
        <router-link
          v-for="item in navItems"
          :key="item.key"
          :to="item.to"
          class="pill"
          :class="{ active: currentKey === item.key }"
          :aria-current="currentKey === item.key ? 'page' : undefined"
        >
          <AppIcon :name="item.icon" :size="15" />
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="stage-user">
        <button
          v-if="member?.role === 'initiator' && inviteLink"
          type="button"
          class="btn soft small"
          @click="copyInvite"
        >
          <AppIcon name="users" :size="14" />
          邀请成员
        </button>
        <div v-if="member" class="who">
          <div class="avatar" aria-hidden="true">{{ member.displayName.slice(0, 1) }}</div>
          <div class="who-t">
            {{ member.displayName }}
            <small>{{ member.role === "initiator" ? "发起者 · 同时参与作答" : "参与者" }}</small>
          </div>
        </div>
        <router-link v-else to="/" class="link-btn">去加入</router-link>
      </div>
    </header>
    <main id="main" class="stage-main" tabindex="-1">
      <router-view />
    </main>

    <nav class="bottom-nav" aria-label="移动端主要导航">
      <router-link
        v-for="item in navItems.filter((i) => ['board', 'answer', 'history'].includes(i.key))"
        :key="item.key"
        :to="item.to"
        :class="{ active: currentKey === item.key }"
      >
        <AppIcon :name="item.icon" />
        <span>{{ item.label }}</span>
      </router-link>
      <router-link
        v-if="secondaryNav"
        :to="secondaryNav.to"
        :class="{ active: currentKey === secondaryNav.key }"
      >
        <AppIcon :name="secondaryNav.icon" />
        <span>{{ secondaryNav.label }}</span>
      </router-link>
    </nav>
  </template>

  <router-view v-else />
</template>
