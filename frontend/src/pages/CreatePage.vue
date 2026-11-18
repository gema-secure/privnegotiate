<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showConfirmDialog, showToast } from "vant";
import AppIcon from "../components/AppIcon.vue";
import PublicHeader from "../components/PublicHeader.vue";
import QrCode from "../components/QrCode.vue";
import { api, errorMessage } from "../api";
import { parseYuan, yuan } from "../format";
import {
  CSV_TEMPLATE,
  MAX_CANDIDATES,
  MIN_CANDIDATES,
  parseMenuJson,
  parseMenuText,
  trimByPrice,
  trimEvenlyByCategory,
  type ParsedMenuRow,
} from "../menuImport";
import { listMenus, removeMenu, saveMenu, type MenuTemplate } from "../menuLibrary";
import { presetByKey, SCENARIO_PRESETS, type ScenarioPreset } from "../scenario";
import { getSession, saveSession, session } from "../session";

interface DraftRow {
  key: number;
  name: string;
  cost: string;
  category: string;
}

const router = useRouter();
const route = useRoute();
const step = ref(1);
const error = ref("");
const submitting = ref(false);
const created = ref<{ id: number; link: string } | null>(null);
const replayedFrom = ref<number | null>(null);

const form = reactive({
  title: "",
  initiatorName: "",
  budget: "",
  count: 6,
  rounds: 4,
});

const rows = ref<DraftRow[]>([]);
const fileInput = ref<HTMLInputElement | null>(null);
const ocrInput = ref<HTMLInputElement | null>(null);
const importText = ref("");
const showPaste = ref(false);
const importError = ref("");
const importNotice = ref("");
const importing = ref(false);
const ocrBusy = ref(false);
const ocrModel = ref("");
const ocrCredential = ref("");

const templates = ref<MenuTemplate[]>([]);
const templateName = ref("");
const showTemplates = ref(false);
const trimTarget = ref(MAX_CANDIDATES);

const presets = SCENARIO_PRESETS;
const activeScenarioKey = ref(
  presetByKey(String(route.query.scenario ?? ""))?.key ?? "",
);
const titlePlaceholder = computed(() => {
  const preset = presetByKey(activeScenarioKey.value);
  return preset ? preset.titlePlaceholder : "例如：周五聚餐 / 周末出游";
});

async function fillScenario(preset: ScenarioPreset) {
  if (filledRows.value.length) {
    try {
      await showConfirmDialog({
        title: "替换当前候选",
        message: `将用「${preset.label}」示例候选（${preset.rows.length} 项）替换当前已填写的 ${filledRows.value.length} 项。`,
        confirmButtonText: "替换",
      });
    } catch {
      return;
    }
  }
  nextKey = 0;
  rows.value = preset.rows.map((row) => {
    nextKey += 1;
    return { key: nextKey, name: row.name, cost: row.price, category: row.category };
  });
  if (!form.budget) form.budget = String(preset.budgetYuan);
  form.count = preset.count;
  activeScenarioKey.value = preset.key;
  importError.value = "";
  importNotice.value = `已填入「${preset.label}」示例候选 ${preset.rows.length} 项，可继续增删改；预算与数量已按该场景建议预填。`;
  showToast(`已填入${preset.label}示例`);
}

let nextKey = 1;

const budgetCents = computed(() => parseYuan(form.budget));
const categories = computed(() => [
  ...new Set(rows.value.map((row) => row.category.trim()).filter(Boolean)),
]);
const filledRows = computed(() =>
  rows.value.filter((row) => row.name.trim() || row.cost.trim() || row.category.trim()),
);

function addRow() {
  if (rows.value.length >= 30) return;
  nextKey += 1;
  rows.value.push({ key: nextKey, name: "", cost: "", category: "" });
}

function removeRow(key: number) {
  rows.value = rows.value.filter((row) => row.key !== key);
}

async function applyImported(parsed: ParsedMenuRow[]) {
  if (parsed.length > 200) {
    importError.value = `共读到 ${parsed.length} 项，单次最多处理 200 项。请先在菜单中删减后再导入。`;
    return false;
  }
  if (filledRows.value.length) {
    try {
      await showConfirmDialog({
        title: "替换当前候选",
        message: `导入的 ${parsed.length} 项将替换当前已填写的 ${filledRows.value.length} 项。`,
        confirmButtonText: "导入并替换",
      });
    } catch {
      return false;
    }
  }
  nextKey = 0;
  rows.value = parsed.map((row) => {
    nextKey += 1;
    return { key: nextKey, name: row.name, cost: row.price, category: row.category };
  });
  importError.value = "";
  importNotice.value =
    parsed.length > MAX_CANDIDATES
      ? `已导入 ${parsed.length} 项，超过 ${MAX_CANDIDATES} 项上限。请用下方裁剪助手保留 ${MIN_CANDIDATES}–${MAX_CANDIDATES} 项。`
      : `已导入 ${parsed.length} 项。可直接修改，或删减到本次协商需要的范围。`;
  showToast(`已导入 ${parsed.length} 项候选`);
  return true;
}

async function finishParse(result: { rows: ParsedMenuRow[]; errors: string[] }) {
  if (result.errors.length) {
    importError.value = result.errors.slice(0, 5).join(" ");
    if (result.errors.length > 5) {
      importError.value += ` 另有 ${result.errors.length - 5} 处问题。`;
    }
    if (!result.rows.length) return;
    try {
      await showConfirmDialog({ title: "导入有效候选",
        message: `可导入 ${result.rows.length} 项，跳过 ${result.errors.length} 处问题。${importError.value}`,
        confirmButtonText: "仅导入有效项" });
    } catch { return; }
  }
  if (!result.rows.length) {
    importError.value = "没有解析到可用的候选行。";
    return;
  }
  const applied = await applyImported(result.rows);
  if (applied && result.errors.length) importNotice.value += ` 已跳过 ${result.errors.length} 处问题。`;
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  importError.value = "";
  importNotice.value = "";
  if (file.size > 1_000_000) {
    importError.value = "文件超过 1MB，请精简后再导入。";
    input.value = "";
    return;
  }
  importing.value = true;
  try {
    const text = await file.text();
    const isJson = file.name.toLowerCase().endsWith(".json") || /^\s*[[{]/.test(text);
    finishParse(isJson ? parseMenuJson(text) : parseMenuText(text));
  } catch {
    importError.value = "文件读取失败，请确认文件未损坏。";
  } finally {
    importing.value = false;
    input.value = "";
  }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

async function onOcrChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  importError.value = "";
  importNotice.value = "";
  ocrModel.value = "";
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
    importError.value = "仅支持 JPG、PNG、WebP 格式的菜单截图。";
    input.value = "";
    return;
  }
  if (file.size > 6 * 1024 * 1024) {
    importError.value = "截图超过 6MB，请压缩或只截取菜单区域后重试。";
    input.value = "";
    return;
  }
  ocrBusy.value = true;
  try {
    const credential = ocrCredential.value.trim() || session.current?.token;
    if (!credential) {
      importError.value = "截图识别需要已有成员身份或组织者提供的识别凭证；也可使用文本导入。";
      return;
    }
    const dataUrl = await readAsDataUrl(file);
    const base64 = dataUrl.split(",", 2)[1] ?? "";
    const result = await api.parseMenuImage(
      base64,
      file.type as "image/jpeg" | "image/png" | "image/webp",
      credential,
    );
    ocrModel.value = result.model;
    await applyImported(
      result.items.map((item) => ({
        name: item.name,
        price: item.price,
        category: item.category,
      })),
    );
    if (result.warnings.length) {
      importNotice.value += ` ${result.warnings.slice(0, 3).join(" ")}`;
    }
  } catch (err) {
    importError.value = errorMessage(err);
  } finally {
    ocrBusy.value = false;
    input.value = "";
  }
}

function refreshTemplates() {
  templates.value = listMenus();
}

function saveCurrentTemplate() {
  if (!rows.value.length) {
    importError.value = "当前没有可保存的候选。";
    return;
  }
  saveMenu(
    templateName.value || form.title || "未命名菜单",
    rows.value.map((row) => ({ name: row.name, price: row.cost, category: row.category })),
  );
  templateName.value = "";
  refreshTemplates();
  showTemplates.value = true;
  showToast("菜单模板已保存");
}

function loadTemplate(template: MenuTemplate) {
  nextKey = 0;
  rows.value = template.rows.map((row) => {
    nextKey += 1;
    return { key: nextKey, name: row.name, cost: row.price, category: row.category };
  });
  importError.value = "";
  importNotice.value = `已载入模板「${template.name}」，共 ${template.rows.length} 项。`;
  showToast("已载入菜单模板");
}

function deleteTemplate(template: MenuTemplate) {
  removeMenu(template.id);
  refreshTemplates();
}

const categoryCounts = computed(() => {
  const counts = new Map<string, number>();
  for (const row of rows.value) {
    const key = row.category.trim() || "未分类";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((left, right) => right[1] - left[1]);
});

const overLimit = computed(() => rows.value.length > MAX_CANDIDATES);

function applyTrim(strategy: "even" | "price") {
  if (!overLimit.value) {
    showToast("当前候选已在 30 项以内");
    return;
  }
  const target = Math.min(
    MAX_CANDIDATES,
    Math.max(MIN_CANDIDATES, Math.round(trimTarget.value) || MAX_CANDIDATES),
  );
  const source = rows.value.map((row) => ({
    name: row.name,
    price: row.cost,
    category: row.category,
  }));
  const indexOf = new Map(source.map((item, index) => [item, index]));
  const kept = strategy === "even" ? trimEvenlyByCategory(source, target) : trimByPrice(source, target);
  const keepKeys = new Set(
    kept.map((item) => rows.value[indexOf.get(item) ?? 0].key),
  );
  const before = rows.value.length;
  rows.value = rows.value.filter((row) => keepKeys.has(row.key));
  importNotice.value = `裁剪助手已从 ${before} 项中保留 ${rows.value.length} 项，仍可继续手动增删。`;
}

async function prefillFromPrevious() {
  const from = Number(route.query.from);
  if (!Number.isInteger(from) || from <= 0) return;
  try {
    const identity = getSession(from);
    if (!identity) {
      error.value = "请使用原协商的成员身份读取候选与条件。";
      return;
    }
    const pub = await api.publicView(from, identity.token);
    replayedFrom.value = from;
    form.title = pub.title;
    form.budget = yuan(pub.conditions.budget_cents);
    form.count = pub.conditions.exact_count;
    form.rounds = pub.conditions.round_limit;
    nextKey = 0;
    rows.value = pub.candidates.map((candidate) => {
      nextKey += 1;
      return {
        key: nextKey,
        name: candidate.name,
        cost: yuan(candidate.cost_cents),
        category: candidate.category,
      };
    });
    importNotice.value = `已沿用「${pub.title}」的候选与条件，确认无误后继续。`;
  } catch (err) {
    error.value = errorMessage(err);
  }
}

onMounted(() => {
  refreshTemplates();
  const fromScenario = presetByKey(String(route.query.scenario ?? ""));
  if (fromScenario && !rows.value.length) {
    void fillScenario(fromScenario);
  }
  void prefillFromPrevious();
});

function importPasted() {
  importError.value = "";
  importNotice.value = "";
  const text = importText.value.trim();
  if (!text) {
    importError.value = "请先粘贴候选内容。";
    return;
  }
  const isJson = /^[[{]/.test(text);
  finishParse(isJson ? parseMenuJson(text) : parseMenuText(text));
  if (!importError.value) showPaste.value = false;
}

function downloadTemplate() {
  const blob = new Blob([`\uFEFF${CSV_TEMPLATE}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "和议-候选导入模板.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

function validateStep(): string {
  if (step.value === 1) {
    if (!form.title.trim()) return "请填写这次协商的名称。";
    if (!form.initiatorName.trim() || form.initiatorName.trim().length > 32)
      return "请填写 1–32 字的发起者称呼。";
    if (!Number.isFinite(budgetCents.value) || budgetCents.value <= 0 || budgetCents.value > 100_000_00)
      return "请填写有效的总预算（1–100000 元）。";
    if (!Number.isInteger(form.count) || form.count < 1 || form.count > 8)
      return "目标数量需为 1–8 的整数。";
    if (!Number.isInteger(form.rounds) || form.rounds < 1 || form.rounds > 20)
      return "轮数上限需为 1–20 的整数。";
  }
  if (step.value === 2) {
    if (rows.value.length < 13 || rows.value.length > 30)
      return `请导入或填写 13–30 个候选，当前为 ${rows.value.length} 个。`;
    const names = rows.value.map((row) => row.name.trim());
    if (names.some((name) => !name)) return "请为每个候选填写名称。";
    if (new Set(names).size !== names.length) return "候选名称有重复，请使用容易区分的名称。";
    for (const row of rows.value) {
      const cents = parseYuan(row.cost);
      if (!Number.isFinite(cents) || cents <= 0) return `请检查「${row.name}」的价格。`;
      if (Math.abs(Number(row.cost) * 100 - cents) > 0.0001)
        return `「${row.name}」的价格最多保留两位小数。`;
    }
  }
  return "";
}

function next() {
  error.value = "";
  const message = validateStep();
  if (message) {
    error.value = message;
    return;
  }
  if (step.value < 3) {
    step.value += 1;
    window.scrollTo({ top: 0 });
    return;
  }
  void submit();
}

function back() {
  error.value = "";
  if (step.value === 1) {
    router.push("/");
    return;
  }
  step.value -= 1;
  window.scrollTo({ top: 0 });
}

async function submit() {
  if (submitting.value) return;
  const message = validateStep();
  if (message) {
    error.value = message;
    return;
  }
  submitting.value = true;
  try {
    const result = await api.createNegotiation({
      title: form.title.trim(),
      initiator_name: form.initiatorName.trim(),
      candidates: rows.value.map((row, index) => ({
        candidate_id: `c${String(index + 1).padStart(2, "0")}`,
        name: row.name.trim(),
        cost_cents: parseYuan(row.cost),
        category: row.category.trim() || "未分类",
        confirmed_attributes: {},
      })),
      budget_cents: budgetCents.value,
      exact_count: form.count,
      round_limit: form.rounds,
    });
    saveSession(result.negotiation_id, {
      memberId: result.member_id,
      token: result.member_token,
      displayName: form.initiatorName.trim(),
      role: "initiator",
      inviteToken: result.invite_token,
    });
    created.value = {
      id: result.negotiation_id,
      link: new URL(result.invite_path, window.location.origin).toString(),
    };
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    submitting.value = false;
  }
}

async function copyLink() {
  if (!created.value) return;
  try {
    await navigator.clipboard.writeText(created.value.link);
    showToast("邀请链接已复制");
  } catch {
    showToast("复制受限，请手动选择链接复制");
  }
}
</script>

<template>
  <div class="home-wrap">
    <PublicHeader />
    <main class="home-main" style="max-width: 920px">
      <div v-if="created" class="panel">
        <span class="badge green">协商已创建</span>
        <h1 class="mt16" style="font-size: 27px">接下来，先把每个人请进来。</h1>
        <p class="muted mt8" style="font-size: 13px">
          每位成员使用同一条公共邀请链接，但会获得各自独立的成员身份。
          创建后首先收集底线，不能跳过该环节直接宣称全组可行。
        </p>
        <div class="invite-result">
          <h2>公共邀请链接</h2>
          <p class="hint">群发给小组成员即可；成员的回答与记录彼此不可见。</p>
          <div class="copy-box">{{ created.link }}</div>
          <div class="invite-qr">
            <QrCode :value="created.link" :size="152" />
            <p class="hint">面对面聚餐时，直接让对方扫码加入</p>
          </div>
          <div class="top-actions">
            <button type="button" class="btn primary" @click="copyLink">
              <AppIcon name="copy" :size="16" />
              复制邀请链接
            </button>
            <button type="button" class="btn soft" @click="router.push(`/n/${created.id}`)">
              进入公共看板
            </button>
            <button
              type="button"
              class="btn ghost"
              @click="router.push(`/n/${created.id}/me/bottomlines`)"
            >
              先确认我的底线
            </button>
          </div>
        </div>
      </div>

      <template v-else>
        <div class="page-intro">
          <span class="eyebrow">LET'S MAKE ROOM FOR EVERYONE</span>
          <h1>发起一次，舒服的共同决定。</h1>
          <p>先定共同条件，准备候选清单，再邀请每个人独立表达。</p>
          <div v-if="replayedFrom" class="notice mt16">
            <AppIcon name="history" :size="15" />
            <span>已沿用上一次协商的候选与条件，确认后即可邀请新成员。</span>
          </div>
        </div>

        <div class="wizard-steps">
          <div
            v-for="(label, index) in ['共同条件', '导入候选', '检查并邀请']"
            :key="label"
            class="wizard-step"
            :class="{ active: index + 1 <= step }"
          >
            0{{ index + 1 }} · {{ label }}
          </div>
        </div>

        <section class="panel">
          <template v-if="step === 1">
            <h2 class="panel-header">这次想一起决定什么？</h2>
            <div class="form">
              <div class="field">
                <label for="create-title">协商名称</label>
                <input id="create-title" v-model="form.title" maxlength="120" :placeholder="titlePlaceholder" />
              </div>
              <div class="field-grid">
                <div class="field">
                  <label for="create-name">发起者称呼</label>
                  <input id="create-name" v-model="form.initiatorName" maxlength="32" placeholder="大家熟悉的称呼" />
                </div>
                <div class="field">
                  <label for="create-budget">总预算 / 元</label>
                  <input id="create-budget" v-model="form.budget" type="number" min="1" max="100000" step="0.01" placeholder="按本次总花费填写" />
                  <span class="hint">全组总预算，不是人均预算。</span>
                </div>
                <div class="field">
                  <label for="create-count">目标选择数量</label>
                  <input id="create-count" v-model.number="form.count" type="number" min="1" max="8" />
                  <span class="hint">最终组合需要正好选中这么多项。</span>
                </div>
                <div class="field">
                  <label for="create-rounds">轮数上限</label>
                  <input id="create-rounds" v-model.number="form.rounds" type="number" min="1" max="20" />
                  <span class="hint">建议 4 轮。达到上限不等于全员同意。</span>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="step === 2">
            <div class="between">
              <div>
                <h2>准备候选清单。</h2>
                <p class="hint" style="margin-top: 6px">
                  场景速填一键开始，或导入 CSV / TSV / JSON；价格按每项总费用填写，分类可自定义。
                </p>
              </div>
              <span class="badge outline">{{ rows.length }} / 30 个</span>
            </div>

            <div class="scenario-fill">
              <div class="between">
                <span class="small-label">场景速填 · 一个机制，多种场景</span>
                <span class="badge amber">示例数据</span>
              </div>
              <div class="scenario-fill-actions mt8">
                <button
                  v-for="preset in presets"
                  :key="preset.key"
                  type="button"
                  class="btn soft"
                  @click="fillScenario(preset)"
                >
                  {{ preset.emoji }} 填入{{ preset.label }}示例（{{ preset.rows.length }} 项）
                </button>
              </div>
            </div>

            <div class="import-zone">
              <label class="field">截图识别凭证（已有成员身份可留空）
                <input v-model="ocrCredential" type="password" autocomplete="off" placeholder="组织者提供的识别凭证" />
              </label>
              <div class="import-actions">
                <button type="button" class="btn primary" :disabled="importing" @click="fileInput?.click()">
                  <AppIcon name="share" :size="16" />
                  {{ importing ? "读取中…" : "选择 CSV / JSON 文件" }}
                </button>
                <button type="button" class="btn soft" :disabled="ocrBusy" @click="ocrInput?.click()">
                  <AppIcon name="camera" :size="16" />
                  {{ ocrBusy ? "识别中…" : "菜单截图识别" }}
                </button>
                <button type="button" class="btn soft" @click="showPaste = !showPaste">
                  <AppIcon name="copy" :size="16" />
                  {{ showPaste ? "收起粘贴" : "粘贴菜单文本" }}
                </button>
                <button type="button" class="btn ghost small" @click="downloadTemplate">
                  <AppIcon name="arrow" :size="14" style="transform: rotate(90deg)" />
                  下载空模板
                </button>
                <input
                  ref="fileInput"
                  class="file-input"
                  type="file"
                  accept=".csv,.tsv,.txt,.json,text/csv,application/json"
                  @change="onFileChange"
                />
                <input
                  ref="ocrInput"
                  class="file-input"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  @change="onOcrChange"
                />
              </div>

              <div v-if="showPaste" class="paste-zone">
                <textarea
                  v-model="importText"
                  class="textarea"
                  rows="6"
                  placeholder="名称,价格,分类&#10;示例格式；从 Excel 复制后直接粘贴即可"
                />
                <div class="paste-actions">
                  <span class="hint">支持逗号、Tab 或分号分隔，也支持 JSON 数组。</span>
                  <button type="button" class="btn primary small" @click="importPasted">解析并导入</button>
                </div>
              </div>

              <p class="hint mt8">
                聚餐场景可上传外卖/团购/店内菜单截图，由视觉模型识别成候选；
                任意场景都支持 CSV / JSON / 粘贴文本。价格单位为元，也支持 cost_cents（分）。
              </p>
              <p v-if="ocrModel" class="hint">识别模型：{{ ocrModel }}</p>
              <div v-if="importError" class="error-box" role="alert">{{ importError }}</div>
              <div v-if="importNotice" class="notice mt8">
                <AppIcon name="check" :size="15" />
                <span>{{ importNotice }}</span>
              </div>
            </div>

            <div class="template-zone">
              <div class="between">
                <span class="small-label">候选模板 · 下次可直接载入</span>
                <button
                  v-if="templates.length"
                  type="button"
                  class="btn ghost small"
                  @click="showTemplates = !showTemplates"
                >
                  {{ showTemplates ? "收起" : `我的模板（${templates.length}）` }}
                </button>
              </div>
              <div class="template-save">
                <input
                  v-model="templateName"
                  maxlength="40"
                  placeholder="模板名称，例如「聚餐 · 川菜馆」或「出游 · 近郊线」"
                />
                <button
                  type="button"
                  class="btn soft small"
                  :disabled="!rows.length"
                  @click="saveCurrentTemplate"
                >
                  保存当前候选
                </button>
              </div>
              <div v-if="showTemplates && templates.length" class="template-list">
                <div v-for="template in templates" :key="template.id" class="template-item">
                  <div>
                    <strong>{{ template.name }}</strong>
                    <span class="hint"> · {{ template.rows.length }} 项</span>
                  </div>
                  <div class="template-actions">
                    <button type="button" class="btn ghost small" @click="loadTemplate(template)">
                      载入
                    </button>
                    <button
                      type="button"
                      class="btn ghost small"
                      :aria-label="`删除模板${template.name}`"
                      @click="deleteTemplate(template)"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="rows.length" class="editor-list mt24">
              <div class="between">
                <span class="small-label">已导入候选 · 可逐项修改</span>
                <span class="small-label">本次协商要求 13–30 项</span>
              </div>
              <div class="editor-head mt8">
                <span>候选名称</span><span>价格 / 元</span><span>分类</span><span></span>
              </div>
              <div v-for="(row, index) in rows" :key="row.key" class="editor-row">
                <input v-model="row.name" maxlength="120" :aria-label="`第${index + 1}个候选名称`" />
                <input
                  v-model="row.cost"
                  type="number"
                  min="0.01"
                  step="0.01"
                  :aria-label="`第${index + 1}个候选价格`"
                />
                <input
                  v-model="row.category"
                  list="category-options"
                  maxlength="64"
                  placeholder="可留空"
                  :aria-label="`第${index + 1}个候选分类`"
                />
                <button
                  type="button"
                  class="btn ghost"
                  :aria-label="`删除第${index + 1}个候选`"
                  @click="removeRow(row.key)"
                >
                  <AppIcon name="close" :size="15" />
                </button>
              </div>
              <datalist id="category-options">
                <option v-for="category in categories" :key="category" :value="category" />
              </datalist>
            </div>

            <div v-else class="empty-state">
              <div class="empty-icon"><AppIcon name="ticket" :size="22" /></div>
              <h3>还没有候选</h3>
              <p>用场景示例快速开始、导入清单，或手动添加。应用不会预置任何候选项。</p>
            </div>

            <div v-if="overLimit" class="trim-zone">
              <div class="between">
                <strong>裁剪助手</strong>
                <span class="badge amber">{{ rows.length }} 项 · 上限 {{ MAX_CANDIDATES }}</span>
              </div>
              <p class="hint mt8">
                导入的清单常常超过 30 项。选择保留策略，一次收敛到可协商的规模，之后仍可手动增删。
              </p>
              <div class="trim-categories">
                <span v-for="[name, count] in categoryCounts" :key="name" class="trim-chip">
                  {{ name }} {{ count }}
                </span>
              </div>
              <div class="trim-actions">
                <label class="trim-target">
                  保留
                  <input v-model.number="trimTarget" type="number" min="13" max="30" />
                  项
                </label>
                <button type="button" class="btn soft small" @click="applyTrim('even')">
                  按分类等比保留
                </button>
                <button type="button" class="btn soft small" @click="applyTrim('price')">
                  按价格从低到高保留
                </button>
              </div>
            </div>

            <button type="button" class="btn soft mt8" :disabled="rows.length >= MAX_CANDIDATES" @click="addRow">
              <AppIcon name="plus" :size="16" />
              手动添加一行
            </button>
          </template>

          <template v-else>
            <h2 class="panel-header">再看一眼，就可以邀请大家了。</h2>
            <div class="receipt-list">
              <div class="receipt-item"><span>协商名称</span><strong>{{ form.title }}</strong></div>
              <div class="receipt-item">
                <span>发起者 / 人数上限</span><strong>{{ form.initiatorName }} / 最多 6 人</strong>
              </div>
              <div class="receipt-item">
                <span>预算 / 目标数量</span>
                <strong>总计 ¥{{ form.budget }} / {{ form.count }} 项</strong>
              </div>
              <div class="receipt-item">
                <span>候选数量 / 轮数上限</span><strong>{{ rows.length }} 个 / {{ form.rounds }} 轮</strong>
              </div>
              <div class="receipt-item">
                <span>候选分类</span>
                <strong>{{ categories.length ? categories.join("、") : "未分类" }}</strong>
              </div>
            </div>
            <div class="notice mt16">
              <AppIcon name="shield" :size="15" />
              <span>
                创建后先邀请成员独立确认底线。只有每个人明确接受同一版本，结果才会标记为全员同意。
              </span>
            </div>
          </template>

          <div v-if="error" class="error-box" role="alert">{{ error }}</div>

          <div class="form-actions">
            <p>本页不会公开成员的个人回答。</p>
            <div class="actions">
              <button type="button" class="btn soft" @click="back">
                {{ step === 1 ? "取消" : "上一步" }}
              </button>
              <button type="button" class="btn primary" :disabled="submitting" @click="next">
                {{ submitting ? "正在创建…" : step === 3 ? "创建并邀请" : "下一步" }}
                <AppIcon name="arrow" :size="16" />
              </button>
            </div>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>
