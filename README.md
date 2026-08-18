# 📚 七猫风向标（QiMaoRankTracker2）

> 每日自动抓取七猫小说中文网 **16 个排行榜**，两层采集（榜单层 + 分类层），生成趋势对比、黑马雷达、作者榜、跨榜统计与 AI 市场风向分析，通过 GitHub Actions 全自动运行并部署到 GitHub Pages 的零后端排行榜追踪看板。

[![每日抓取](https://github.com/siweimidu/QiMaoRankTracker2/actions/workflows/scrape.yml/badge.svg)](https://github.com/siweimidu/QiMaoRankTracker2/actions/workflows/scrape.yml)
[![Pages 部署](https://github.com/siweimidu/QiMaoRankTracker2/actions/workflows/pages.yml/badge.svg)](https://github.com/siweimidu/QiMaoRankTracker2/actions/workflows/pages.yml)
[![在线看板](https://img.shields.io/badge/在线看板-GitHub_Pages-0065fd?logo=githubpages&logoColor=white)](https://siweimidu.github.io/QiMaoRankTracker2/)

---

## 目录

- [功能特性](#功能特性)
- [在线看板 / 快速开始](#在线看板--快速开始)
- [每日自动运行说明](#每日自动运行说明)
- [AI 配置详解](#ai-配置详解)
- [API 文档](#api-文档)
- [本地开发](#本地开发)
- [环境变量参考](#环境变量参考)
- [项目结构](#项目结构)
- [与 FanqieRankTracker 的对比](#与-fanqieranktracker-的对比)
- [FAQ](#faq)
- [免责声明](#免责声明)

## 功能特性

- **16 个榜单全覆盖**：男生/女生频道 × 大热/新书/完结/收藏/更新 五类日榜 + 大热/新书/完结 三类月榜（收藏/更新无月榜，系实测该组合无数据），每榜 Top 20 全字段。
- **分类层自动发现**：第二层抓取从当日全部上榜书籍的分类链接中自动发现 30+ 个小类（东方玄幻、都市高武、总裁豪门、年代重生……随榜单内容动态增减），每类抓取两页合并去重。
- **每日快照**：原始数据落盘 `data/{slug}/snapshots/ranks_YYYYMMDD.json`，永不丢失，支持任意历史回溯。
- **趋势对比**：每本书的排名变化（↑↓新）、热度增量、新上榜标记；首日收录自动给安全默认值。
- **黑马评分**：黑马分 = 60% × 排名跃升（封顶 20 名）+ 40% × 热度增长率（封顶 100%），全站去重取 TOP20。
- **跨榜统计**：同时在榜 ≥2 个榜单的「跨榜常青树」，含在榜数、最佳名次、总热度。
- **作者榜**：热门作者 TOP30（在榜作品数、覆盖榜数、总热度、代表作）。
- **AI 市场分析**：对接任意 OpenAI 兼容 API（OpenAI / DeepSeek / Kimi / 智谱 / 火山方舟等），生成全站风向研判与逐榜分类速评；结果按日缓存，未配置 Key 时自动回退规则文案。
- **静态 API**：全部数据以 JSON 文件形式随仓库发布，无后端、无数据库、可 CDN。
- **精美看板三页**：
  - `index.html` 总览看板 —— 频道/类型/周期三级切换、分类筛选、书名作者搜索、状态/动势筛选、卡片流；
  - `trend.html` 风向标 —— 排名跃升与热度增长 TOP10、AI 全站风向、赛道分布图、高频题材词云、黑马雷达、热门作者、跨榜常青树；
  - `book.html?id={book_id}` 书籍详情 —— 封面简介、排名 × 热度历史双轴曲线（ECharts）。
- **GitHub Actions 自动化**：定时抓取 → 构建 → JSON 校验 → 提交 → 部署，一条流水线全搞定。
- **GitHub Pages 部署**：官方 `actions/deploy-pages` 现代流程，Fork 即用。
- **CSV 导出**：总览页一键导出当前视图为 CSV（带 BOM，Excel 中文不乱码）。
- **暗色模式**：跟随系统偏好 + 手动切换（记忆到 localStorage），图表配色同步切换。
- **移动端适配**：响应式布局，表格/卡片在小屏自动收纳。

## 在线看板 / 快速开始

**在线体验**：<https://siweimidu.github.io/QiMaoRankTracker2/>

想拥有自己的副本？三步即可：

1. **Fork 本仓库** —— 点击右上角 Fork 按钮。
2. **启用 Actions** —— Fork 后进入仓库的 Actions 标签页，点击绿色按钮「I understand my workflows, go ahead and enable」。
3. **配置 Pages** —— 进入 Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。
4. **（可选）配置 AI Secrets** —— Settings → Secrets and variables → Actions → New repository secret，依次添加 [AI 配置详解](#ai-配置详解)中的 3 个 Secret，启用 AI 风向分析；不配置也能正常运行（使用规则文案）。
5. **立即体验** —— Actions → 「每日榜单抓取与构建」→ Run workflow → Run，约几分钟后访问 `https://<你的用户名>.github.io/QiMaoRankTracker2/` 即可看到看板。

## 每日自动运行说明

- **定时触发**：`scrape.yml` 每天**北京时间 07:30**（UTC 23:30，cron `30 23 * * *`）自动运行，错开 GitHub Actions 整点调度高峰。
- **运行内容**：抓取 16 个榜单（L1）→ 分类层（L2）→ 构建 latest / 趋势 / 静态 API / AI 分析 → JSON 全量校验 → 提交 `data/` `api/` → 部署 Pages。
- **缺档自动补跑（断点续跑）**：爬虫通过 `data/{slug}/task_state_YYYYMMDD.json` 与 `data/categories/task_state_YYYYMMDD.json` 记录当日已完成项；某榜/某分类抓取失败时状态文件保留，**下次运行自动只补抓缺失部分**，全部完成后状态文件自动清理。构建日取全部快照的最大日期，天然支持隔天补跑。
- **健康度可视**：`api/status.json` 的 `boards[].ok` 字段标记每榜是否正常；总览页顶部显示缺档提示条。
- **手动触发**：Actions 页可直接 Run workflow，支持填写 `boards` 参数只抓指定榜单子集（如 `boy-hot-date,girl-new-date`）。
- **强制重跑**：使用「手动强制重跑」工作流，先删除指定日期的断点状态与快照再完整重抓（注意：七猫榜单页为实时页面，重抓得到的始终是当前数据）。

## AI 配置详解

在仓库 **Settings → Secrets and variables → Actions** 中配置以下 3 个 Secret 即可启用 AI 分析（三者齐备才生效）：

| Secret | 含义 | 示例值 |
| --- | --- | --- |
| `API_BASE_URL` | OpenAI 兼容 API 端点（含版本路径） | `https://api.deepseek.com/v1` |
| `API_KEY` | 对应平台的 API 密钥 | `sk-xxxxxxxxxxxxxxxx` |
| `API_MODEL` | 模型名称 | `deepseek-chat` |

**支持的提供商**（任何 OpenAI 兼容 Chat Completions 端点均可）：

| 提供商 | API_BASE_URL | API_MODEL 示例 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 接入点 ID `ep-2024xxxx` |

**AI 产出内容**：全站风向研判（每天仅 1 次 API 调用）+ 逐榜分类速评（分批调用）。结果按 `日期 + 榜单` 缓存于 `data/{slug}/ai_cache/`，同日重跑不重复计费；每条文案带 `ai_source` 标记（`ai` / `rule`）。

**未配置 Key 时的行为**：自动跳过 AI，全部使用内置规则文案（基于黑马/跨榜/题材统计生成），看板功能完全不受影响。

## API 文档

全部端点为静态 JSON 文件，直接 HTTP GET 即可。在线基准地址：

```bash
BASE=https://siweimidu.github.io/QiMaoRankTracker2
```

### 全站聚合端点

| 端点 | 说明 | curl 示例 |
| --- | --- | --- |
| `api/boards.json` | 16 个榜单元信息（slug/名称/频道/最新日期/收录数/小类清单） | `curl $BASE/api/boards.json` |
| `api/status.json` | 全站状态：最后更新/抓取时间、收录总数、新上榜数、黑马数、各榜健康度 `boards[].ok` | `curl $BASE/api/status.json` |
| `api/history.json` | 近 90 天滚动历史：每本书的 `points[]`（日期/榜单/名次/热度） | `curl $BASE/api/history.json` |
| `api/black-horses.json` | 黑马雷达 TOP20（黑马分 = 60% 排名跃升 + 40% 热度增长率） | `curl $BASE/api/black-horses.json` |
| `api/authors.json` | 热门作者榜 TOP30（在榜作品数/覆盖榜数/总热度/代表作） | `curl $BASE/api/authors.json` |
| `api/cross-board.json` | 跨榜常青树（同时在榜 ≥2 榜，含最佳名次/总热度） | `curl $BASE/api/cross-board.json` |

### 书籍端点

| 端点 | 说明 | curl 示例 |
| --- | --- | --- |
| `api/books/{book_id}.json` | 书籍详情：简介/状态/字数/分类/当前在榜/历史曲线数据 | `curl $BASE/api/books/195958.json` |

### 单榜端点（`{slug}` 为榜单短名，如 `boy-hot-date`）

| 端点 | 说明 | curl 示例 |
| --- | --- | --- |
| `api/{slug}/latest.json` | 单榜元信息与分类导航（`types[]` 含各分类文件相对地址） | `curl $BASE/api/boy-hot-date/latest.json` |
| `api/{slug}/latest/all.json` | 该榜全部书籍（含排名变化/热度增量/新上榜标记） | `curl $BASE/api/boy-hot-date/latest/all.json` |
| `api/{slug}/latest/{小类名}.json` | 该榜按小类筛选的书籍（文件名为 URL 编码中文） | `curl "$BASE/api/boy-hot-date/latest/%E9%83%BD%E5%B8%82%E9%AB%98%E6%AD%A6.json"`（都市高武） |

### 分类层端点（小类自动发现）

| 端点 | 说明 | curl 示例 |
| --- | --- | --- |
| `api/categories/latest.json` | 全部小类导航（名称/所属大类/文件地址，随榜单动态增减） | `curl $BASE/api/categories/latest.json` |
| `api/categories/latest/{分类名}.json` | 单个小类最新榜单（URL 编码中文文件名） | `curl "$BASE/api/categories/latest/%E6%80%BB%E8%A3%81%E8%B1%AA%E9%97%A8.json"`（总裁豪门） |

> 提示：中文文件名需 URL 编码访问，可用 `python -c "from urllib.parse import quote; print(quote('都市高武'))"` 生成。

## 本地开发

```bash
# 1. 克隆仓库
git clone https://github.com/siweimidu/QiMaoRankTracker2.git
cd QiMaoRankTracker2

# 2. 创建并激活虚拟环境（Windows 用 .venv\Scripts\activate）
python -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 抓取榜单（两层；只抓部分榜可用 QM_BOARDS=boy-hot-date python scrape_qimao_ranks.py）
python scrape_qimao_ranks.py

# 5. 构建 latest 数据 / 静态 API（如需 AI：export API_BASE_URL=... API_KEY=... API_MODEL=...）
python scripts/build_latest.py

# 6. 本地起静态服务预览看板（前端请求 api/ 为相对路径，需经 HTTP 访问）
python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

## 环境变量参考

### 抓取脚本 `scrape_qimao_ranks.py`

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QM_BOARDS` | 空（全部 16 个） | 逗号分隔的 slug 子集，只抓这些榜单 |
| `QM_SKIP_CATEGORIES` | 空 | 设为 `1` 跳过分类层 |
| `QM_CAT_LIMIT` | `30` | 每分类最多保留本数 |
| `QM_LIMIT` | `0`（不限） | 每榜最多取前 N 本（榜单本身为 Top 20） |
| `QM_DELAY_MIN` | `2.0` | 请求间随机延时下限（秒） |
| `QM_DELAY_MAX` | `4.0` | 请求间随机延时上限（秒） |

### 构建脚本 `scripts/build_latest.py`

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QM_BUILD_BOARDS` | 空（全部） | 逗号分隔 slug 子集，仅构建这些榜（调试用；全站索引仍按全部榜单计算） |
| `API_BASE_URL` | 空 | OpenAI 兼容端点，与下两者齐备才启用 AI |
| `API_KEY` | 空 | API 密钥 |
| `API_MODEL` | 空 | 模型名称 |

## 项目结构

```
QiMaoRankTracker2/
├── .github/
│   └── workflows/                  # GitHub Actions
│       ├── scrape.yml              #   核心流水线：抓取→构建→校验→提交→部署（每日 07:30 北京时间）
│       ├── force_update.yml        #   手动强制重跑（清除断点状态与快照）
│       └── pages.yml               #   前端文件变更时的 Pages 兜底部署
├── api/                            # 静态 API（构建产物，随仓库发布到 Pages）
│   ├── boards.json / status.json / history.json
│   ├── black-horses.json / authors.json / cross-board.json
│   ├── books/{book_id}.json        #   每本上榜书籍的详情与历史
│   ├── {slug}/latest.json          #   单榜导航
│   │   └── latest/{all,小类名}.json
│   └── categories/
│       └── latest/{分类名}.json     #   自动发现的小类榜单
├── data/                           # 快照与构建产物（随仓库提交）
│   ├── {slug}/snapshots/ranks_YYYYMMDD.json   # 每日原始快照（勿改）
│   ├── {slug}/latest_ranks.json / market_summary.json / dates.json
│   ├── {slug}/trends/YYYY-MM-DD.json
│   ├── {slug}/ai_cache/YYYY-MM-DD.json        # AI 结果留档（缓存感知）
│   └── categories/snapshots/ranks_YYYYMMDD.json
├── scripts/
│   └── build_latest.py             # 构建脚本：快照 → latest/趋势/静态 API/AI
├── boards_config.py                # 榜单配置：16 个榜单的单一事实源（含题材词表/赛道分组）
├── scrape_qimao_ranks.py           # 两层爬虫（requests + BeautifulSoup，静态 HTML 无需浏览器）
├── index.html                      # 总览看板（三页之一）
├── trend.html                      # 风向标（三页之二）
├── book.html                       # 书籍详情（三页之三，?id={book_id}）
├── js/                             # common.js / app.js / trend.js / book.js
├── css/                            # tokens.css（明暗主题令牌）/ style.css
└── requirements.txt                # requests / beautifulsoup4 / lxml / openai
```

## 与 FanqieRankTracker 的对比

本项目在同类番茄榜单追踪项目的基础上做了多项增强：

| 能力 | 说明 |
| --- | --- |
| **两层抓取** | L1 榜单层之外新增 L2 分类层：从上榜书籍的分类链接自动发现全部小类并单独建榜，覆盖榜单 Top 20 之外的长尾书目 |
| **分类自动发现** | 无需硬编码分类清单，小类随榜单内容动态增减 |
| **AI 市场分析** | 任意 OpenAI 兼容端点生成全站风向 + 逐榜分类速评，按日缓存控制成本，无 Key 优雅降级 |
| **黑马评分** | 量化公式（排名跃升 × 热度增长率加权）输出黑马榜，而非仅展示原始排名 |
| **跨榜统计** | 跨榜常青树聚合（在榜数/最佳名次/总热度） |
| **作者榜** | 作者维度聚合 TOP30 |
| **更多聚合 API** | boards / status / history / black-horses / authors / cross-board / books 全套静态端点 |

## FAQ

**Q：Fork 后 Actions 不自动运行？**
A：① Fork 仓库需在 Actions 页手动点一次启用；② `schedule` 触发只对默认分支（main）生效；③ GitHub 会对 60 天无活动的仓库暂停定时任务，点一次 Star 或提交任意 commit 即可恢复；④ 定时任务高峰期可能延迟几分钟到半小时属正常现象。

**Q：Pages 打开 404？**
A：进入 Settings → Pages，确认 Build and deployment 的 Source 选择了 **GitHub Actions**（而不是 Deploy from a branch）；再手动跑一次「Pages 兜底部署」工作流。首次部署需等待 1-2 分钟生效。

**Q：想改抓取频率 / 时间？**
A：编辑 `.github/workflows/scrape.yml` 中的 `cron` 表达式（UTC 时间），例如北京 07:30 = UTC 前一天 23:30 → `30 23 * * *`。注意保持礼貌延时，勿高频抓取。

**Q：数据保留多久？**
A：原始快照 `data/{slug}/snapshots/` 长期保留；趋势与历史 API（`api/history.json` 等）按 **90 天滚动窗口**输出。

**Q：遇到反爬/抓取失败怎么办？**
A：爬虫已内置桌面 UA、2-4 秒随机延时与指数退避重试（最多 3 次）。仍失败时可调大 `QM_DELAY_MIN` / `QM_DELAY_MAX`；断点续跑机制会自动补抓缺失部分。目标站结构变化导致解析为空时，需更新 `scrape_qimao_ranks.py` 的解析逻辑。

**Q：AI 分析报错会影响构建吗？**
A：不会。AI 失败或未配置时自动回退规则文案，管线照常完成并部署。

## 免责声明

- 本项目**仅供学习与研究用途**（网页抓取技术、数据可视化、GitHub Actions 自动化实践），不得用于任何商业用途。
- 数据来源为七猫中文网公开排行榜页面，版权归原作者及平台所有；请勿高频抓取、勿对目标站点造成压力，本项目默认每日一次 + 随机延时已属低频礼貌访问。
- 依据目标站 `robots.txt` 与服务条款的变化，请自行评估继续运行的合规性；若收到权利方异议请立即停止使用并删除数据。
