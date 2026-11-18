<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import AppIcon from "../components/AppIcon.vue";
import { api, errorMessage } from "../api";
import { shortDate, yuan } from "../format";
import { useNegotiationState } from "../useNegotiationState";


const route = useRoute();
const id = Number(route.params.id);
const { pub, me, history, loading, error, token, isInitiator, polling, refresh: refreshState } = useNegotiationState(id, { history: true, personal: false });


const entries = computed(() => [...history.value].sort((a, b) => b.round_no - a.round_no));

function candidateName(candidateId: string): string {
  return (
    pub.value?.candidates.find((item) => item.candidate_id === candidateId)?.name ??
    candidateId
  );
}

function names(ids: string[]): string[] {
  return ids.map(candidateName);
}

async function refresh() {
  await refreshState();

}

onMounted(refresh);
</script>

<template>
  <div class="action-page">
    <div v-if="loading" class="panel empty-state"><p>正在读取你的选择链…</p></div>

    <div v-else-if="!token" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="lock" :size="22" /></div>
      <h3>需要你的独立参与入口</h3>
      <p>选择链只属于你本人，请用邀请链接加入后查看。</p>
      <router-link to="/" class="btn soft mt16">返回首页</router-link>
    </div>

    <div v-else-if="error && !history.length" class="panel empty-state">
      <div class="empty-icon"><AppIcon name="warning" :size="22" /></div>
      <h3>暂时无法读取</h3>
      <p>{{ error }}</p>
      <button type="button" class="btn soft mt16" @click="refresh">重试</button>
    </div>

    <template v-else>
      <div class="page-intro">
        <span class="private-label"><AppIcon name="clock" :size="14" /> 仅本人可见</span>
        <h1>你的选择链，只有你知道。</h1>
        <p>这里记录实际展示、你的接受集合、顶项与理由原文，不做任何心理解读。</p>
      </div>

      <section v-if="!entries.length" class="panel empty-state">
        <div class="empty-icon"><AppIcon name="clock" :size="22" /></div>
        <h3>还没有作答记录</h3>
        <p>完成一次私下作答后，你的选择会按轮次记录在这里。</p>
        <router-link :to="`/n/${id}/me/answer`" class="btn primary mt16">去本轮作答</router-link>
      </section>

      <div v-else class="timeline">
        <article v-for="entry in entries" :key="entry.round_no" class="timeline-item">
          <div class="timeline-card">
            <div class="between">
              <h3>第 {{ entry.round_no }} 轮</h3>
              <span v-if="entry.result_version" class="badge green">
                方案 v{{ entry.result_version }}
              </span>
              <span v-else class="badge">尚未形成方案</span>
            </div>

            <p>实际展示 · {{ names(entry.displayed_ids).join("、") || "无" }}</p>

            <template v-if="entry.answer">
              <p>
                {{
                  entry.answer.mode === "skip"
                    ? "本轮暂时跳过，未产生否定偏好。"
                    : entry.answer.acceptable_ids.length
                      ? "明确评价："
                      : "明确表示这些都不接受。"
                }}
              </p>
              <div v-if="entry.answer.acceptable_ids.length" class="choice-pills">
                <span
                  v-for="cid in entry.answer.acceptable_ids"
                  :key="cid"
                  :class="{ top: cid === entry.answer.top_id }"
                >
                  {{ cid === entry.answer.top_id ? "★ " : "" }}{{ candidateName(cid) }}
                </span>
              </div>
              <p v-if="entry.answer.top_id">
                本轮最想要：{{ candidateName(entry.answer.top_id) }}
              </p>
              <p>提交于 {{ shortDate(entry.answer.accepted_at) }}</p>
            </template>
            <template v-else>
              <p>本轮未提交作答，结束前仍在等待。</p>
            </template>

            <div v-if="entry.result_version" class="panel-foot">
              <span class="small-label">
                当轮组合：{{ names(entry.result_candidate_ids).join("、") || "无" }}
                <template v-if="entry.result_total_cost_cents !== null">
                  · ¥{{ yuan(entry.result_total_cost_cents) }}
                </template>
              </span>
            </div>
          </div>
        </article>
      </div>

      <p class="hint mt16">
        发起者可以读取你明确提交的本次顾虑原文，但不会获得这份完整选择链。
      </p>
    </template>
  </div>
</template>
