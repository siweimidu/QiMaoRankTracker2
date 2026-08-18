# -*- coding: utf-8 -*-
"""
七猫榜单数据构建脚本：读取爬虫快照 -> 生成 latest_ranks / market_summary / 静态 API / AI 分析。

输入（爬虫产出，勿改）：
    data/{slug}/snapshots/ranks_YYYYMMDD.json
    data/categories/snapshots/ranks_YYYYMMDD.json

输出（详见 .trae/specs/build-qimao-rank-tracker/api-schema.md）：
    每榜：data/{slug}/{latest_ranks,market_summary,dates}.json
          data/{slug}/trends/YYYY-MM-DD.json
          data/{slug}/ai_cache/YYYY-MM-DD.json（AI 结果留档，随 git 提交）
          api/{slug}/latest.json + api/{slug}/latest/{all,小类名}.json
    分类层：api/categories/latest.json + api/categories/latest/{小类名}.json
    全站：api/{boards,status,history,black-horses,authors,cross-board}.json
          api/books/{book_id}.json

环境变量：
    API_BASE_URL / API_KEY / API_MODEL   三者齐备才启用 AI（OpenAI 兼容）
    QM_BUILD_BOARDS                      逗号分隔 slug 子集，仅构建这些榜（调试用）

main() 顺序：建全站索引 -> 逐榜构建 -> 分类层 -> 全站聚合 -> AI（缓存感知）-> 汇总。
首日（只有一份快照）所有趋势字段给安全默认值，不报错。
"""

import os
import re
import sys
import glob
import json
from datetime import datetime, timedelta
from urllib.parse import quote

# 允许从仓库根目录导入 boards_config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boards_config import enabled_boards, board_public_meta  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
API_DIR = os.path.join(BASE_DIR, "api")

DAYS_RETAINED = 90      # history 保留天数
STALE_DAYS = 3          # 更新停滞判定阈值（天）
AI_BATCH_SIZE = 3       # AI 分类速评每批分类数
KEYWORDS_TOP = 30       # 关键词统计取前 N
HORSE_TOP = 20          # 黑马榜取前 N
AUTHORS_TOP = 30        # 作者榜取前 N
CROSS_TOP_LIMIT = 200   # 跨榜书最多输出条数（防膨胀）
DROPPED_INTRO_LEN = 100  # 掉榜书简介截断长度
FIRST_DAY_TEXT = "首日收录，暂无趋势对比"

SNAP_DATE_RE = re.compile(r"ranks_(\d{8})\.json$")


# ============================================================
#  基础 IO
# ============================================================

def read_json(path: str):
    """读 JSON 文件；不存在或损坏返回 None（损坏时打印警告）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [警告] 读取 JSON 失败({path}): {e}")
        return None


def write_json(path: str, data):
    """统一写 JSON：UTF-8、中文可读、自动建目录。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iso_date(compact: str) -> str:
    """'20260818' -> '2026-08-18'。"""
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


# ============================================================
#  快照加载与全站索引
# ============================================================

def list_snapshot_files(slug: str) -> list:
    """某榜全部快照文件，按文件名日期升序 [(compact_date, path)]。"""
    snap_dir = os.path.join(DATA_DIR, slug, "snapshots")
    result = []
    for path in glob.glob(os.path.join(snap_dir, "ranks_*.json")):
        m = SNAP_DATE_RE.search(os.path.basename(path))
        if m:
            result.append((m.group(1), path))
    return sorted(result, key=lambda x: x[0])


def build_global_index(boards: list) -> dict:
    """建全站索引：确定构建日、加载当日/上一份快照、books 全站计数与对比数据。

    构建日 = 全部快照文件名日期的最大值（支持当天构建与隔天补跑）。
    """
    now = datetime.now()
    # ---- 第一遍：找最大快照日期 ----
    all_dates = []
    per_board_files = {}
    for board in boards:
        files = list_snapshot_files(board["slug"])
        per_board_files[board["slug"]] = files
        all_dates.extend(d for d, _ in files)

    if not all_dates:
        print("[错误] 未找到任何榜单快照，无可构建数据。")
        return {}

    build_date = max(all_dates)  # YYYYMMDD
    build_iso = iso_date(build_date)

    index = {
        "build_date": build_date,
        "build_date_iso": build_iso,
        "now": now,
        "board_snapshots": {},   # slug -> 当日快照数据
        "prev_snapshots": {},    # slug -> 上一份快照数据（可能为 None）
        "snapshot_dates": {},    # slug -> ["YYYY-MM-DD", ...] 升序
        "boards_count": {},      # book_id -> 当日出现榜数
        "today_entries": {},     # book_id -> [{slug, board_name, rank, heat, book}]
        "compare": {},           # slug -> {book_id: {rank_change, heat_change, prev_heat, is_new}}
        "new_ids": set(),        # 当日全站新上榜 book_id 集合
        "last_scraped_at": "",
        "missing": [],
        "board_files": per_board_files,
    }

    # ---- 第二遍：加载当日与上一份快照 ----
    for board in boards:
        slug = board["slug"]
        files = per_board_files[slug]
        index["snapshot_dates"][slug] = [iso_date(d) for d, _ in files]

        today_pair = next(((d, p) for d, p in files if d == build_date), None)
        if today_pair is None:
            index["missing"].append(
                f"{board['name']}({slug}) 缺少 {build_iso} 当日快照")
            continue

        snap = read_json(today_pair[1])
        if not snap or not snap.get("books"):
            index["missing"].append(
                f"{board['name']}({slug}) 当日快照为空或损坏({today_pair[1]})")
            continue
        index["board_snapshots"][slug] = snap

        scraped = snap.get("scraped_at") or ""
        if scraped > index["last_scraped_at"]:
            index["last_scraped_at"] = scraped

        # 上一份：日期 < 构建日的最近一份（允许跳档）
        prev_snap = None
        prev_pairs = [(d, p) for d, p in files if d < build_date]
        if prev_pairs:
            prev_snap = read_json(prev_pairs[-1][1])
        index["prev_snapshots"][slug] = prev_snap

        # ---- 全站计数与在榜条目 ----
        for book in snap["books"]:
            bid = book.get("book_id") or 0
            if not bid:
                continue
            index["boards_count"][bid] = index["boards_count"].get(bid, 0) + 1
            index["today_entries"].setdefault(bid, []).append({
                "slug": slug,
                "board_name": board["name"],
                "rank": book.get("rank") or 0,
                "heat": book.get("heat") or 0,
                "book": book,
            })

        # ---- 与上一份对比：rank_change / heat_change / is_new ----
        compare = {}
        prev_index = {}
        if prev_snap:
            for pb in prev_snap.get("books", []):
                pid = pb.get("book_id") or 0
                if pid:
                    prev_index[pid] = pb
        for book in snap["books"]:
            bid = book.get("book_id") or 0
            if not bid:
                continue
            pb = prev_index.get(bid)
            if pb is not None:
                compare[bid] = {
                    "rank_change": (pb.get("rank") or 0) - (book.get("rank") or 0),
                    "heat_change": (book.get("heat") or 0) - (pb.get("heat") or 0),
                    "prev_heat": pb.get("heat") or 0,
                    "is_new": False,
                }
            else:
                compare[bid] = {
                    "rank_change": 0, "heat_change": 0, "prev_heat": 0,
                    "is_new": bool(prev_snap),
                }
                if prev_snap:
                    index["new_ids"].add(bid)
        index["compare"][slug] = compare

    print(f"[全站索引] 构建日 {build_iso}，当日有快照 {len(index['board_snapshots'])}/{len(boards)} 榜，"
          f"去重 {len(index['boards_count'])} 本，新上榜 {len(index['new_ids'])} 本")
    return index


# ============================================================
#  逐榜构建
# ============================================================

def is_stale_update(updated_at: str, now: datetime) -> bool:
    """updated_at 距今超过 STALE_DAYS 天视为更新停滞；解析失败给安全默认 False。"""
    if not updated_at:
        return False
    try:
        t = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return (now - t).total_seconds() > STALE_DAYS * 86400


def empty_trend() -> dict:
    """首日/无对比数据时的安全默认趋势。"""
    return {
        "new_count": 0, "dropped_count": 0,
        "new_books": [], "dropped_books": [],
        "top_risers": [], "top_fallers": [], "heat_leaders": [],
        "summary": FIRST_DAY_TEXT, "ai_source": "first-day",
    }


def rule_category_summary(name: str, trend: dict) -> str:
    """AI 不可用时的规则分类速评文案。"""
    if trend.get("ai_source") == "first-day":
        return FIRST_DAY_TEXT
    parts = []
    new_books = [b["title"] for b in trend.get("new_books", [])[:3]]
    if new_books:
        parts.append(f"新上榜 {'、'.join('《'+t+'》' for t in new_books)}")
    dropped = [b["title"] for b in trend.get("dropped_books", [])[:3]]
    if dropped:
        parts.append(f"{'、'.join('《'+t+'》' for t in dropped)} 掉出榜单")
    risers = trend.get("top_risers") or []
    if risers:
        parts.append(f"《{risers[0]['title']}》上升{risers[0]['rank_change']}位领涨")
    fallers = trend.get("top_fallers") or []
    if fallers:
        parts.append(f"《{fallers[0]['title']}》下滑{abs(fallers[0]['rank_change'])}位")
    leaders = trend.get("heat_leaders") or []
    if leaders:
        hc = leaders[0].get("heat_change") or 0
        parts.append(f"《{leaders[0]['title']}》热度{'+' if hc >= 0 else ''}{hc}")
    if not parts:
        parts.append("榜单结构稳定，无明显变动")
    return f"「{name}」" + "；".join(parts) + "。"


def enhance_book(book: dict, cmp_info: dict, boards_count: dict,
                 now: datetime) -> dict:
    """榜单书对象 + 构建期计算字段（rank_change/heat_change/is_new/boards_count/stale_update/category）。"""
    out = dict(book)
    bid = out.get("book_id") or 0
    out["rank_change"] = cmp_info["rank_change"] if cmp_info else 0
    out["heat_change"] = cmp_info["heat_change"] if cmp_info else 0
    out["is_new"] = bool(cmp_info and cmp_info["is_new"])
    out["boards_count"] = boards_count.get(bid, 1) if bid else 1
    out["stale_update"] = is_stale_update(out.get("updated_at") or "", now)
    out["category"] = out.get("minor") or ""
    return out


def build_category_trend(today_books: list, prev_minor_books: list,
                         has_prev: bool) -> dict:
    """单个小类的趋势对比（当日书 vs 上一份快照同小类书，按 book_id）。"""
    if not has_prev:
        return empty_trend()

    today_ids = {b.get("book_id") or 0 for b in today_books}
    prev_index = {}
    for pb in prev_minor_books:
        pid = pb.get("book_id") or 0
        if pid:
            prev_index[pid] = pb

    new_books = [{"title": b.get("title", ""), "book_id": b.get("book_id") or 0}
                 for b in today_books
                 if (b.get("book_id") or 0) and (b.get("book_id") or 0) not in prev_index]
    dropped_books = [
        {"title": pb.get("title", ""), "book_id": pid,
         "intro": (pb.get("intro") or "")[:DROPPED_INTRO_LEN]}
        for pid, pb in prev_index.items() if pid not in today_ids]

    risers = sorted(
        (b for b in today_books if (b.get("rank_change") or 0) > 0),
        key=lambda b: abs(b["rank_change"]), reverse=True)[:3]
    fallers = sorted(
        (b for b in today_books if (b.get("rank_change") or 0) < 0),
        key=lambda b: abs(b["rank_change"]), reverse=True)[:3]
    heat_leaders = sorted(
        (b for b in today_books if (b.get("heat_change") or 0) != 0),
        key=lambda b: abs(b["heat_change"]), reverse=True)[:3]

    trend = {
        "new_count": len(new_books),
        "dropped_count": len(dropped_books),
        "new_books": new_books,
        "dropped_books": dropped_books,
        "top_risers": [{"title": b.get("title", ""), "book_id": b.get("book_id") or 0,
                        "rank_change": b.get("rank_change") or 0} for b in risers],
        "top_fallers": [{"title": b.get("title", ""), "book_id": b.get("book_id") or 0,
                         "rank_change": b.get("rank_change") or 0} for b in fallers],
        "heat_leaders": [{"title": b.get("title", ""), "book_id": b.get("book_id") or 0,
                          "heat_change": b.get("heat_change") or 0} for b in heat_leaders],
        "summary": "",   # 由规则文案/AI 填充
        "ai_source": "rule",
    }
    return trend


def build_board_categories(today_books: list, prev_books: list,
                           has_prev: bool) -> list:
    """按小类(minor)聚合一榜书籍为 categories 数组。"""
    today_by_minor = {}
    for b in today_books:
        today_by_minor.setdefault(b.get("minor") or "未分类", []).append(b)
    prev_by_minor = {}
    if has_prev:
        for pb in prev_books:
            prev_by_minor.setdefault(pb.get("minor") or "未分类", []).append(pb)

    categories = []
    for minor, books in today_by_minor.items():
        trend = build_category_trend(
            books, prev_by_minor.get(minor, []), has_prev)
        trend["summary"] = rule_category_summary(minor, trend)
        categories.append({
            "name": minor,
            "major": (books[0].get("major") or "") if books else "",
            "count": len(books),
            "total_heat": sum(b.get("heat") or 0 for b in books),
            "trend": trend,
            "books": books,
        })
    categories.sort(key=lambda c: (-c["count"], -c["total_heat"], c["name"]))
    return categories


def count_keywords(books: list, keywords: list) -> list:
    """统计书籍 intro+minor 命中频道词表的词频，取前 KEYWORDS_TOP。"""
    counter = {}
    for b in books:
        text = (b.get("intro") or "") + " " + (b.get("minor") or "")
        if not text:
            continue
        for kw in keywords:
            c = text.count(kw)
            if c:
                counter[kw] = counter.get(kw, 0) + c
    ranked = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    return [{"word": w, "count": c} for w, c in ranked[:KEYWORDS_TOP]]


def build_genre_groups(board: dict, today_books: list, prev_books: list,
                       has_prev: bool) -> list:
    """按 boards_config 的 genre_groups（大类聚合）统计每赛道书数/总热度/书数变化。"""
    groups_cfg = board.get("genre_groups") or []

    def group_counts(books: list) -> dict:
        counts = {g["name"]: [] for g in groups_cfg}
        matched = set()
        for g in groups_cfg:
            if not g.get("majors"):
                continue
            majors = set(g["majors"])
            for b in books:
                if b.get("major") in majors:
                    counts[g["name"]].append(b)
                    matched.add(id(b))
        for g in groups_cfg:
            if not g.get("majors"):  # 空 majors = 兜底组
                for b in books:
                    if id(b) not in matched:
                        counts[g["name"]].append(b)
        return counts

    today_counts = group_counts(today_books)
    if has_prev:
        prev_counts = group_counts(prev_books)
    else:
        prev_counts = {}  # 首日无对比，change 统一为 0

    result = []
    for g in groups_cfg:
        tbooks = today_counts.get(g["name"], [])
        if not tbooks:
            continue
        result.append({
            "name": g["name"],
            "count": len(tbooks),
            "total_heat": sum(b.get("heat") or 0 for b in tbooks),
            "change": len(tbooks) - len(prev_counts.get(g["name"], [])),
        })
    result.sort(key=lambda x: -x["count"])
    return result


def build_board_trends(board: dict, index: dict) -> dict:
    """构建单个榜单的全部产物（AI 步骤之前，summary 均为规则文案/首日文案）。

    返回内存 payload（含 market 概要），供 AI 步骤填充后统一重写文件。
    """
    slug = board["slug"]
    snap = index["board_snapshots"][slug]
    prev_snap = index["prev_snapshots"].get(slug)
    has_prev = bool(prev_snap and prev_snap.get("books"))
    compare = index["compare"].get(slug, {})
    now = index["now"]
    date_iso = snap.get("date") or index["build_date_iso"]
    prev_date_iso = (prev_snap or {}).get("date") if has_prev else None

    # ---- 增强书对象 ----
    enhanced = [enhance_book(b, compare.get(b.get("book_id") or 0),
                             index["boards_count"], now)
                for b in snap["books"]]

    # ---- 小类聚合 ----
    categories = build_board_categories(enhanced, (prev_snap or {}).get("books", []),
                                        has_prev)

    # ---- 榜级统计 ----
    total_heat = sum(b.get("heat") or 0 for b in enhanced)
    # 全榜掉榜数按 book_id 差集独立计算（避免整类消失时按小类汇总漏计）
    if has_prev:
        today_ids = {b.get("book_id") or 0 for b in enhanced}
        prev_ids = {pb.get("book_id") or 0
                    for pb in prev_snap.get("books", []) if pb.get("book_id")}
        dropped_all = len(prev_ids - today_ids)
    else:
        dropped_all = 0
    stats = {
        "total_books": len(enhanced),
        "new_count": sum(1 for b in enhanced if b["is_new"]),
        "dropped_count": dropped_all,
        "avg_heat": int(total_heat / len(enhanced)) if enhanced else 0,
        "total_heat": total_heat,
        "first_day": not has_prev,
    }

    # ---- 关键词 / 赛道分组 ----
    keywords = count_keywords(enhanced, board.get("keywords") or [])
    genre_groups = build_genre_groups(board, enhanced,
                                      (prev_snap or {}).get("books", []), has_prev)

    payload = {
        "board": board,
        "date": date_iso,
        "prev_date": prev_date_iso,
        "books": enhanced,
        "categories": categories,
        "stats": stats,
        "keywords": keywords,
        "market": {
            "board": {"slug": slug, "name": board["name"]},
            "date": date_iso,
            "period_days": 1 if board.get("period") == "date" else 30,
            "overview": {"text": "全站风向待生成。", "ai_source": "rule"},
            "categories": [{"name": c["name"], "summary": c["trend"]["summary"],
                            "ai_source": c["trend"]["ai_source"]}
                           for c in categories],
            "keywords": keywords,
            "genre_groups": genre_groups,
        },
    }

    # ---- 写文件（latest_ranks / dates / trends / api 静态文件 / market_summary 占位）----
    data_dir = os.path.join(DATA_DIR, slug)
    write_json(os.path.join(data_dir, "dates.json"),
               {"dates": index["snapshot_dates"].get(slug, [])})
    write_board_outputs(payload)

    print(f"  [完成] {board['name']}({slug})：{stats['total_books']} 本 / "
          f"{len(categories)} 小类 / 新 {stats['new_count']} / 掉 {stats['dropped_count']}"
          f"{'（首日）' if not has_prev else ''}")
    return payload


# ============================================================
#  榜单产物写出（构建期与 AI 填充后共用）
# ============================================================

def cat_filename(name: str) -> str:
    """小类名 -> URL 编码文件名（不含扩展名）。"""
    return quote(name or "未分类", safe="")


def write_board_outputs(payload: dict):
    """写出一榜全部产物：latest_ranks/api 静态文件/trends/market_summary。"""
    board = payload["board"]
    slug = board["slug"]
    board_meta = board_public_meta(board)
    date_iso = payload["date"]
    data_dir = os.path.join(DATA_DIR, slug)

    latest_ranks = {
        "board": board_meta,
        "date": date_iso,
        "prev_date": payload["prev_date"],
        "books": payload["books"],
        "categories": payload["categories"],
        "stats": payload["stats"],
        "keywords": payload["keywords"],
    }
    write_json(os.path.join(data_dir, "latest_ranks.json"), latest_ranks)
    write_json(os.path.join(API_DIR, slug, "latest", "all.json"), latest_ranks)

    # ---- 小类单文件 + types 索引 ----
    latest_dir = os.path.join(API_DIR, slug, "latest")
    types = [{"name": "全部", "url": f"api/{slug}/latest/all.json"}]
    for cat in payload["categories"]:
        fn = cat_filename(cat["name"])
        types.append({"name": cat["name"], "url": f"api/{slug}/latest/{fn}.json"})
        write_json(os.path.join(latest_dir, f"{fn}.json"), {
            "board": board_meta,
            "date": date_iso,
            "category": cat,
        })
    write_json(os.path.join(API_DIR, slug, "latest.json"), {
        "board": board_meta,
        "date": date_iso,
        "types": types,
    })

    # ---- 趋势归档 ----
    write_json(os.path.join(data_dir, "trends", f"{date_iso}.json"), {
        "date": date_iso,
        "board": slug,
        "stats": payload["stats"],
        "categories": [{"name": c["name"], "trend": c["trend"]}
                       for c in payload["categories"]],
    })

    # ---- market_summary（data 归档 + api 供前端 trend.html 消费）----
    write_json(os.path.join(data_dir, "market_summary.json"), payload["market"])
    write_json(os.path.join(API_DIR, slug, "market_summary.json"), payload["market"])


# ============================================================
#  分类层（书库 Top 30）
# ============================================================

def build_categories_layer(index: dict):
    """分类层：当日分类快照 vs 上一份，输出 api/categories/latest*.json。缺快照时优雅跳过。"""
    print("\n[分类层] 构建")
    snap_dir = os.path.join(DATA_DIR, "categories", "snapshots")
    files = []
    for path in glob.glob(os.path.join(snap_dir, "ranks_*.json")):
        m = SNAP_DATE_RE.search(os.path.basename(path))
        if m:
            files.append((m.group(1), path))
    files.sort(key=lambda x: x[0])

    today_pair = next(((d, p) for d, p in files if d == index["build_date"]), None)
    if today_pair is None:
        print(f"  [跳过] 无 {index['build_date_iso']} 分类快照，分类层 API 不生成")
        return 0

    snap = read_json(today_pair[1])
    if not snap or not snap.get("categories"):
        print("  [跳过] 分类快照为空或损坏")
        return 0

    prev_snap = None
    prev_pairs = [(d, p) for d, p in files if d < index["build_date"]]
    if prev_pairs:
        prev_snap = read_json(prev_pairs[-1][1])
    has_prev = bool(prev_snap and prev_snap.get("categories"))
    prev_by_key = {}
    if has_prev:
        for pc in prev_snap["categories"]:
            prev_by_key[pc.get("key") or pc.get("name")] = pc

    date_iso = snap.get("date") or index["build_date_iso"]
    latest_dir = os.path.join(API_DIR, "categories", "latest")
    types = []
    for cat in snap["categories"]:
        name = cat.get("name") or "未分类"
        prev_cat = prev_by_key.get(cat.get("key") or name)
        prev_index = {}
        if prev_cat:
            for pb in prev_cat.get("books", []):
                pid = pb.get("book_id") or 0
                if pid:
                    prev_index[pid] = pb

        books = []
        for b in cat.get("books", []):
            out = dict(b)
            pid = out.get("book_id") or 0
            pb = prev_index.get(pid) if pid else None
            if pb is not None:
                out["rank_change"] = (pb.get("rank") or 0) - (out.get("rank") or 0)
                out["is_new"] = False
            else:
                out["rank_change"] = 0
                out["is_new"] = has_prev and bool(pid)
            books.append(out)

        fn = cat_filename(name)
        types.append({"name": name, "major": cat.get("major") or "",
                      "url": f"api/categories/latest/{fn}.json"})
        write_json(os.path.join(latest_dir, f"{fn}.json"), {
            "date": date_iso,
            "category": {
                "name": name,
                "major": cat.get("major") or "",
                "key": cat.get("key") or "",
                "url": cat.get("url") or "",
                "books": books,
            },
        })

    write_json(os.path.join(API_DIR, "categories", "latest.json"), {
        "date": date_iso,
        "categories": types,
    })
    print(f"  [完成] {len(types)} 个分类 -> api/categories/latest/"
          f"{'（首日）' if not has_prev else ''}")
    return len(types)


# ============================================================
#  全站聚合
# ============================================================

def build_black_horses(index: dict) -> list:
    """黑马榜：score = 0.6*min(rank_change,20)/20*100 + 0.4*min(max(heat_growth_pct,0),100)。

    只收录 rank_change>0 或 heat_growth_pct>5；prev_heat=0 时不计热度维度；
    每本书取各榜中 score 最高的条目，按 score 降序取前 HORSE_TOP。
    """
    best = {}
    for board in enabled_boards():
        slug = board["slug"]
        snap = index["board_snapshots"].get(slug)
        compare = index["compare"].get(slug, {})
        if not snap:
            continue
        for book in snap["books"]:
            bid = book.get("book_id") or 0
            cmp_info = compare.get(bid) if bid else None
            if not cmp_info:
                continue
            rc = cmp_info["rank_change"]
            hc = cmp_info["heat_change"]
            prev_heat = cmp_info["prev_heat"]
            hgp = (hc / prev_heat * 100.0) if prev_heat > 0 else 0.0
            if not (rc > 0 or hgp > 5):
                continue
            score = 0.6 * min(rc, 20) / 20 * 100
            if prev_heat > 0:
                score += 0.4 * min(max(hgp, 0.0), 100.0)
            horse = {
                "book_id": bid,
                "title": book.get("title", ""),
                "author": book.get("author", ""),
                "cover": book.get("cover", ""),
                "board": slug,
                "board_name": board["name"],
                "rank": book.get("rank") or 0,
                "rank_change": rc,
                "heat": book.get("heat") or 0,
                "heat_growth_pct": round(hgp, 1),
                "score": round(score, 1),
                "minor": book.get("minor") or "",
                "intro": (book.get("intro") or "")[:DROPPED_INTRO_LEN],
            }
            old = best.get(bid)
            if old is None or horse["score"] > old["score"]:
                best[bid] = horse

    horses = sorted(best.values(), key=lambda h: -h["score"])[:HORSE_TOP]
    return horses


def build_cross_board(index: dict) -> list:
    """跨榜书：boards_count>=2，含 boards/board_names/best_rank/total_heat。"""
    board_name_map = {b["slug"]: b["name"] for b in enabled_boards()}
    books = []
    for bid, entries in index["today_entries"].items():
        if len(entries) < 2:
            continue
        best = min(entries, key=lambda e: e["rank"] or 9999)
        base = best["book"]
        books.append({
            "book_id": bid,
            "title": base.get("title", ""),
            "cover": base.get("cover", ""),
            "author": base.get("author", ""),
            "minor": base.get("minor") or "",
            "boards_count": len(entries),
            "boards": [e["slug"] for e in entries],
            "board_names": [board_name_map.get(e["slug"], e["board_name"])
                            for e in entries],
            "best_rank": min(e["rank"] for e in entries),
            "total_heat": max(e["heat"] for e in entries),
        })
    books.sort(key=lambda x: (-x["boards_count"], x["best_rank"]))
    return books[:CROSS_TOP_LIMIT]


def build_authors(index: dict) -> list:
    """作者聚合：各榜当日书按作者统计 books/boards/total_heat/titles，取前 AUTHORS_TOP。"""
    agg = {}
    for bid, entries in index["today_entries"].items():
        best = min(entries, key=lambda e: e["rank"] or 9999)
        book = best["book"]
        author = (book.get("author") or "").strip()
        if not author:
            continue
        a = agg.setdefault(author, {
            "author": author,
            "author_url": book.get("author_url") or "",
            "book_ids": set(),
            "boards": set(),
            "heat_by_book": {},
            "titles": {},
        })
        a["book_ids"].add(bid)
        a["titles"].setdefault(bid, book.get("title", ""))
        a["heat_by_book"][bid] = max(a["heat_by_book"].get(bid, 0),
                                     max(e["heat"] for e in entries))
        for e in entries:
            a["boards"].add(e["slug"])

    authors = []
    for a in agg.values():
        authors.append({
            "author": a["author"],
            "author_url": a["author_url"],
            "books": len(a["book_ids"]),
            "boards": len(a["boards"]),
            "total_heat": sum(a["heat_by_book"].values()),
            "titles": [a["titles"][bid] for bid in sorted(a["titles"])],
        })
    authors.sort(key=lambda x: (-x["books"], -x["total_heat"], x["author"]))
    return authors[:AUTHORS_TOP]


def build_global_keywords(index: dict) -> list:
    """全站关键词：各榜按自身频道词表统计后合并，取前 15。"""
    merged = {}
    for board in enabled_boards():
        snap = index["board_snapshots"].get(board["slug"])
        if not snap:
            continue
        for item in count_keywords(snap["books"], board.get("keywords") or []):
            merged[item["word"]] = merged.get(item["word"], 0) + item["count"]
    ranked = sorted(merged.items(), key=lambda x: (-x[1], x[0]))
    return [{"word": w, "count": c} for w, c in ranked[:15]]


def build_history(index: dict) -> dict:
    """api/history.json：合并已有文件 + 当日全部榜单书点列，按日期升序只留近 90 天。

    返回 {"generated_at","days_retained","books":{book_id:{title,cover,author,points}}}。
    """
    generated_at = index["now"].isoformat(timespec="seconds")
    date_iso = index["build_date_iso"]
    cutoff = (datetime.strptime(date_iso, "%Y-%m-%d")
              - timedelta(days=DAYS_RETAINED)).strftime("%Y-%m-%d")

    old = read_json(os.path.join(API_DIR, "history.json")) or {}
    books_out = {}

    # ---- 旧数据先入（覆盖 meta/points 逻辑：新点列后面按 (d,b) 覆盖）----
    for bid, info in (old.get("books") or {}).items():
        books_out[bid] = {
            "title": info.get("title", ""),
            "cover": info.get("cover", ""),
            "author": info.get("author", ""),
            "points": {tuple([p.get("d", ""), p.get("b", "")]): p
                       for p in info.get("points", [])},
        }

    # ---- 当日点：每书每榜一条 {d,b,r,h}，覆盖同键旧点并刷新 meta ----
    for bid, entries in index["today_entries"].items():
        best = min(entries, key=lambda e: e["rank"] or 9999)
        base = best["book"]
        rec = books_out.setdefault(str(bid), {
            "title": "", "cover": "", "author": "", "points": {}})
        rec["title"] = base.get("title", "")
        rec["cover"] = base.get("cover", "")
        rec["author"] = base.get("author", "")
        for e in entries:
            rec["points"][(date_iso, e["slug"])] = {
                "d": date_iso, "b": e["slug"],
                "r": e["rank"], "h": e["heat"]}

    # ---- 裁剪 90 天 + 排序输出 ----
    result_books = {}
    for bid, rec in books_out.items():
        points = [p for (d, _), p in rec["points"].items() if d >= cutoff]
        if not points:
            continue  # 全部超期，丢弃
        points.sort(key=lambda p: (p.get("d", ""), p.get("b", "")))
        result_books[bid] = {
            "title": rec["title"], "cover": rec["cover"],
            "author": rec["author"], "points": points,
        }

    return {"generated_at": generated_at, "days_retained": DAYS_RETAINED,
            "books": result_books}


def build_book_pages(index: dict, history: dict):
    """api/books/{book_id}.json：仅为当日任一快照出现的书生成。"""
    out_dir = os.path.join(API_DIR, "books")
    count = 0
    for bid, entries in index["today_entries"].items():
        if not bid:
            continue
        best = min(entries, key=lambda e: e["rank"] or 9999)
        base = best["book"]
        hist = (history.get("books") or {}).get(str(bid), {})
        write_json(os.path.join(out_dir, f"{bid}.json"), {
            "book_id": bid,
            "title": base.get("title", ""),
            "cover": base.get("cover", ""),
            "author": base.get("author", ""),
            "intro": base.get("intro", ""),
            "minor": base.get("minor") or "",
            "status": base.get("status", ""),
            "word_count_text": base.get("word_count_text", ""),
            "latest": [{"board": e["slug"], "board_name": e["board_name"],
                        "rank": e["rank"], "heat": e["heat"]}
                       for e in sorted(entries, key=lambda x: x["rank"])],
            "history": hist.get("points", []),
        })
        count += 1
    return count


def build_global_aggregates(index: dict) -> dict:
    """全站聚合：status/boards/history/black-horses/authors/cross-board/books。返回 AI 阶段所需摘要。"""
    print("\n[全站聚合]")
    boards = enabled_boards()
    generated_at = index["now"].isoformat(timespec="seconds")
    date_iso = index["build_date_iso"]

    horses = build_black_horses(index)
    cross_books = build_cross_board(index)
    authors = build_authors(index)
    keywords_all = build_global_keywords(index)

    # ---- api/status.json ----
    board_status = []
    for board in boards:
        slug = board["slug"]
        snap = index["board_snapshots"].get(slug)
        board_status.append({
            "slug": slug,
            "latest_date": snap.get("date") if snap else None,
            "book_count": len(snap.get("books", [])) if snap else 0,
            "ok": bool(snap),
        })
    write_json(os.path.join(API_DIR, "status.json"), {
        "generated_at": generated_at,
        "last_scraped_at": index["last_scraped_at"],
        "total_books": len(index["boards_count"]),
        "new_today": len(index["new_ids"]),
        "horse_count": len(horses),
        "boards": board_status,
        "missing": index["missing"],
    })

    # ---- api/boards.json（Tab 索引）----
    boards_meta = []
    for board in boards:
        slug = board["slug"]
        snap = index["board_snapshots"].get(slug)
        meta = board_public_meta(board)
        cats = sorted({b.get("minor") or "未分类" for b in snap["books"]}) if snap else []
        meta.update({
            "latest_date": snap.get("date") if snap else None,
            "book_count": len(snap.get("books", [])) if snap else 0,
            "categories": cats,
        })
        boards_meta.append(meta)
    write_json(os.path.join(API_DIR, "boards.json"), {
        "generated_at": generated_at,
        "boards": boards_meta,
    })

    # ---- api/history.json + api/books/{id}.json ----
    history = build_history(index)
    write_json(os.path.join(API_DIR, "history.json"), history)
    book_pages = build_book_pages(index, history)

    # ---- api/black-horses.json ----
    write_json(os.path.join(API_DIR, "black-horses.json"), {
        "generated_at": generated_at, "date": date_iso, "horses": horses,
    })

    # ---- api/authors.json ----
    write_json(os.path.join(API_DIR, "authors.json"), {
        "generated_at": generated_at, "date": date_iso, "authors": authors,
    })

    # ---- api/cross-board.json ----
    write_json(os.path.join(API_DIR, "cross-board.json"), {
        "generated_at": generated_at, "date": date_iso, "books": cross_books,
    })

    print(f"  [完成] status/boards/history/black-horses({len(horses)})/"
          f"authors({len(authors)})/cross-board({len(cross_books)})"
          f"/books({book_pages})")

    return {
        "date": date_iso,
        "horses": horses,
        "cross_books": cross_books,
        "authors": authors,
        "keywords": keywords_all,
    }


# ============================================================
#  AI 分析（OpenAI 兼容，可选）
# ============================================================

def build_rule_overview(ctx: dict) -> str:
    """AI 不可用时的全站风向规则文案。"""
    lines = []
    horses = ctx.get("horses") or []
    if horses:
        top = "、".join(f"《{h['title']}》({h['board_name']} 第{h['rank']}名，"
                        f"热度+{h['heat_growth_pct']}%)"
                        for h in horses[:3])
        lines.append(f"- 黑马：{top} 领跑")
    cross = ctx.get("cross_books") or []
    if cross:
        top = "、".join(f"《{b['title']}》(同时上榜{b['boards_count']}榜)"
                        for b in cross[:3])
        lines.append(f"- 跨榜：{top}")
    authors = ctx.get("authors") or []
    if authors:
        top = "、".join(f"{a['author']}({a['books']}本)"
                        for a in authors[:3])
        lines.append(f"- 高产作者：{top}")
    kws = ctx.get("keywords") or []
    if kws:
        top = "、".join(f"{k['word']}({k['count']})" for k in kws[:8])
        lines.append(f"- 高频题材词：{top}")
    if not lines:
        return "暂无足够数据生成全站风向。"
    return "**全站风向（规则统计）**\n\n" + "\n".join(lines)


def build_batch_ai_prompt(payload: dict, batch: list) -> str:
    """分类速评批量 prompt（每批 AI_BATCH_SIZE 个小类），风格参考行业分析师快报。"""
    board_name = payload["board"]["name"]
    sections = []
    for cat in batch:
        books_lines = []
        for b in cat["books"][:8]:
            rc = b.get("rank_change") or 0
            mark = f"↑{rc}" if rc > 0 else (f"↓{abs(rc)}" if rc < 0 else "持平")
            new_mark = "，新上榜" if b.get("is_new") else ""
            books_lines.append(
                f"{b.get('rank', 0)}. 《{b.get('title', '')}》- {b.get('author', '')}"
                f"（热度{b.get('heat', 0)}，排名{mark}{new_mark}）\n"
                f"   简介：{(b.get('intro') or '无')[:120]}")
        trend = cat["trend"]
        new_text = "、".join(f"《{b['title']}》" for b in trend["new_books"]) or "无"
        dropped_text = "、".join(
            f"《{b['title']}》（{(b.get('intro') or '')[:50]}）"
            for b in trend["dropped_books"]) or "无"
        risers_text = "、".join(
            f"《{b['title']}》+{b['rank_change']}" for b in trend["top_risers"]) or "无"
        fallers_text = "、".join(
            f"《{b['title']}》{b['rank_change']}" for b in trend["top_fallers"]) or "无"
        heat_text = "、".join(
            f"《{b['title']}》{'+' if (b['heat_change'] or 0) >= 0 else ''}{b['heat_change']}"
            for b in trend["heat_leaders"]) or "无"

        sections.append(
            f"### 小类：{cat['name']}（{len(cat['books'])}本，总热度{cat['total_heat']}）\n\n"
            f"**当前在榜书籍：**\n" + "\n".join(books_lines) + "\n\n"
            f"**榜单变动：**\n"
            f"- 新上榜：{new_text}\n"
            f"- 掉出榜单：{dropped_text}\n"
            f"- 排名上升：{risers_text}\n"
            f"- 排名下降：{fallers_text}\n"
            f"- 热度变化居前：{heat_text}")

    names = [c["name"] for c in batch]
    output_tpl = "\n\n".join(
        f"===BEGIN: {n}===\n"
        f"**题材趋势** ...\n**读者偏好** ...\n**变动解读** ...\n**值得关注** ...\n"
        f"===END: {n}==="
        for n in names)

    return f"""你是一位网文行业分析师。请根据以下七猫小说「{board_name}」各小类的上榜数据与变动，为每个小类分别生成结构化速评。

{chr(10).join(sections)}

## 输出要求

请严格按照以下格式，为每个小类分别输出分析，包裹在对应标记中：

{output_tpl}

每个板块 1-2 句话，每个小类总字数 250 字以内。语言简洁专业，像行业快报；
只基于给定数据，不要编造未出现的书名或题材。必须为每个小类都输出完整分析，不可省略。"""


def parse_batch_response(text: str, names: list) -> dict:
    """解析 ===BEGIN: name=== ... ===END: name=== 标记包裹的批量响应。"""
    results = {}
    for name in names:
        m = re.search(
            rf"===BEGIN:\s*{re.escape(name)}\s*===(.*?)===END:\s*{re.escape(name)}\s*===",
            text or "", re.DOTALL)
        if m and m.group(1).strip():
            results[name] = m.group(1).strip()
    return results


def build_overview_prompt(ctx: dict) -> str:
    """全站风向 prompt：输入黑马/跨榜/热门作者/高频题材摘要，输出 Markdown 风向研判。"""
    horses = ctx.get("horses") or []
    horse_lines = [
        f"{i}. 《{h['title']}》{h['author']}（{h['board_name']}第{h['rank']}名，"
        f"升{h['rank_change']}位，热度+{h['heat_growth_pct']}%，{h['minor']}）"
        f"简介：{h['intro'][:60]}"
        for i, h in enumerate(horses[:10], 1)] or ["暂无"]
    cross = ctx.get("cross_books") or []
    cross_lines = [
        f"{i}. 《{b['title']}》{b['author']}（{b['boards_count']}榜在榜："
        f"{'、'.join(b['board_names'][:4])}，最佳第{b['best_rank']}名，{b['minor']}）"
        for i, b in enumerate(cross[:10], 1)] or ["暂无"]
    authors = ctx.get("authors") or []
    author_lines = [
        f"{i}. {a['author']}：{a['books']}本在榜/{a['boards']}个榜，"
        f"总热度{a['total_heat']}（《{'》、《'.join(a['titles'][:3])}》）"
        for i, a in enumerate(authors[:10], 1)] or ["暂无"]
    kws = ctx.get("keywords") or []
    kw_text = "、".join(f"{k['word']}({k['count']})" for k in kws[:15]) or "暂无"

    return f"""你是一位网文行业分析师。基于七猫全站 16 个榜单（大热/新书/完结/收藏/更新 × 日榜/月榜，男女频）的当日数据，输出全站风向研判。

## 黑马榜（排名与热度双升）
{chr(10).join(horse_lines)}

## 跨榜书（同时在多个榜单）
{chr(10).join(cross_lines)}

## 热门作者
{chr(10).join(author_lines)}

## 高频题材词
{kw_text}

## 输出要求（Markdown，400 字以内）

## 大盘风向
男女频整体冷热与结构特征。

## 题材风向
当前最活跃的题材方向与可能的原因。

## 黑马预警
重点提示黑马榜前 3 的题材与卖点，指出值得关注的信号。

只基于给定数据，不要编造书名与数字。"""


def _chat(client, model: str, prompt: str, max_tokens: int) -> str:
    """单次对话调用；任何异常向上抛出由调用方兜底。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("API 返回空内容")
    return content.strip()


def _apply_rule_overview(payloads: list, rule_text: str):
    """AI 整体不可用时：overview 填规则文案并重写各榜产物。"""
    for payload in payloads:
        payload["market"]["overview"] = {"text": rule_text, "ai_source": "rule"}
        write_board_outputs(payload)


def generate_ai(payloads: list, ctx: dict):
    """AI 阶段：逐榜缓存感知生成分类速评 + 全站风向（全局仅 1 次 API），失败/无 Key 回规则文案。

    缓存 data/{slug}/ai_cache/YYYY-MM-DD.json，键=日期+slug；只缓存 AI 成功产物（留档）。
    """
    print("\n[AI 分析]")
    base = os.environ.get("API_BASE_URL", "").strip()
    key = os.environ.get("API_KEY", "").strip()
    model = os.environ.get("API_MODEL", "").strip()

    if not (base and key and model):
        print("  [跳过] 未配置 API_BASE_URL/API_KEY/API_MODEL，全部使用规则文案")
        _apply_rule_overview(payloads, build_rule_overview(ctx))
        return

    client = None
    try:
        from openai import OpenAI  # 延迟导入：无 openai 库时降级规则文案
        client = OpenAI(base_url=base, api_key=key, timeout=60.0)
    except Exception as e:
        print(f"  [警告] openai 客户端初始化失败({e})，全部使用规则文案")
        _apply_rule_overview(payloads, build_rule_overview(ctx))
        return

    print(f"  AI 已启用：{model} @ {base}")
    overview = None  # 全站风向文本（全局仅生成一次；None=未生成，""=已尝试但失败）

    for payload in payloads:
        slug = payload["board"]["slug"]
        date_iso = payload["date"]
        first_day = payload["stats"]["first_day"]
        cache_path = os.path.join(DATA_DIR, slug, "ai_cache", f"{date_iso}.json")
        cache = read_json(cache_path) or {}
        cat_cache = dict(cache.get("categories") or {})
        if overview is None and cache.get("overview"):
            overview = cache["overview"]
            print(f"  [{slug}] 命中缓存全站风向")

        # ---- 分类速评（首日跳过：无对比数据）----
        if first_day:
            print(f"  [{slug}] 首日无对比数据，分类速评使用首日文案")
        else:
            pending = [c for c in payload["categories"] if c["name"] not in cat_cache]
            batches = [pending[i:i + AI_BATCH_SIZE]
                       for i in range(0, len(pending), AI_BATCH_SIZE)]
            for bi, batch in enumerate(batches):
                names = [c["name"] for c in batch]
                try:
                    text = _chat(client, model,
                                 build_batch_ai_prompt(payload, batch),
                                 max_tokens=800 * len(batch))
                    parsed = parse_batch_response(text, names)
                    for name, summary in parsed.items():
                        cat_cache[name] = summary
                    missed = [n for n in names if n not in parsed]
                    if missed:
                        print(f"  [{slug}] 第{bi+1}批未解析出: {'、'.join(missed)}（用规则文案）")
                except Exception as e:
                    print(f"  [警告] {slug} 第{bi+1}批速评失败({e})，该批用规则文案")
            if not batches:
                print(f"  [{slug}] 分类速评全部命中缓存（{len(cat_cache)} 条）")

        # ---- 全站风向（全局仅 1 次 API 调用）----
        if overview is None:
            try:
                overview = _chat(client, model, build_overview_prompt(ctx),
                                 max_tokens=1200)
                print("  [全站风向] AI 已生成")
            except Exception as e:
                print(f"  [警告] 全站风向生成失败({e})，使用规则文案")
                overview = ""

        ov_text = overview if overview else build_rule_overview(ctx)
        ov_source = "ai" if overview else "rule"

        # ---- 应用到 payload 并重写榜单产物 ----
        payload["market"]["overview"] = {"text": ov_text, "ai_source": ov_source}
        for cat in payload["categories"]:
            name = cat["name"]
            if not first_day and name in cat_cache:
                cat["trend"]["summary"] = cat_cache[name]
                cat["trend"]["ai_source"] = "ai"
        payload["market"]["categories"] = [
            {"name": c["name"], "summary": c["trend"]["summary"],
             "ai_source": c["trend"]["ai_source"]}
            for c in payload["categories"]]
        write_board_outputs(payload)

        # ---- 写缓存（只留 AI 成功产物；首日无分类速评则仅存风向）----
        cache_out = {"date": date_iso, "slug": slug,
                     "model": model,
                     "overview": overview if overview else None,
                     "categories": cat_cache or None}
        if cache_out["overview"] or cache_out["categories"]:
            write_json(cache_path, cache_out)

        ai_count = sum(1 for c in payload["categories"]
                       if c["trend"]["ai_source"] == "ai")
        print(f"  [完成] {slug}：AI 分类速评 {ai_count} 条，"
              f"overview={'ai' if ov_source == 'ai' else 'rule'}")


# ============================================================
#  主入口
# ============================================================

def select_build_boards(boards: list) -> list:
    """取启用榜单，并用 QM_BUILD_BOARDS 环境变量限定构建子集（全站索引/聚合仍按全部榜）。"""
    wanted = os.environ.get("QM_BUILD_BOARDS", "").strip()
    if not wanted:
        return boards
    slugs = {s.strip() for s in wanted.split(",") if s.strip()}
    subset = [b for b in boards if b["slug"] in slugs]
    unknown = slugs - {b["slug"] for b in subset}
    if unknown:
        print(f"[警告] QM_BUILD_BOARDS 中的未知 slug 被忽略: {', '.join(sorted(unknown))}")
    return subset


def main():
    started = datetime.now()
    print("=" * 60)
    print("七猫榜单数据构建（build_latest.py）")
    print("=" * 60)

    boards = enabled_boards()
    build_boards = select_build_boards(boards)
    if not build_boards:
        print("[错误] 没有可构建的榜单（检查 QM_BUILD_BOARDS 或 boards_config.py）")
        return

    # 1. 全站索引（始终覆盖全部启用榜单，保证 boards_count 等全局字段正确）
    index = build_global_index(boards)
    if not index:
        return
    if index["missing"]:
        for msg in index["missing"]:
            print(f"  [缺档] {msg}")

    # 2. 逐榜构建（summary 为规则/首日文案，AI 步骤再填充）
    print(f"\n[逐榜构建] {len(build_boards)} 个榜单")
    payloads = []
    for board in build_boards:
        if board["slug"] not in index["board_snapshots"]:
            print(f"  [跳过] {board['name']}({board['slug']}) 当日快照缺失")
            continue
        try:
            payloads.append(build_board_trends(board, index))
        except Exception as e:
            print(f"  [错误] {board['slug']} 构建失败: {e}")

    # 3. 分类层
    build_categories_layer(index)

    # 4. 全站聚合
    ctx = build_global_aggregates(index)

    # 5. AI（逐榜缓存感知；未配置/失败回规则文案）
    generate_ai(payloads, ctx)

    # 6. 汇总
    elapsed = (datetime.now() - started).total_seconds()
    print("\n" + "=" * 60)
    print("构建汇总")
    print("=" * 60)
    print(f"  构建日: {index['build_date_iso']}")
    print(f"  榜单构建: {len(payloads)}/{len(build_boards)} 个（缺档 {len(index['missing'])}）")
    print(f"  全站去重书数: {len(index['boards_count'])}，新上榜: {len(index['new_ids'])}")
    print(f"  黑马: {len(ctx['horses'])} 本，跨榜书: {len(ctx['cross_books'])} 本")
    print(f"  AI: {'启用 ' + os.environ.get('API_MODEL', '') if all(os.environ.get(v) for v in ('API_BASE_URL', 'API_KEY', 'API_MODEL')) else '未配置（规则文案）'}")
    print(f"  数据目录: {DATA_DIR}")
    print(f"  API 目录: {API_DIR}")
    print(f"  耗时: {elapsed:.1f}s")
    print("构建结束。")


if __name__ == "__main__":
    main()
