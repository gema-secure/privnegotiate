/**
 * 场景层：一个协商机制 × 多个场景（聚餐 / 出游 / 通用）。
 *
 * detectScenario 只依据已公开的分类与候选名称做关键词计分，不引入任何
 * 新的持久化字段；SCENARIO_VOICE 提供语气自适应文案，SCENARIO_PRESETS
 * 提供创建页的场景速填示例（13 项，满足 13–30 候选约束）。
 */

export type Scenario = "dining" | "outing" | "generic";

export interface ScenarioVoice {
  label: string;
  homeTitle: string;
  homeDesc: string;
  homeCta: string;
  finalTitle: string;
  proposalTitle: string;
  shareBanner: string;
}

export interface ScenarioPresetRow {
  name: string;
  price: string;
  category: string;
}

export interface ScenarioPreset {
  key: Exclude<Scenario, "generic">;
  label: string;
  emoji: string;
  titlePlaceholder: string;
  budgetYuan: number;
  count: number;
  rows: ScenarioPresetRow[];
}

const DINING_RULE =
  /荤|素菜|菜品|菜|餐|食|饭|面|烧烤|火锅|汤|主食|小吃|饮|茶|咖|酒|甜品/g;
const OUTING_RULE =
  /交通|包车|高铁|巴士|船|打车|住宿|酒店|民宿|景点|门票|山|公园|户外|露营|温泉|古镇|徒步|体验|观光|行程/g;

function score(text: string, rule: RegExp): number {
  const matches = text.match(rule);
  return matches ? matches.length : 0;
}

export function detectScenario(
  categories: readonly string[],
  names: readonly string[] = [],
): Scenario {
  const joined = [...categories, ...names].join(" ");
  const dining = score(joined, DINING_RULE);
  const outing = score(joined, OUTING_RULE);
  if (dining >= 2 && dining > outing) return "dining";
  if (outing >= 2 && outing > dining) return "outing";
  return "generic";
}

export const SCENARIO_VOICE: Record<Scenario, ScenarioVoice> = {
  dining: {
    label: "聚餐",
    homeTitle: "今晚，谁都不用说「随便」。",
    homeDesc: "十几道菜里，选出大家都愿意接受的一桌。各自勾选，私下表达。",
    homeCta: "发起一次聚餐协商",
    finalTitle: "今晚，就这样定。",
    proposalTitle: "给这顿饭的一份提案",
    shareBanner: "这次一起吃",
  },
  outing: {
    label: "出游",
    homeTitle: "周末去哪，现在就能定。",
    homeDesc: "交通、景点、体验里，凑出大家都接受的一天行程。",
    homeCta: "发起一次出游协商",
    finalTitle: "这次，就这样出发。",
    proposalTitle: "给这次出行的一份提案",
    shareBanner: "这次一起出发",
  },
  generic: {
    label: "共同决定",
    homeTitle: "把每个人的「随便」，变成一份都愿意接受的方案。",
    homeDesc: "小组成员独立表达、逐轮收窄，在不知道彼此偏好的前提下得到可执行组合。",
    homeCta: "发起一次协商",
    finalTitle: "这次，就这样定。",
    proposalTitle: "给这次决定的一份提案",
    shareBanner: "这次，一起定",
  },
};

export const SCENARIO_PRESETS: ScenarioPreset[] = [
  {
    key: "dining",
    label: "聚餐",
    emoji: "🍽",
    titlePlaceholder: "例如：周五部门聚餐",
    budgetYuan: 300,
    count: 6,
    rows: [
      { name: "清蒸鲈鱼", price: "58", category: "荤菜" },
      { name: "番茄牛腩", price: "66", category: "荤菜" },
      { name: "宫保鸡丁", price: "46", category: "荤菜" },
      { name: "糖醋排骨", price: "52", category: "荤菜" },
      { name: "酸菜鱼", price: "68", category: "荤菜" },
      { name: "蒜蓉西兰花", price: "32", category: "素菜" },
      { name: "菌菇豆腐", price: "36", category: "素菜" },
      { name: "凉拌黄瓜", price: "18", category: "素菜" },
      { name: "地三鲜", price: "28", category: "素菜" },
      { name: "莲藕排骨汤", price: "48", category: "汤品" },
      { name: "番茄蛋汤", price: "16", category: "汤品" },
      { name: "扬州炒饭", price: "24", category: "主食" },
      { name: "手工水饺", price: "30", category: "主食" },
    ],
  },
  {
    key: "outing",
    label: "出游",
    emoji: "🚐",
    titlePlaceholder: "例如：周末近郊一日游",
    budgetYuan: 800,
    count: 5,
    rows: [
      { name: "高铁往返票", price: "228", category: "交通" },
      { name: "包车一日", price: "680", category: "交通" },
      { name: "古镇大门票", price: "80", category: "景点" },
      { name: "温泉票", price: "128", category: "景点" },
      { name: "观光船票", price: "40", category: "景点" },
      { name: "湖区骑行", price: "45", category: "体验" },
      { name: "密室逃脱", price: "88", category: "体验" },
      { name: "剧本杀", price: "68", category: "体验" },
      { name: "采茶体验", price: "60", category: "体验" },
      { name: "农家乐午餐", price: "58", category: "餐饮" },
      { name: "咖啡馆下午茶", price: "38", category: "餐饮" },
      { name: "烧烤晚餐", price: "75", category: "餐饮" },
      { name: "特产伴手礼", price: "50", category: "购物" },
    ],
  },
];

export function presetByKey(key: string): ScenarioPreset | undefined {
  return SCENARIO_PRESETS.find((item) => item.key === key);
}

/** 分类感知的候选图标名；映射不到时用星芒兜底。 */
const CATEGORY_ICON_RULES: Array<[RegExp, string]> = [
  [/荤|素|菜|餐|食|饭|面|烧烤|火锅|汤|主食|小吃/, "utensils"],
  [/饮|茶|咖|酒|奶|奶茶|果汁/, "coffee"],
  [/观影|电影|影/, "film"],
  [/歌|音乐|KTV|ktv/, "music"],
  [/桌游|剧本|密室|游戏|VR|电玩|轰趴/, "gamepad"],
  [/山|景|公园|户外|自然|湖|海|古镇|采摘|露营|温泉|徒步|风光/, "trees"],
  [/交通|车|高铁|地铁|巴士|船|机票|打车|观光/, "bus"],
  [/住|酒店|民宿|房|客栈/, "bed"],
  [/运动|球|健身|卡丁|滑雪|骑行/, "ball"],
  [/展|馆|博物馆|艺术|文化|拍照|摄影/, "camera"],
  [/票|门票|演出|展览/, "ticket"],
];

export function iconForCategory(category: string, name = ""): string {
  const text = `${category} ${name}`;
  for (const [rule, icon] of CATEGORY_ICON_RULES) {
    if (rule.test(text)) return icon;
  }
  return "sparkles";
}

/** 候选令牌调色：按分类（未分类时按名称）稳定取色，供各页面共享。 */
const TOKEN_PALETTES = [
  { bg: "#f1e5d4", food: "#d8c49a", accent: "#7a9958" },
  { bg: "#e4edf1", food: "#9dbfcc", accent: "#5f8a9c" },
  { bg: "#f4e8e2", food: "#d9a98f", accent: "#b5755a" },
  { bg: "#e7ebda", food: "#7d9e57", accent: "#548559" },
  { bg: "#f0e2d5", food: "#b97b4d", accent: "#a1613b" },
  { bg: "#e9e6ef", food: "#a498bd", accent: "#77699b" },
  { bg: "#f1e9cf", food: "#cbb46d", accent: "#96793a" },
];

const GENERIC_CATEGORY_KEYS = new Set(["", "未分类", "unknown", "其他", "其它"]);

function hashOf(value: string): number {
  let hash = 0;
  for (const char of value) hash = (hash * 31 + char.codePointAt(0)!) % 1000003;
  return hash;
}

export function paletteFor(category: string, name = "") {
  const key = GENERIC_CATEGORY_KEYS.has(category.trim().toLowerCase()) ? name : category;
  return TOKEN_PALETTES[hashOf(key) % TOKEN_PALETTES.length];
}
