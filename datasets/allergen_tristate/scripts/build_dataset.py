#!/usr/bin/env python3
"""构建「菜品—过敏原三态」数据集。

输入：data/ 下的过敏原清单、来源清单、配料表、菜品配方、场景定义。
输出：output/ 下的宽表、长表、JSONL、场景候选集、统计与清单。

设计约束：
- 只使用标准库，Python 3.11+；
- 固定随机种子，默认 20261110；
- 输出不含时间戳，重复运行字节一致；
- 菜品状态由配料状态按三值逻辑聚合，不手工填写结果。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
SCHEMA_VERSION = "1.0.0"
DEFAULT_SEED = 20261110
STATES = ("CONTAINS", "NOT_CONTAINS", "UNKNOWN")
STATE_ALIASES = {
    "contains": "CONTAINS",
    "not_contains": "NOT_CONTAINS",
    "unknown": "UNKNOWN",
    "y": "CONTAINS",
    "n": "NOT_CONTAINS",
    "u": "UNKNOWN",
}
CRED_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
CRED_LABEL = {rank: label for label, rank in CRED_RANK.items()}
REQUIRED = {
    "allergens.csv": [
        "code",
        "name_zh",
        "name_en",
        "eu_annex_ii",
        "cn_gb7718_2011",
        "source_ids",
        "note",
    ],
    "sources.csv": ["source_id", "title", "publisher", "year", "url", "license", "usage", "credibility"],
    "ingredients.csv": [
        "ingredient_id",
        "name",
        "category",
        "evidence",
        "state_overrides",
        "source_ids",
        "credibility",
        "manual_review",
        "note",
    ],
    "dishes.csv": [
        "dish_id",
        "name",
        "cuisine",
        "scenarios",
        "price_cents",
        "price_source",
        "ingredient_ids",
        "other_ingredients",
        "source_ids",
        "review_hint",
        "note",
    ],
    "scenarios.csv": ["scenario_id", "name", "description", "target_size", "note"],
}


def fail(message: str) -> None:
    raise SystemExit(f"[构建失败] {message}")


def warn(message: str) -> None:
    print(f"[警告] {message}", file=sys.stderr)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"缺少输入文件：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{path.name} 没有表头")
        headers = [name.strip() for name in reader.fieldnames]
        required = REQUIRED.get(path.name)
        if required:
            missing = [name for name in required if name not in headers]
            if missing:
                fail(f"{path.name} 缺少字段：{missing}")
        rows: list[dict[str, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
            if not any(row.values()):
                continue
            row["_line"] = str(line_no)
            rows.append(row)
        return rows


def split_ids(value: str, field: str, where: str) -> list[str]:
    if not value:
        fail(f"{where} 的 {field} 不能为空")
    parts = [item.strip() for item in value.replace("、", ";").split(";") if item.strip()]
    if not parts:
        fail(f"{where} 的 {field} 不能为空")
    if len(parts) != len(set(parts)):
        fail(f"{where} 的 {field} 存在重复项：{parts}")
    return parts


def parse_int(value: str, field: str, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        fail(f"{where} 的 {field} 不是整数：{value!r}")


def parse_float_or_int(value: str, field: str, where: str) -> int:
    number = parse_int(value, field, where)
    if number < 0:
        fail(f"{where} 的 {field} 不能为负")
    return number


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def csv_bytes(headers: list[str], rows: Iterable[Iterable[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue().encode("utf-8")


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def jsonl_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_allergens(sources: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows = read_csv(DATA_DIR / "allergens.csv")
    seen: set[str] = set()
    for row in rows:
        code = row["code"]
        if not code:
            fail("allergens.csv 存在空的 code")
        if code in seen:
            fail(f"allergens.csv 的 code 重复：{code}")
        seen.add(code)
        row["source_ids"] = resolve_sources(
            row["source_ids"], "source_ids", f"allergens.csv 的 {code}", sources
        )
    return rows


def load_sources() -> dict[str, dict[str, str]]:
    rows = read_csv(DATA_DIR / "sources.csv")
    sources: dict[str, dict[str, str]] = {}
    for row in rows:
        source_id = row["source_id"]
        if not source_id:
            fail("sources.csv 存在空的 source_id")
        if source_id in sources:
            fail(f"sources.csv 的 source_id 重复：{source_id}")
        credibility = row["credibility"].upper()
        if credibility not in CRED_RANK:
            fail(f"{source_id} 的 credibility 必须是 A/B/C/D，实际为 {row['credibility']!r}")
        row["credibility"] = credibility
        sources[source_id] = row
    return sources


def parse_states(text: str, allergens: list[dict[str, str]], where: str) -> dict[str, str]:
    states = {row["code"]: "NOT_CONTAINS" for row in allergens}
    if not text:
        return states
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            fail(f"{where} 的 state_overrides 片段缺少等号：{part!r}")
        code, raw = (item.strip() for item in part.split("=", 1))
        if code not in states:
            fail(f"{where} 的 state_overrides 含未知过敏原：{code!r}")
        value = STATE_ALIASES.get(raw.lower())
        if value is None:
            fail(f"{where} 的 state_overrides[{code}] 值非法：{raw!r}")
        states[code] = value
    return states


def resolve_sources(
    text: str,
    field: str,
    where: str,
    sources: dict[str, dict[str, str]],
) -> list[str]:
    ids = split_ids(text, field, where)
    for source_id in ids:
        if source_id not in sources:
            fail(f"{where} 引用了不存在的来源：{source_id}")
    return ids


def load_ingredients(
    allergens: list[dict[str, str]],
    sources: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    rows = read_csv(DATA_DIR / "ingredients.csv")
    ingredients: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for row in rows:
        ingredient_id = row["ingredient_id"]
        where = f"ingredients.csv 第 {row['_line']} 行"
        if not ingredient_id:
            fail(f"{where} 缺少 ingredient_id")
        if ingredient_id in ingredients:
            fail(f"{where} 的 ingredient_id 重复：{ingredient_id}")
        if row["name"] in names:
            fail(f"{where} 的配料名重复：{row['name']}")
        names.add(row["name"])
        credibility = row["credibility"].upper()
        if credibility not in CRED_RANK:
            fail(f"{where} 的 credibility 必须是 A/B/C/D")
        states = parse_states(row["state_overrides"], allergens, where)
        source_ids = resolve_sources(row["source_ids"], "source_ids", where, sources)
        ingredients[ingredient_id] = {
            "ingredient_id": ingredient_id,
            "name": row["name"],
            "category": row["category"],
            "evidence": row["evidence"],
            "states": states,
            "source_ids": source_ids,
            "credibility": credibility,
            "manual_review": parse_int(row["manual_review"], "manual_review", where) == 1,
            "note": row["note"],
        }
    return ingredients


def load_scenarios() -> list[dict[str, Any]]:
    rows = read_csv(DATA_DIR / "scenarios.csv")
    scenarios: list[dict[str, Any]] = []
    names: set[str] = set()
    ids: set[str] = set()
    for row in rows:
        where = f"scenarios.csv 第 {row['_line']} 行"
        scenario_id = row["scenario_id"]
        if not scenario_id or scenario_id in ids:
            fail(f"{where} 的 scenario_id 为空或重复")
        if row["name"] in names:
            fail(f"{where} 的场景名重复：{row['name']}")
        target = parse_int(row["target_size"], "target_size", where)
        if not 13 <= target <= 30:
            fail(f"{where} 的 target_size 必须在 13–30 之间，实际为 {target}")
        ids.add(scenario_id)
        names.add(row["name"])
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "name": row["name"],
                "description": row["description"],
                "target_size": target,
                "note": row["note"],
            }
        )
    return scenarios


def min_credibility(values: Iterable[str]) -> str:
    return CRED_LABEL[max(CRED_RANK[value] for value in values)]


def derive_dish(
    row: dict[str, str],
    allergens: list[dict[str, str]],
    ingredients: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, str]],
    scenario_names: set[str],
) -> dict[str, Any]:
    where = f"dishes.csv 第 {row['_line']} 行"
    dish_id = row["dish_id"]
    if not dish_id:
        fail(f"{where} 缺少 dish_id")
    scenario_list = split_ids(row["scenarios"], "scenarios", where)
    for name in scenario_list:
        if name not in scenario_names:
            fail(f"{where} 引用了未定义的场景：{name}")
    ingredient_ids = split_ids(row["ingredient_ids"], "ingredient_ids", where)
    selected: list[dict[str, Any]] = []
    for ingredient_id in ingredient_ids:
        if ingredient_id not in ingredients:
            fail(f"{where} 引用了不存在的配料：{ingredient_id}")
        selected.append(ingredients[ingredient_id])

    states: dict[str, str] = {}
    contains_basis: dict[str, list[str]] = {}
    unknown_basis: dict[str, list[str]] = {}
    for allergen in allergens:
        code = allergen["code"]
        contains = [item["ingredient_id"] for item in selected if item["states"][code] == "CONTAINS"]
        unknown = [item["ingredient_id"] for item in selected if item["states"][code] == "UNKNOWN"]
        if contains:
            states[code] = "CONTAINS"
            contains_basis[code] = contains
            unknown_basis[code] = []
        elif unknown:
            states[code] = "UNKNOWN"
            contains_basis[code] = []
            unknown_basis[code] = unknown
        else:
            states[code] = "NOT_CONTAINS"
            contains_basis[code] = []
            unknown_basis[code] = []

    source_ids = resolve_sources(row["source_ids"], "source_ids", where, sources)
    credibility = min_credibility(item["credibility"] for item in selected)
    review_hint = parse_int(row["review_hint"], "review_hint", where) == 1
    manual_review = (
        review_hint
        or any(item["manual_review"] for item in selected)
        or any(state == "UNKNOWN" for state in states.values())
    )
    price = None
    if row["price_cents"]:
        price = parse_float_or_int(row["price_cents"], "price_cents", where)
    return {
        "dish_id": dish_id,
        "name": row["name"],
        "cuisine": row["cuisine"],
        "scenarios": scenario_list,
        "price_cents": price,
        "price_source": row["price_source"],
        "ingredient_ids": ingredient_ids,
        "other_ingredients": row["other_ingredients"],
        "source_ids": source_ids,
        "note": row["note"],
        "states": states,
        "contains_basis": contains_basis,
        "unknown_basis": unknown_basis,
        "credibility": credibility,
        "manual_review": manual_review,
    }


def load_dishes(
    allergens: list[dict[str, str]],
    ingredients: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, str]],
    scenarios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = read_csv(DATA_DIR / "dishes.csv")
    scenario_names = {item["name"] for item in scenarios}
    dishes: list[dict[str, Any]] = []
    ids: set[str] = set()
    names: set[str] = set()
    for row in rows:
        dish = derive_dish(row, allergens, ingredients, sources, scenario_names)
        if dish["dish_id"] in ids:
            fail(f"dishes.csv 的 dish_id 重复：{dish['dish_id']}")
        if dish["name"] in names:
            fail(f"dishes.csv 的菜品名重复：{dish['name']}")
        ids.add(dish["dish_id"])
        names.add(dish["name"])
        dishes.append(dish)
    return dishes


def price_bands(dishes: list[dict[str, Any]]) -> dict[str, str]:
    values = sorted(dish["price_cents"] for dish in dishes if dish["price_cents"] is not None)
    if len(values) < 3:
        return {dish["dish_id"]: "unknown" for dish in dishes}
    first = values[len(values) // 3]
    second = values[(2 * len(values)) // 3]
    bands: dict[str, str] = {}
    for dish in dishes:
        price = dish["price_cents"]
        if price is None:
            bands[dish["dish_id"]] = "unknown"
        elif price <= first:
            bands[dish["dish_id"]] = "low"
        elif price <= second:
            bands[dish["dish_id"]] = "mid"
        else:
            bands[dish["dish_id"]] = "high"
    return bands


def select_candidates(
    pool: list[dict[str, Any]],
    target: int,
    seed: int,
    index: int,
    allergen_codes: list[str],
    bands: dict[str, str],
) -> list[dict[str, Any]]:
    if len(pool) < 13:
        fail(f"场景候选池只有 {len(pool)} 项，不足 13 项")
    target = min(target, 30, len(pool))
    if target < 13:
        fail(f"场景目标规模 {target} 小于 13")
    rng = random.Random(seed * 1000 + index)
    remaining = sorted(pool, key=lambda item: item["dish_id"])
    rng.shuffle(remaining)
    selected: list[dict[str, Any]] = []
    covered: set[tuple[str, str]] = set()
    cuisines: set[str] = set()
    used_bands: set[str] = set()
    while len(selected) < target:
        best_index = 0
        best_score: tuple[int, int, int] | None = None
        for position, dish in enumerate(remaining):
            new_pairs = 0
            for code in allergen_codes:
                state = dish["states"][code]
                if state in ("CONTAINS", "UNKNOWN") and (code, state) not in covered:
                    new_pairs += 1
            score = (
                new_pairs,
                1 if dish["cuisine"] not in cuisines else 0,
                1 if bands[dish["dish_id"]] not in used_bands else 0,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_index = position
        dish = remaining.pop(best_index)
        selected.append(dish)
        for code in allergen_codes:
            state = dish["states"][code]
            if state in ("CONTAINS", "UNKNOWN"):
                covered.add((code, state))
        cuisines.add(dish["cuisine"])
        used_bands.add(bands[dish["dish_id"]])
    return sorted(selected, key=lambda item: item["dish_id"])


def state_counts(dishes: list[dict[str, Any]], allergen_codes: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for code in allergen_codes:
        counter = Counter(dish["states"][code] for dish in dishes)
        result[code] = {state: counter.get(state, 0) for state in STATES}
    return result


def render_outputs(
    seed: int,
    allergens: list[dict[str, str]],
    sources: dict[str, dict[str, str]],
    ingredients: dict[str, dict[str, Any]],
    dishes: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
) -> dict[str, bytes]:
    codes = [row["code"] for row in allergens]
    names = {row["code"]: row["name_zh"] for row in allergens}
    bands = price_bands(dishes)
    rendered: dict[str, bytes] = {}

    ingredient_headers = [
        "ingredient_id",
        "name",
        "category",
        "evidence",
        *codes,
        "source_ids",
        "credibility",
        "manual_review",
        "note",
    ]
    ingredient_rows = []
    for ingredient in ingredients.values():
        ingredient_rows.append(
            [
                ingredient["ingredient_id"],
                ingredient["name"],
                ingredient["category"],
                ingredient["evidence"],
                *[ingredient["states"][code] for code in codes],
                ";".join(ingredient["source_ids"]),
                ingredient["credibility"],
                "1" if ingredient["manual_review"] else "0",
                ingredient["note"],
            ]
        )
    rendered["ingredients_allergen_tristate.csv"] = csv_bytes(ingredient_headers, ingredient_rows)

    dish_headers = [
        "dish_id",
        "name",
        "cuisine",
        "scenarios",
        "price_cents",
        "price_source",
        *codes,
        "contains_allergens",
        "unknown_allergens",
        "not_contains_allergens",
        "source_ids",
        "credibility",
        "manual_review",
        "note",
    ]
    dish_rows = []
    for dish in dishes:
        contains = [code for code in codes if dish["states"][code] == "CONTAINS"]
        unknown = [code for code in codes if dish["states"][code] == "UNKNOWN"]
        not_contains = [code for code in codes if dish["states"][code] == "NOT_CONTAINS"]
        dish_rows.append(
            [
                dish["dish_id"],
                dish["name"],
                dish["cuisine"],
                ";".join(dish["scenarios"]),
                dish["price_cents"],
                dish["price_source"],
                *[dish["states"][code] for code in codes],
                ";".join(contains),
                ";".join(unknown),
                ";".join(not_contains),
                ";".join(dish["source_ids"]),
                dish["credibility"],
                "1" if dish["manual_review"] else "0",
                dish["note"],
            ]
        )
    rendered["dishes_allergen_tristate.csv"] = csv_bytes(dish_headers, dish_rows)

    long_headers = [
        "dish_id",
        "name",
        "cuisine",
        "allergen_code",
        "allergen_name_zh",
        "state",
        "basis_ingredient_ids",
        "credibility",
        "manual_review",
    ]
    long_rows = []
    for dish in dishes:
        for code in codes:
            basis: list[str] = []
            if dish["states"][code] == "CONTAINS":
                basis = dish["contains_basis"][code]
            elif dish["states"][code] == "UNKNOWN":
                basis = dish["unknown_basis"][code]
            long_rows.append(
                [
                    dish["dish_id"],
                    dish["name"],
                    dish["cuisine"],
                    code,
                    names[code],
                    dish["states"][code],
                    ";".join(basis),
                    dish["credibility"],
                    "1" if dish["manual_review"] else "0",
                ]
            )
    rendered["dishes_allergen_long.csv"] = csv_bytes(long_headers, long_rows)

    jsonl_records = []
    for dish in dishes:
        jsonl_records.append(
            {
                "dish_id": dish["dish_id"],
                "name": dish["name"],
                "cuisine": dish["cuisine"],
                "scenarios": dish["scenarios"],
                "price_cents": dish["price_cents"],
                "price_source": dish["price_source"],
                "allergens": {
                    code: {
                        "state": dish["states"][code],
                        "basis": dish["contains_basis"][code]
                        if dish["states"][code] == "CONTAINS"
                        else dish["unknown_basis"][code]
                        if dish["states"][code] == "UNKNOWN"
                        else [],
                    }
                    for code in codes
                },
                "source_ids": dish["source_ids"],
                "credibility": dish["credibility"],
                "manual_review": dish["manual_review"],
                "note": dish["note"],
            }
        )
    rendered["dishes_allergen_tristate.jsonl"] = jsonl_bytes(jsonl_records)

    scenario_payload = []
    scenario_stats: dict[str, dict[str, Any]] = {}
    for index, scenario in enumerate(scenarios, start=1):
        pool = [dish for dish in dishes if scenario["name"] in dish["scenarios"]]
        chosen = select_candidates(
            pool,
            scenario["target_size"],
            seed,
            index,
            codes,
            bands,
        )
        counts = state_counts(chosen, codes)
        scenario_stats[scenario["scenario_id"]] = {
            "name": scenario["name"],
            "pool_size": len(pool),
            "candidate_count": len(chosen),
            "mean_unknown_per_candidate": round(
                statistics.mean(
                    sum(1 for code in codes if dish["states"][code] == "UNKNOWN")
                    for dish in chosen
                ),
                3,
            ),
            "allergen_state_counts": counts,
        }
        scenario_payload.append(
            {
                "scenario_id": scenario["scenario_id"],
                "name": scenario["name"],
                "description": scenario["description"],
                "target_size": scenario["target_size"],
                "pool_size": len(pool),
                "candidate_count": len(chosen),
                "dishes": [
                    {
                        "dish_id": dish["dish_id"],
                        "name": dish["name"],
                        "cuisine": dish["cuisine"],
                        "price_cents": dish["price_cents"],
                        "allergens": {
                            code: dish["states"][code] for code in codes
                        },
                    }
                    for dish in chosen
                ],
            }
        )
        scenario_headers = ["dish_id", "name", "cuisine", "price_cents", *codes]
        scenario_rows = [
            [
                dish["dish_id"],
                dish["name"],
                dish["cuisine"],
                dish["price_cents"],
                *[dish["states"][code] for code in codes],
            ]
            for dish in chosen
        ]
        rendered[f"scenarios/{scenario['scenario_id']}.csv"] = csv_bytes(
            scenario_headers, scenario_rows
        )

    rendered["scenarios.json"] = json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "seed": seed,
            "scenarios": scenario_payload,
        }
    )

    counts = state_counts(dishes, codes)
    unknown_per_dish = [
        sum(1 for code in codes if dish["states"][code] == "UNKNOWN") for dish in dishes
    ]
    credibility_counter = Counter(dish["credibility"] for dish in dishes)
    manual_counter = Counter(dish["manual_review"] for dish in dishes)
    cuisine_counter = Counter(dish["cuisine"] for dish in dishes)
    source_usage: dict[str, dict[str, int]] = {
        source_id: {"allergens": 0, "ingredients": 0, "dishes": 0}
        for source_id in sources
    }
    for allergen in allergens:
        for source_id in allergen["source_ids"]:
            source_usage[source_id]["allergens"] += 1
    for ingredient in ingredients.values():
        for source_id in ingredient["source_ids"]:
            source_usage[source_id]["ingredients"] += 1
    for dish in dishes:
        for source_id in dish["source_ids"]:
            source_usage[source_id]["dishes"] += 1
    price_source_counter = Counter(
        dish["price_source"] for dish in dishes if dish["price_cents"] is not None
    )
    prices = [dish["price_cents"] for dish in dishes if dish["price_cents"] is not None]
    positive_allergens = [code for code in codes if counts[code]["CONTAINS"] > 0]
    unknown_allergens = [code for code in codes if counts[code]["UNKNOWN"] > 0]
    warnings: list[str] = []
    missing_positive = [code for code in codes if counts[code]["CONTAINS"] == 0]
    if missing_positive:
        warnings.append(
            "以下过敏原在菜品中没有 CONTAINS 正例，只能用于负例或未知例实验："
            + "、".join(missing_positive)
        )
    missing_unknown = [code for code in codes if counts[code]["UNKNOWN"] == 0]
    if missing_unknown:
        warnings.append(
            "以下过敏原没有 UNKNOWN 例，三态覆盖不完整：" + "、".join(missing_unknown)
        )
    stats = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "counts": {
            "allergens": len(allergens),
            "ingredients": len(ingredients),
            "dishes": len(dishes),
            "scenarios": len(scenarios),
            "sources": len(sources),
        },
        "allergen_state_counts": counts,
        "allergen_unknown_rate": {
            code: round(counts[code]["UNKNOWN"] / len(dishes), 6) for code in codes
        },
        "allergen_positive_dish_count": {
            code: counts[code]["CONTAINS"] for code in codes
        },
        "coverage": {
            "allergens_with_contains": positive_allergens,
            "allergens_without_contains": missing_positive,
            "allergens_with_unknown": unknown_allergens,
            "allergens_without_unknown": missing_unknown,
        },
        "dish_unknown_count": {
            "mean": round(statistics.mean(unknown_per_dish), 3),
            "median": round(statistics.median(unknown_per_dish), 3),
            "max": max(unknown_per_dish),
            "min": min(unknown_per_dish),
        },
        "dishes_with_any_unknown": sum(1 for value in unknown_per_dish if value > 0),
        "dishes_without_unknown": sum(1 for value in unknown_per_dish if value == 0),
        "credibility_counts": {label: credibility_counter.get(label, 0) for label in "ABCD"},
        "manual_review_counts": {
            "true": manual_counter.get(True, 0),
            "false": manual_counter.get(False, 0),
        },
        "cuisine_counts": dict(sorted(cuisine_counter.items())),
        "scenario_summary": scenario_stats,
        "price": {
            "non_null": len(prices),
            "missing": len(dishes) - len(prices),
            "min_cents": min(prices) if prices else None,
            "median_cents": int(statistics.median(prices)) if prices else None,
            "max_cents": max(prices) if prices else None,
        },
        "source_usage": source_usage,
        "price_source_usage": dict(sorted(price_source_counter.items())),
        "warnings": warnings,
    }
    rendered["stats.json"] = json_bytes(stats)

    input_hashes = {
        path.name: sha256(path.read_bytes())
        for path in sorted(DATA_DIR.glob("*.csv"))
    }
    output_hashes = {
        name: sha256(data)
        for name, data in sorted(rendered.items())
        if name != "manifest.json"
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "input_sha256": input_hashes,
        "output_sha256": output_hashes,
    }
    rendered["manifest.json"] = json_bytes(manifest)
    return rendered


def write_outputs(rendered: dict[str, bytes]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for relative, payload in rendered.items():
        target = OUTPUT_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    scenario_dir = OUTPUT_DIR / "scenarios"
    if scenario_dir.exists():
        expected = {
            Path(relative).name
            for relative in rendered
            if relative.startswith("scenarios/")
        }
        for path in scenario_dir.glob("*.csv"):
            if path.name not in expected:
                path.unlink()


def check_outputs(rendered: dict[str, bytes]) -> None:
    problems: list[str] = []
    for relative, payload in rendered.items():
        target = OUTPUT_DIR / relative
        if not target.exists():
            problems.append(f"缺少输出：{relative}")
        elif target.read_bytes() != payload:
            problems.append(f"输出不一致：{relative}")
    scenario_dir = OUTPUT_DIR / "scenarios"
    if scenario_dir.exists():
        expected = {
            Path(relative).name
            for relative in rendered
            if relative.startswith("scenarios/")
        }
        for path in scenario_dir.glob("*.csv"):
            if path.name not in expected:
                problems.append(f"存在多余场景文件：{path.name}")
    if problems:
        for problem in problems:
            print(f"[不一致] {problem}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[校验通过] {len(rendered)} 个输出文件与固定种子重建结果完全一致。")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建菜品—过敏原三态数据集")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    parser.add_argument("--check", action="store_true", help="校验现有输出是否可复现")
    args = parser.parse_args()

    sources = load_sources()
    allergens = load_allergens(sources)
    ingredients = load_ingredients(allergens, sources)
    scenarios = load_scenarios()
    dishes = load_dishes(allergens, ingredients, sources, scenarios)
    if not dishes:
        fail("dishes.csv 没有有效数据")
    for scenario in scenarios:
        pool = [dish for dish in dishes if scenario["name"] in dish["scenarios"]]
        if len(pool) < 13:
            fail(
                f"场景「{scenario['name']}」候选池只有 {len(pool)} 项，"
                f"无法满足单次 13–30 项的要求"
            )

    rendered = render_outputs(args.seed, allergens, sources, ingredients, dishes, scenarios)
    if args.check:
        check_outputs(rendered)
        return
    write_outputs(rendered)
    stats = json.loads(rendered["stats.json"])
    print(
        "构建完成："
        f"{stats['counts']['dishes']} 道菜、"
        f"{stats['counts']['ingredients']} 种配料、"
        f"{stats['counts']['allergens']} 类过敏原、"
        f"{stats['counts']['scenarios']} 个场景，"
        f"固定种子 {args.seed}。"
    )
    if stats["warnings"]:
        for message in stats["warnings"]:
            warn(message)


if __name__ == "__main__":
    main()
