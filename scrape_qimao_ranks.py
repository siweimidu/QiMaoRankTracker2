# -*- coding: utf-8 -*-
"""
七猫小说排行榜爬虫 —— 两层抓取（requests + BeautifulSoup，目标站为静态 HTML，无需浏览器）。

L1 榜单层：遍历 boards_config 中启用的榜单（可用环境变量 QM_BOARDS 限定子集），
    解析每榜 Top 20 全字段，快照写入 data/{slug}/snapshots/ranks_YYYYMMDD.json。

L2 分类层（默认开启，QM_SKIP_CATEGORIES=1 跳过）：从 L1 全部当日快照收集书籍的
    小类链接（click-1 形式）全站去重得到分类清单，每分类抓 click-1/click-2 两页
    （每页 15 本），按 book_id 合并去重取前 QM_CAT_LIMIT(默认 30) 本，快照写入
    data/categories/snapshots/ranks_YYYYMMDD.json（分类书卡无热度，不存 heat）。

环境变量：
    QM_BOARDS          逗号分隔的 slug 子集，只抓这些榜单
    QM_SKIP_CATEGORIES 设为 1 跳过分类层
    QM_CAT_LIMIT       每分类最多保留本数（默认 30）
    QM_LIMIT           每榜最多取前 N 本（默认 0 = 不限，即 20）
    QM_DELAY_MIN/MAX   请求间随机延时上下限（秒，默认 2.0 / 4.0）

断点续跑：data/{slug}/task_state_YYYYMMDD.json 与 data/categories/task_state_YYYYMMDD.json
记录当日已完成项，重跑时跳过已完成且快照存在的；全部完成后删除 task_state 文件保持仓库干净。
"""

import os
import json
import re
import time
import random
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from boards_config import enabled_boards

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# ---- 请求头：桌面 UA + 中文优先，规避基础风控 ----
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ---- 环境变量配置 ----
DELAY_MIN = float(os.environ.get("QM_DELAY_MIN", "2.0"))
DELAY_MAX = float(os.environ.get("QM_DELAY_MAX", "4.0"))
if DELAY_MAX < DELAY_MIN:
    DELAY_MAX = DELAY_MIN
CAT_LIMIT = int(os.environ.get("QM_CAT_LIMIT", "30"))          # 每分类保留本数
BOARD_LIMIT = int(os.environ.get("QM_LIMIT", "0"))             # 每榜截取本数，0=不限
SKIP_CATEGORIES = os.environ.get("QM_SKIP_CATEGORIES", "") == "1"

# ---- 数字单位换算：字数/热度通用 ----
_UNIT_MULT = {"万": 10000, "亿": 100000000}


# ============================================================
#  网络层
# ============================================================

def make_session():
    """创建带桌面 UA 的会话。"""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch_html(session, url: str):
    """带指数退避重试的 GET：初次请求 + 最多 3 次重试（间隔 1s/2s/4s + 随机抖动）。

    成功返回 HTML 文本，最终失败返回 None。
    """
    for attempt in range(4):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            resp.encoding = "utf-8"  # 七猫全站 UTF-8
            return resp.text
        except Exception as e:
            if attempt >= 3:
                print(f"    [网络] 请求最终失败: {url} -> {e}")
                return None
            wait = (2 ** attempt) + random.uniform(0, 0.8)
            print(f"    [网络] 第 {attempt + 1} 次失败({e})，{wait:.1f}s 后重试")
            time.sleep(wait)
    return None


def polite_delay():
    """请求之间的随机礼貌延时，降低对目标站的压力。"""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# ============================================================
#  解析辅助
# ============================================================

def extract_book_id(url: str) -> int:
    """从书籍链接 https://www.qimao.com/shuku/{id}/ 提取数字 id。"""
    m = re.search(r"/shuku/(\d+)/", url or "")
    return int(m.group(1)) if m else 0


def extract_cat_ids(url: str):
    """从分类链接 /shuku/a-{major_id}-{minor_id}-... 提取 (major_id, minor_id)。"""
    m = re.search(r"/shuku/a-(\d+)-(\d+)-", url or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def parse_number_with_unit(text: str) -> int:
    """解析带单位的数值文本：'894.38万字'->8943800，'136.2万'->1362000，'1.2亿'->120000000。"""
    if not text:
        return 0
    for unit, mult in _UNIT_MULT.items():
        m = re.search(rf"([\d.]+)\s*{unit}", text)
        if m:
            return int(round(float(m.group(1)) * mult))
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else 0


def _is_separator_em(em) -> bool:
    """判断 .s-book-info 内的 em 是否为分隔符（class 含 line/point）。"""
    classes = " ".join(em.get("class") or [])
    return "line" in classes or "point" in classes


def category_page_url(url: str, page: int) -> str:
    """将分类 URL 末尾 click-N 改为目标页码（探针实测 click-{页码} 翻页有效）。"""
    if page <= 1:
        return url
    return re.sub(r"click-\d+/$", f"click-{page}/", url)


# ============================================================
#  L1：榜单页解析（ul.rank-list > li.rank-list-item，每榜固定 Top 20）
# ============================================================

def parse_board(html: str) -> list:
    """解析榜单页 HTML，返回书籍列表（全字段）。"""
    soup = BeautifulSoup(html, "lxml")
    books = []
    for idx, item in enumerate(soup.select("ul.rank-list > li.rank-list-item"), start=1):
        # 书名 + 书籍链接（缺失即视为脏数据，跳过）
        title_el = item.select_one("a.s-book-title")
        if not title_el:
            continue
        book_url = title_el.get("href", "")
        title = title_el.get_text(strip=True)

        # 排名：.rank-number 文本（前三名类含 first/second/third，文本仍是数字），兜底用序号
        rank_el = item.select_one(".rank-number")
        try:
            rank = int(rank_el.get_text(strip=True)) if rank_el else idx
        except ValueError:
            rank = idx

        # 封面
        img_el = item.select_one(".pic img")
        cover = img_el.get("src", "") if img_el else ""

        # 作者 + 链接
        author_el = item.select_one(".s-book-info a[href*='/zuozhe/']")
        author = author_el.get_text(strip=True) if author_el else ""
        author_url = author_el.get("href", "") if author_el else ""

        # 分类：.s-book-info 直接子级 a 的倒数第 2 个=大类，最后 1 个=小类
        # （这些 a 的 href 形如 /shuku/a-{major_id}-{minor_id}-a-a-a-a-click-1/；
        #   防御性排除作者链接，避免其混入分类列表导致错位）
        info_links = [a for a in item.select(".s-book-info > a")
                      if "/zuozhe/" not in (a.get("href") or "")]
        major = info_links[-2].get_text(strip=True) if len(info_links) >= 2 else ""
        minor_el = info_links[-1] if info_links else None
        minor = minor_el.get_text(strip=True) if minor_el else ""
        minor_url = minor_el.get("href", "") if minor_el else ""
        major_id, minor_id = extract_cat_ids(minor_url)

        # 状态 / 字数：.s-book-info 直接子级 em（排除分隔符 line/point），第 1 个=状态，最后 1 个=字数
        ems = [em for em in item.select(".s-book-info > em") if not _is_separator_em(em)]
        status = ems[0].get_text(strip=True) if ems else ""
        word_count_text = ems[-1].get_text(strip=True) if ems else ""
        word_count = parse_number_with_unit(word_count_text)

        # 简介
        intro_el = item.select_one(".s-book-intro")
        intro = intro_el.get_text(strip=True) if intro_el else ""

        # 最新章节（去掉「最近更新」前缀）/ 更新时间
        update_el = item.select_one(".s-book-update")
        latest_chapter, updated_at = "", ""
        if update_el:
            chapter_el = update_el.select_one("a")
            if chapter_el:
                latest_chapter = re.sub(r"^最近更新[:：]?", "", chapter_el.get_text(strip=True))
            time_el = update_el.select_one("em")
            updated_at = time_el.get_text(strip=True) if time_el else ""

        # 热度：数值 × 单位（万/亿），存整数
        num_el = item.select_one(".rank-change-num .rank-num")
        unit_el = item.select_one(".rank-change-num .rank-unit")
        heat = 0
        if num_el:
            try:
                unit = unit_el.get_text(strip=True) if unit_el else ""
                num_text = num_el.get_text(strip=True).replace(",", "")
                heat = int(round(float(num_text) * _UNIT_MULT.get(unit, 1)))
            except ValueError:
                heat = 0

        # 平台涨跌图标：iconfont class 含 rise/drop
        platform_trend = ""
        icon_el = item.select_one(".rank-change-num i.iconfont")
        if icon_el:
            icon_classes = " ".join(icon_el.get("class") or [])
            if "rise" in icon_classes:
                platform_trend = "rise"
            elif "drop" in icon_classes:
                platform_trend = "drop"

        # 徽章（如「蝉联榜首」，可能为空）
        badge_el = item.select_one(".rank-tag")
        badge = badge_el.get_text(strip=True) if badge_el else ""

        books.append({
            "rank": rank,
            "title": title,
            "book_id": extract_book_id(book_url),
            "url": book_url,
            "author": author,
            "author_url": author_url,
            "major": major,
            "minor": minor,
            "major_id": major_id,
            "minor_id": minor_id,
            "minor_url": minor_url,
            "status": status,
            "word_count_text": word_count_text,
            "word_count": word_count,
            "heat": heat,
            "platform_trend": platform_trend,
            "badge": badge,
            "intro": intro,
            "cover": cover,
            "latest_chapter": latest_chapter,
            "updated_at": updated_at,
        })
    return books


# ============================================================
#  L2：分类页解析（li.qm-cover-text-item，每页 15 本，无热度字段）
# ============================================================

def parse_category_page(html: str) -> list:
    """解析书库分类页 HTML，返回书籍列表。"""
    soup = BeautifulSoup(html, "lxml")
    books = []
    for item in soup.select("li.qm-cover-text-item"):
        title_el = item.select_one(".s-tit a")
        if not title_el:
            continue
        book_url = title_el.get("href", "")

        # 作者：.s-author 本身可能是链接，也可能是包裹链接的容器
        author_el = item.select_one(".s-author")
        if author_el is not None and author_el.name != "a":
            author_el = author_el.select_one("a[href*='/zuozhe/']") or author_el.select_one("a")
        author = author_el.get_text(strip=True) if author_el else ""
        author_url = author_el.get("href", "") if author_el else ""

        minor_el = item.select_one(".s-category")
        status_el = item.select_one(".s-status")
        words_el = item.select_one(".s-words-num")
        intro_el = item.select_one(".s-desc")
        cover_el = item.select_one("img.book-cover-src")
        update_el = item.select_one(".s-update-time")

        # 更新日期形如「2026-08-17更新」，只保留日期部分
        updated_date = ""
        if update_el:
            m = re.search(r"\d{4}-\d{2}-\d{2}", update_el.get_text(strip=True))
            updated_date = m.group(0) if m else update_el.get_text(strip=True)

        word_count_text = words_el.get_text(strip=True) if words_el else ""

        books.append({
            "title": title_el.get_text(strip=True),
            "book_id": extract_book_id(book_url),
            "url": book_url,
            "author": author,
            "author_url": author_url,
            "minor": minor_el.get_text(strip=True) if minor_el else "",
            "status": status_el.get_text(strip=True) if status_el else "",
            "word_count_text": word_count_text,
            "word_count": parse_number_with_unit(word_count_text),
            "intro": intro_el.get_text(strip=True) if intro_el else "",
            "cover": cover_el.get("src", "") if cover_el else "",
            "updated_date": updated_date,
        })
    return books


# ============================================================
#  通用 IO 辅助
# ============================================================

def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [警告] 读取 JSON 失败({path}): {e}")
        return {}


def _load_completed(state_file: str) -> list:
    """读取续跑状态中的已完成项列表。"""
    return _load_json(state_file).get("completed", [])


def _board_snapshot_file(slug: str, date_str: str) -> str:
    return os.path.join(DATA_DIR, slug, "snapshots", f"ranks_{date_str}.json")


def _board_state_file(slug: str, date_str: str) -> str:
    return os.path.join(DATA_DIR, slug, f"task_state_{date_str}.json")


# ============================================================
#  L1：榜单层抓取
# ============================================================

def scrape_board_layer(boards: list, date_str: str) -> dict:
    """抓取全部目标榜单，返回统计 {ok, fail, detail: [(slug, 本数)]}。"""
    session = make_session()
    stats = {"ok": 0, "fail": 0, "detail": []}

    for board in boards:
        slug = board["slug"]
        snapshot_file = _board_snapshot_file(slug, date_str)
        state_file = _board_state_file(slug, date_str)
        os.makedirs(os.path.dirname(snapshot_file), exist_ok=True)

        # 断点续跑：已完成且快照存在则跳过
        if slug in _load_completed(state_file) and os.path.exists(snapshot_file):
            book_count = len(_load_json(snapshot_file).get("books", []))
            print(f"  [跳过] {board['name']}({slug}) 当日已完成（快照 {book_count} 本）")
            stats["ok"] += 1
            stats["detail"].append((slug, book_count))
            continue

        print(f"[榜单] {board['name']}({slug}) -> {board['url']}")
        try:
            html = fetch_html(session, board["url"])
            if not html:
                raise RuntimeError("抓取失败（重试耗尽）")
            books = parse_board(html)
            if not books:
                raise RuntimeError("解析到 0 本书，页面结构可能变化")
            if BOARD_LIMIT > 0:
                books = books[:BOARD_LIMIT]
            snapshot = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "board": slug,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
                "source_url": board["url"],
                "books": books,
            }
            _write_json(snapshot_file, snapshot)
            _write_json(state_file, {"completed": [slug]})
            print(f"  [完成] 抓取 {len(books)} 本 -> {snapshot_file}")
            stats["ok"] += 1
            stats["detail"].append((slug, len(books)))
        except Exception as e:
            print(f"  [失败] {board['name']}({slug}): {e}")
            stats["fail"] += 1
            stats["detail"].append((slug, 0))
        polite_delay()

    # 全部目标榜单均有快照 → 删除当日 task_state，保持仓库干净
    if all(os.path.exists(_board_snapshot_file(b["slug"], date_str)) for b in boards):
        for board in boards:
            state_file = _board_state_file(board["slug"], date_str)
            if os.path.exists(state_file):
                os.remove(state_file)
        print("[榜单层] 全部完成，已清理当日 task_state 文件")

    return stats


# ============================================================
#  L2：分类层抓取
# ============================================================

def collect_categories(boards: list, date_str: str) -> list:
    """从 L1 全部当日快照收集小类分类清单（按 key 全站去重）。

    返回 [{name(小类), major(大类), key("{major_id}-{minor_id}"), url(click-1 形式)}]
    """
    cats = {}
    for board in boards:
        snapshot = _load_json(_board_snapshot_file(board["slug"], date_str))
        if not snapshot:
            print(f"  [警告] {board['slug']} 无当日快照，分类发现跳过该榜")
            continue
        for book in snapshot.get("books", []):
            url = book.get("minor_url") or ""
            major_id, minor_id = book.get("major_id") or 0, book.get("minor_id") or 0
            if not (major_id and minor_id):
                major_id, minor_id = extract_cat_ids(url)
            if not url or not (major_id and minor_id):
                continue
            key = f"{major_id}-{minor_id}"
            if key not in cats:
                cats[key] = {
                    "name": book.get("minor", ""),
                    "major": book.get("major", ""),
                    "key": key,
                    "url": url,
                }
    return list(cats.values())


def scrape_category_layer(boards: list, date_str: str) -> dict:
    """抓取分类层：每分类 2 页合并去重取前 QM_CAT_LIMIT 本，返回统计 {ok, fail, total}。"""
    session = make_session()
    categories = collect_categories(boards, date_str)
    if not categories:
        print("[分类层] 未发现任何分类（L1 快照缺失或无分类链接），跳过")
        return {"ok": 0, "fail": 0, "total": 0}

    print(f"[分类层] 从 L1 快照发现 {len(categories)} 个小类分类，逐个抓取 Top {CAT_LIMIT}")
    snap_dir = os.path.join(DATA_DIR, "categories", "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    snapshot_file = os.path.join(snap_dir, f"ranks_{date_str}.json")
    state_file = os.path.join(DATA_DIR, "categories", f"task_state_{date_str}.json")

    # 断点续跑：恢复已完成分类的存量数据
    completed = _load_completed(state_file)
    done_cats = []
    if completed:
        for cat in _load_json(snapshot_file).get("categories", []):
            if cat.get("key") in completed:
                done_cats.append(cat)
        if done_cats:
            print(f"[分类层] 续跑：已有 {len(done_cats)} 个分类完成，继续抓取剩余部分")

    stats = {"ok": len(done_cats), "fail": 0, "total": len(categories)}
    for cat in categories:
        key = cat["key"]
        if key in completed:
            continue
        print(f"  [分类] {cat['major']}/{cat['name']}({key})")
        try:
            # 抓 click-1/click-2 两页，按 book_id 合并去重
            # 第 1 页失败才算整个分类失败；第 2 页失败（小分类可能不足 15 本）降级用第 1 页
            merged, seen_ids = [], set()
            for page in (1, 2):
                page_url = category_page_url(cat["url"], page)
                html = fetch_html(session, page_url)
                if not html:
                    if page == 1:
                        raise RuntimeError("第 1 页抓取失败（重试耗尽）")
                    print(f"    [降级] 第 2 页抓取失败，仅用第 1 页数据")
                    break
                for book in parse_category_page(html):
                    if book["book_id"] and book["book_id"] not in seen_ids:
                        seen_ids.add(book["book_id"])
                        merged.append(book)
                polite_delay()
            merged = merged[:CAT_LIMIT]
            # 分类书卡无排名字段，按合并顺序补编号并置于首位
            merged = [{"rank": i, **book} for i, book in enumerate(merged, start=1)]

            done_cats.append({
                "name": cat["name"],
                "major": cat["major"],
                "key": key,
                "url": cat["url"],
                "books": merged,
            })
            completed.append(key)
            _write_json(snapshot_file, {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
                "categories": done_cats,
            })
            _write_json(state_file, {"completed": completed})
            stats["ok"] += 1
            print(f"    [完成] {len(merged)} 本")
        except Exception as e:
            stats["fail"] += 1
            print(f"    [失败] {cat['name']}({key}): {e}")

    # 全部分类完成 → 删除续跑状态文件
    if set(completed) >= {c["key"] for c in categories} and not stats["fail"]:
        if os.path.exists(state_file):
            os.remove(state_file)
            print("[分类层] 全部完成，已清理当日 task_state 文件")

    print(f"[分类层] 结束：成功 {stats['ok']} / 失败 {stats['fail']} / 共 {stats['total']}")
    return stats


# ============================================================
#  入口
# ============================================================

def select_boards() -> list:
    """取启用榜单，并用 QM_BOARDS 环境变量限定子集。"""
    boards = enabled_boards()
    wanted = os.environ.get("QM_BOARDS", "").strip()
    if wanted:
        slugs = {s.strip() for s in wanted.split(",") if s.strip()}
        boards = [b for b in boards if b["slug"] in slugs]
        unknown = slugs - {b["slug"] for b in boards}
        if unknown:
            print(f"[警告] QM_BOARDS 中的未知 slug 被忽略: {', '.join(sorted(unknown))}")
    return boards


def main():
    date_str = datetime.now().strftime("%Y%m%d")
    boards = select_boards()
    if not boards:
        print("[错误] 没有可抓取的榜单（检查 QM_BOARDS 或 boards_config.py）")
        return

    print(f"七猫榜单爬虫启动：{len(boards)} 个榜单，日期 {date_str}，"
          f"延时 {DELAY_MIN}~{DELAY_MAX}s")
    board_stats = scrape_board_layer(boards, date_str)

    if SKIP_CATEGORIES:
        print("[分类层] QM_SKIP_CATEGORIES=1，跳过")
    else:
        scrape_category_layer(boards, date_str)

    # ---- 汇总 ----
    print("\n" + "=" * 50)
    print("抓取汇总")
    print("=" * 50)
    for slug, count in board_stats["detail"]:
        print(f"  {slug}: {count} 本")
    print(f"榜单层：成功 {board_stats['ok']} / 失败 {board_stats['fail']}")
    print("全部流程结束。")


if __name__ == "__main__":
    main()
