# -*- coding: utf-8 -*-
"""
七猫榜单配置 —— 多榜单流水线的单一事实源。

七猫排行榜页 URL 模式（2026-08-18 探针实测）：
    https://www.qimao.com/paihang/{sex}/{type}/{period}/
      - sex:    boy(男生) / girl(女生)
      - type:   hot(大热) / new(新书) / over(完结) / collect(收藏) / update(更新)
      - period: date(日榜) / month(月榜)

实测有效榜单共 16 个（每榜固定 Top 20，无分页，服务端渲染静态 HTML，
requests + 桌面 UA 即可，无反爬无字体混淆）：
  - boy/girl × {hot,new,over,collect,update} × date  → 10 个
  - boy/girl × {hot,new,over} × month               → 6 个

爬虫与后续构建脚本都从这里读取榜单列表、题材关键词(keywords)与赛道分组(genre_groups)。
赛道分组按「大类(major)」聚合：爬虫会从榜单页书籍的分类链接中提取大类/小类名称与 id，
构建脚本将小类归到大类、再归到赛道；majors 为空的组表示兜底收拢其余全部大类。
"""

# ============================================================
#  基础枚举：URL 片段 → 中文名
# ============================================================

# 榜单类型
RANK_TYPES = {
    "hot": "大热榜",
    "new": "新书榜",
    "over": "完结榜",
    "collect": "收藏榜",
    "update": "更新榜",
}

# 频道：URL 片段 → (中文频道名, 通用频道名 male/female)
SEXES = {
    "boy": ("男生", "male"),
    "girl": ("女生", "female"),
}

# 周期
PERIODS = {
    "date": "日榜",
    "month": "月榜",
}

# 探针实测：仅 hot/new/over 三个类型存在月榜；
# collect/update 的月榜 URL 返回 HTTP 200 但 rank-list-item 数为 0，故不生成该组合。
MONTH_AVAILABLE = {"hot", "new", "over"}

# 榜单页 URL 前缀
BASE_URL = "https://www.qimao.com/paihang"


# ============================================================
#  题材关键词：按频道区分（命中书籍简介/分类即加权，构建脚本使用）
# ============================================================

# 女频常见题材（结合七猫女频分类：总裁豪门/现代言情/古代言情/幻想言情/年代文等）
FEMALE_KEYWORDS = [
    "总裁豪门", "总裁", "豪门", "现代言情", "婚恋", "先婚后爱", "追妻", "和离", "替嫁",
    "古代言情", "宫斗", "宅斗", "幻想言情", "年代文", "年代", "七零", "八零", "军婚",
    "萌宝", "团宠", "幼崽", "穿书", "快穿", "系统", "重生", "穿越", "甜宠", "甜文",
    "虐恋", "追妻火葬场", "复仇", "马甲", "大佬", "双强", "玄学", "直播", "综艺",
    "娱乐圈", "种田", "经商", "美食", "真假千金", "对照组", "断亲", "洗白", "女配",
    "炮灰", "反派", "权臣", "空间", "囤货", "末世", "星际", "修仙", "无限流", "悬疑",
    "民国", "校园", "暗恋", "青梅竹马", "兽世", "无CP",
]

# 男频常见题材（结合七猫男频分类：都市高武/战神/赘婿/神医/鉴宝等）
MALE_KEYWORDS = [
    "都市高武", "战神", "兵王", "赘婿", "神医", "鉴宝", "系统", "重生", "穿越", "无敌",
    "签到", "苟道", "扮猪吃虎", "杀伐果断", "玄幻", "东方玄幻", "仙侠", "修仙", "炼丹",
    "宗门", "废柴逆袭", "洪荒", "诸天", "万界", "无限流", "历史", "争霸", "三国", "大明",
    "大唐", "抗战", "谍战", "特种兵", "科幻", "末世", "丧尸", "废土", "星际", "机甲",
    "黑科技", "工业", "国运", "游戏", "网游", "电竞", "直播", "灵异", "规则怪谈",
    "克苏鲁", "悬疑", "盗墓", "体育", "年代", "四合院",
]

# 通用关键词（跨频道兜底用，男女题材合并去重）
GENERAL_KEYWORDS = list(dict.fromkeys(FEMALE_KEYWORDS + MALE_KEYWORDS))


# ============================================================
#  赛道分组：按「大类(major)」聚合（majors 含常见别名，未命中落入空 majors 的兜底组）
# ============================================================

FEMALE_GENRE_GROUPS = [
    {"name": "现代言情", "majors": ["现代言情", "都市言情", "豪门总裁"]},
    {"name": "古代言情", "majors": ["古代言情", "古风世情"]},
    {"name": "幻想言情", "majors": ["幻想言情", "玄幻言情"]},
    # 空 majors = 兜底收拢其余全部大类（如悬疑/衍生等同人分类）
    {"name": "其他", "majors": []},
]

MALE_GENRE_GROUPS = [
    # 都市类：都市高武/战神/赘婿/神医/鉴宝等小类均挂在「都市」大类下
    {"name": "都市", "majors": ["都市", "都市生活"]},
    # 玄幻奇幻：仙侠/武侠大类统一并入本赛道
    {"name": "玄幻奇幻", "majors": ["玄幻", "奇幻", "玄幻奇幻", "仙侠", "仙侠武侠", "武侠"]},
    {"name": "历史", "majors": ["历史", "历史古代"]},
    {"name": "军事", "majors": ["军事", "抗战谍战"]},
    {"name": "科幻", "majors": ["科幻", "科幻末世"]},
    {"name": "游戏竞技", "majors": ["游戏", "竞技", "游戏竞技", "体育"]},
    {"name": "明星娱乐", "majors": ["明星", "娱乐", "明星娱乐", "衍生", "衍生同人"]},
    # 空 majors = 兜底收拢其余全部大类
    {"name": "其他", "majors": []},
]


# ============================================================
#  榜单注册表：程序化生成 16 个（collect/update 无月榜，不生成 month 组合）
# ============================================================
#  字段：
#    slug        —— 英文短名（如 boy-hot-date），决定数据目录 data/<slug>/
#    name        —— 中文榜单名（如「男生大热日榜」）
#    channel     —— male / female（通用频道名）
#    sex         —— boy / girl（URL 片段）
#    type        —— hot/new/over/collect/update（URL 片段）
#    type_name   —— 中文类型名（如「大热榜」）
#    period      —— date/month（URL 片段）
#    period_name —— 中文周期名（如「日榜」）
#    url         —— 榜单页绝对 URL
#    enabled     —— 是否参与抓取/构建
#    genre_groups / keywords —— 按频道复用的赛道分组与题材词表（构建脚本使用）

def _build_boards():
    """程序化生成全部榜单：sex × type × period，跳过无月榜的组合。"""
    boards = []
    for sex_key, (sex_cn, channel) in SEXES.items():
        for type_key, type_name in RANK_TYPES.items():
            for period_key, period_name in PERIODS.items():
                # 探针实测 collect/update 无月榜（页面 200 但列表为 0），直接不生成
                if period_key == "month" and type_key not in MONTH_AVAILABLE:
                    continue
                is_female = channel == "female"
                boards.append({
                    "slug": f"{sex_key}-{type_key}-{period_key}",
                    # 类型名去掉末尾「榜」字再拼周期，如 男生+大热+日榜
                    "name": f"{sex_cn}{type_name[:-1]}{period_name}",
                    "channel": channel,
                    "sex": sex_key,
                    "type": type_key,
                    "type_name": type_name,
                    "period": period_key,
                    "period_name": period_name,
                    "url": f"{BASE_URL}/{sex_key}/{type_key}/{period_key}/",
                    "enabled": True,
                    "genre_groups": FEMALE_GENRE_GROUPS if is_female else MALE_GENRE_GROUPS,
                    "keywords": FEMALE_KEYWORDS if is_female else MALE_KEYWORDS,
                })
    return boards


BOARDS = _build_boards()


def enabled_boards():
    """返回已启用且配置了 url 的榜单（默认 16 个）。"""
    return [b for b in BOARDS if b.get("enabled") and b.get("url")]


def get_board(slug: str):
    """按 slug 查找榜单，找不到返回 None。"""
    for b in BOARDS:
        if b["slug"] == slug:
            return b
    return None


def board_public_meta(board: dict) -> dict:
    """供前端 api/boards.json 使用的精简元信息。"""
    return {
        "slug": board["slug"],
        "name": board["name"],
        "channel": board["channel"],
        "type": board["type"],
        "type_name": board["type_name"],
        "period": board["period"],
        "period_name": board["period_name"],
    }
