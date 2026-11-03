export interface ParsedMenuRow {
  name: string;
  price: string;
  category: string;
}

export interface MenuParseResult {
  rows: ParsedMenuRow[];
  errors: string[];
}

const NAME_KEYS = ["名称", "菜品", "菜品名称", "菜名", "商品", "name", "item", "dish", "title"];
const PRICE_KEYS = ["价格", "单价", "金额", "售价", "price", "cost", "amount", "unit_price"];
const CENT_KEYS = ["cost_cents", "price_cents", "分", "价格分"];
const CATEGORY_KEYS = ["分类", "类别", "类型", "分组", "category", "type", "group"];

function normalize(value: string): string {
  return value.replace(/^\uFEFF/, "").trim().toLowerCase();
}

function detectDelimiter(firstLine: string): string {
  if (firstLine.includes("\t")) return "\t";
  if (firstLine.includes(";") && !firstLine.includes(",")) return ";";
  return ",";
}

function parseDelimited(text: string): string[][] {
  const source = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const firstLine = source.split("\n").find((line) => line.trim()) ?? "";
  const delimiter = detectDelimiter(firstLine);
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === '"') {
        if (source[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
      continue;
    }
    if (char === delimiter) {
      row.push(cell);
      cell = "";
      continue;
    }
    if (char === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }
  row.push(cell);
  rows.push(row);
  return rows.filter((item) => item.some((value) => value.trim()));
}

function findColumn(headers: string[], keys: string[]): number {
  return headers.findIndex((header) => keys.includes(header));
}

function parsePrice(value: string): string | null {
  const cleaned = value
    .replace(/[¥￥,\s]/g, "")
    .replace(/元$/, "")
    .replace(/[^\d.]/g, "");
  if (!cleaned) return null;
  const amount = Number.parseFloat(cleaned);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const rounded = Math.round(amount * 100) / 100;
  return String(rounded);
}

export function parseMenuText(text: string): MenuParseResult {
  const table = parseDelimited(text);
  if (!table.length) return { rows: [], errors: ["没有读到任何数据行。"] };

  const headers = table[0].map(normalize);
  const hasHeader =
    findColumn(headers, NAME_KEYS) >= 0 ||
    findColumn(headers, PRICE_KEYS) >= 0 ||
    findColumn(headers, CENT_KEYS) >= 0;
  const nameIndex = hasHeader ? findColumn(headers, NAME_KEYS) : 0;
  const centsIndex = hasHeader ? findColumn(headers, CENT_KEYS) : -1;
  const priceIndex = hasHeader ? findColumn(headers, PRICE_KEYS) : 1;
  const categoryIndex = hasHeader ? findColumn(headers, CATEGORY_KEYS) : 2;

  if (hasHeader && nameIndex < 0) {
    return { rows: [], errors: ["表头缺少名称列（名称 / 菜品 / name）。"] };
  }
  if (hasHeader && centsIndex < 0 && priceIndex < 0) {
    return { rows: [], errors: ["表头缺少价格列（价格 / 单价 / price）。"] };
  }

  const rows: ParsedMenuRow[] = [];
  const errors: string[] = [];
  const dataRows = hasHeader ? table.slice(1) : table;

  dataRows.forEach((data, offset) => {
    const lineNo = hasHeader ? offset + 2 : offset + 1;
    const name = (data[nameIndex] ?? "").trim();
    if (!name) {
      errors.push(`第 ${lineNo} 行缺少名称。`);
      return;
    }
    let price: string | null = null;
    if (centsIndex >= 0) {
      const cents = Number.parseFloat((data[centsIndex] ?? "").replace(/[^\d.-]/g, ""));
      if (Number.isFinite(cents) && cents > 0) price = String(Math.round(cents) / 100);
    }
    if (price === null) {
      price = parsePrice(data[priceIndex] ?? "");
    }
    if (price === null) {
      errors.push(`第 ${lineNo} 行「${name}」价格无效或不为正数。`);
      return;
    }
    const category = categoryIndex >= 0 ? (data[categoryIndex] ?? "").trim() : "";
    rows.push({ name, price, category: category || "未分类" });
  });

  return { rows, errors };
}

export function parseMenuJson(text: string): MenuParseResult {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { rows: [], errors: ["JSON 格式不正确，请检查后重试。"] };
  }
  if (!Array.isArray(data)) {
    return { rows: [], errors: ["JSON 顶层需要是数组，例如 [{ \"name\": \"…\", \"price\": 38 }]。"] };
  }

  const rows: ParsedMenuRow[] = [];
  const errors: string[] = [];
  data.forEach((item, index) => {
    if (!item || typeof item !== "object") {
      errors.push(`第 ${index + 1} 项不是对象。`);
      return;
    }
    const record = item as Record<string, unknown>;
    const name = String(record.name ?? record.title ?? record["名称"] ?? record["菜品"] ?? "").trim();
    if (!name) {
      errors.push(`第 ${index + 1} 项缺少 name。`);
      return;
    }
    const centsValue = record.cost_cents ?? record.price_cents;
    let price: string | null = null;
    if (centsValue !== undefined && centsValue !== null) {
      const cents = Number(centsValue);
      if (Number.isFinite(cents) && cents > 0) price = String(Math.round(cents) / 100);
    }
    if (price === null) {
      price = parsePrice(String(record.price ?? record.cost ?? record["价格"] ?? record["单价"] ?? ""));
    }
    if (price === null) {
      errors.push(`第 ${index + 1} 项「${name}」价格无效或不为正数。`);
      return;
    }
    const category = String(record.category ?? record.type ?? record["分类"] ?? record["类别"] ?? "").trim();
    rows.push({ name, price, category: category || "未分类" });
  });

  return { rows, errors };
}

export const CSV_TEMPLATE = "名称,价格,分类\n";

export const MIN_CANDIDATES = 13;
export const MAX_CANDIDATES = 30;

function groupByCategory(rows: ParsedMenuRow[]): Map<string, ParsedMenuRow[]> {
  const groups = new Map<string, ParsedMenuRow[]>();
  for (const row of rows) {
    const key = row.category || "未分类";
    const list = groups.get(key) ?? [];
    list.push(row);
    groups.set(key, list);
  }
  return groups;
}

function pickEvenly(list: ParsedMenuRow[], count: number): ParsedMenuRow[] {
  if (count >= list.length) return list;
  if (count <= 0) return [];
  const picked: ParsedMenuRow[] = [];
  for (let index = 0; index < count; index += 1) {
    picked.push(list[Math.floor((index * list.length) / count)]);
  }
  return picked;
}

/** 按分类等比保留，尽量保留各分类与价格档位。 */
export function trimEvenlyByCategory(
  rows: ParsedMenuRow[],
  target: number,
): ParsedMenuRow[] {
  if (rows.length <= target) return rows;
  const groups = groupByCategory(rows);
  const total = rows.length;
  const quota = new Map<string, number>();
  const remainders: Array<{ category: string; remainder: number; size: number }> = [];
  let used = 0;

  for (const [category, list] of groups) {
    const exact = (list.length * target) / total;
    const base = Math.floor(exact);
    quota.set(category, base);
    used += base;
    remainders.push({ category, remainder: exact - base, size: list.length });
  }

  remainders.sort((left, right) => right.remainder - left.remainder || right.size - left.size);
  let remaining = target - used;
  for (const item of remainders) {
    if (remaining <= 0) break;
    const current = quota.get(item.category) ?? 0;
    if (current < item.size) {
      quota.set(item.category, current + 1);
      remaining -= 1;
    }
  }

  const selected = new Set<ParsedMenuRow>();
  for (const [category, list] of groups) {
    for (const row of pickEvenly(list, quota.get(category) ?? 0)) selected.add(row);
  }
  return rows.filter((row) => selected.has(row));
}

/** 按价格从低到高保留，适合预算有限时先砍掉高价项。 */
export function trimByPrice(rows: ParsedMenuRow[], target: number): ParsedMenuRow[] {
  if (rows.length <= target) return rows;
  const cheapest = new Set(
    [...rows]
      .sort((left, right) => Number(left.price) - Number(right.price))
      .slice(0, target),
  );
  return rows.filter((row) => cheapest.has(row));
}
