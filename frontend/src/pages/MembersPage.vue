<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { showConfirmDialog } from "vant";
import { api, ApiError, errorMessage } from "../api";
import { loadSession, session } from "../session";
import type { MembershipState, RetainedConstraints } from "../types";

const id = Number(useRoute().params.id);
loadSession(id);
const token = computed(() => session.current?.token ?? "");
const state = ref<MembershipState | null>(null);
const retained = ref<RetainedConstraints | null>(null);
const busy = ref(false);
const error = ref("");
const reason = ref("");
const closed = ref(false);
const initiator = computed(() => session.current?.role === "initiator");
const name = (mid: number) => state.value?.members.find(m => m.member_id === mid)?.display_name ?? "已退出成员";
const statusLabels: Record<string, string> = {
  pending: "等待逐人授权", effective: "已生效", rejected: "已拒绝", cancelled: "已撤销", expired: "授权已失效",
};

async function refresh() {
  error.value = "";
  if (!token.value) { error.value = "请使用自己的成员身份进入。"; return; }
  try {
    try {
      state.value = await api.membership(id, token.value);
      retained.value = null;
      const pub = await api.publicView(id, token.value);
      closed.value = ["FINALIZED", "CLOSED_WITH_CONCERN", "CLOSED_UNCONFIRMED"].includes(pub.status);
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) throw err;
      retained.value = await api.retainedConstraints(id, token.value);
      state.value = null;
    }
  } catch (err) { error.value = errorMessage(err); }
}

async function run(action: () => Promise<unknown>, message?: string) {
  if (busy.value) return;
  if (message) {
    try { await showConfirmDialog({ title: "请明确确认", message }); }
    catch { return; }
  }
  busy.value = true;
  error.value = "";
  try { await action(); await refresh(); }
  catch (err) { error.value = errorMessage(err); }
  finally { busy.value = false; }
}

function leave() {
  if (!state.value) return;
  const ver = state.value.membership_version;
  run(() => api.leave(id, token.value, ver),
    "我明确退出当前共同结果。个人效用和本人替代要求退出，已确认整组禁忌保留。旧确认失效；少于三人或发起者离开将按未确认结束。");
}

function remove(target: number) {
  if (!state.value) return;
  if (!reason.value.trim()) { error.value = "请填写移除原因。"; return; }
  const ver = state.value.membership_version;
  run(() => api.requestRemoval(id, target, token.value, ver, reason.value),
    "提出请求不会立即移除。必须由所有其余参与者（至少两人，含发起者）分别明确授权，目标本人不参与授权。移除仅取消其后续作答与个人效用，已确认的整组禁忌仍将生效。");
}

function authorize(rid: number, approve: boolean) {
  if (!state.value) return;
  const ver = state.value.membership_version;
  run(() => api.authorizeRemoval(id, rid, token.value, ver, approve), approve
    ? "我同意本次移除：仅取消其后续作答与个人效用，已确认整组禁忌仍将生效。成员集合变化会使本次未完成授权失效。"
    : "我不同意本次移除。该请求将结束，目标成员继续参与。");
}

function withdraw(cid: string) {
  if (!retained.value) return;
  const ver = retained.value.preference_version;
  run(() => api.withdrawRetained(id, cid, token.value, ver),
    "我明确撤回本条由本人提出的整组禁忌。其他成员对同项的禁忌继续有效；未结束的协商将重新计算并重新确认。");
}

onMounted(refresh);
</script>

<template>
  <div class="action-page">
    <section class="panel">
      <h1>成员与退出</h1>
      <p class="hint mt16">首次开轮后不能新增或替换成员。失联仍保留其底线、效用和确认权，不视为同意。</p>
      <p v-if="error" class="error-box mt16" role="alert">{{ error }}</p>
      <button class="btn soft small mt16" :disabled="busy" @click="refresh">刷新</button>
      <template v-if="state">
        <p class="hint mt16">当前参与 {{ state.members.length }} 人 · {{ closed ? '协商已结束' : '协商进行中' }}</p>
        <div v-if="!closed && state.response_status === 'UNRESPONSIVE'" class="notice mt16">
          <span>你被标记为未响应，参与资格和底线继续保留。</span>
          <button class="btn soft small" :disabled="busy" @click="run(() => api.returnToActive(id, token))">我已返回</button>
        </div>
        <div v-if="initiator && !closed" class="field mt16">
          <label for="removal-reason">移除原因（仅受控审计保存）</label>
          <input id="removal-reason" v-model="reason" maxlength="500" />
        </div>
        <article v-for="member in state.members" :key="member.member_id" class="concern-card">
          <div class="between">
            <span>{{ member.display_name }} {{ member.role === 'initiator' ? '（发起者）' : '' }}</span>
            <div v-if="initiator && !closed && member.member_id !== session.current?.memberId" class="actions">
              <button class="btn soft small" :disabled="busy" @click="run(() => api.markUnresponsive(id, member.member_id, token, state!.membership_version))">申请标记未响应</button>
              <button v-if="member.role !== 'initiator'" class="btn soft small" :disabled="busy" @click="remove(member.member_id)">提出移除</button>
            </div>
          </div>
        </article>
        <p class="hint mt16">未响应标记须当前题包已过截止时间且未作答；不展示个人响应标签。</p>
        <h2 class="mt24">移除请求</h2>
        <article v-for="request in state.removals" :key="request.request_id" class="concern-card">
          <p>请求 #{{ request.request_id }} · {{ name(request.target_member_id) }} · {{ statusLabels[request.status] ?? request.status }}</p>
          <p class="hint mt8">{{ request.notice }}</p>
          <p v-if="request.my_vote !== null" class="hint mt8">本人已{{ request.my_vote ? '授权' : '拒绝' }}本次移除</p>
          <div v-if="request.can_vote && request.my_vote === null && !closed" class="actions mt16">
            <button class="btn danger small" :disabled="busy" @click="authorize(request.request_id, true)">明确授权</button>
            <button class="btn soft small" :disabled="busy" @click="authorize(request.request_id, false)">拒绝移除</button>
          </div>
          <button v-if="initiator && request.status === 'pending' && !closed" class="btn soft small mt16" :disabled="busy" @click="run(() => api.cancelRemoval(id, request.request_id, token, state!.membership_version))">撤销请求</button>
        </article>
        <button v-if="!closed" class="btn danger mt24" :disabled="busy" @click="leave">本人主动离开</button>
      </template>
      <template v-else-if="retained">
        <h2 class="mt24">你已{{ retained.membership_status === 'LEFT' ? '离开' : '被移除' }}</h2>
        <p class="hint mt16">原参与权限已失效。本人凭证仅可查看或逐条撤回保留的整组禁忌；协商结束后不可改写历史结果。</p>
        <article v-for="constraint in retained.constraints" :key="constraint.constraint_id" class="concern-card">
          <p>本人整组禁忌：{{ constraint.candidate_ids.join('、') }}</p>
          <button class="btn soft small mt16" :disabled="busy" @click="withdraw(constraint.constraint_id)">明确撤回本条</button>
        </article>
        <p v-if="!retained.constraints.length" class="hint mt16">没有本人仍生效的整组禁忌。</p>
      </template>
    </section>
  </div>
</template>
