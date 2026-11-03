<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppIcon from "../components/AppIcon.vue";
import PublicHeader from "../components/PublicHeader.vue";
import { api, errorMessage } from "../api";
import { getSession, saveSession } from "../session";

const route = useRoute();
const router = useRouter();
const negotiationId = Number(route.params.negotiationId);
const inviteToken = String(route.params.inviteToken ?? "");

const name = ref("");
const error = ref("");
const submitting = ref(false);

const existing = getSession(negotiationId);

async function join() {
  if (existing || submitting.value) return;
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
      <div class="page-intro">
        <span class="eyebrow">A SEAT AT THE TABLE</span>
        <h1>你的一票，先为自己而选。</h1>
        <p>
          这是一个成员邀请入口。加入后才能查看共同条件与公共进度；本页不提供匿名预览。
        </p>
      </div>

      <div v-if="existing" class="notice amber mt16">
        <AppIcon name="info" :size="15" />
        <span>
          本设备已有「{{ existing.displayName }}」的参与身份。
          <router-link :to="`/n/${negotiationId}`" class="link-btn">进入我的协商</router-link>
        </span>
      </div>

      <section v-if="!existing" class="panel">
        <h2>建立我的参与身份</h2>
        <p class="hint mt8">加入后你将以成员身份参与作答，并能查看公共进度。</p>
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
    </main>
  </div>
</template>
