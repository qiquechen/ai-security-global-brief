# 境外 AI 安全治理信息智能摘报系统

面向决策支撑的境外人工智能安全治理信息每日摘报智能体。
筛选语义标准对齐中国《人工智能安全治理框架》2.0 版（见 `docs/`）。

## 目录结构

```
ai-digest/
├─ docs/                        # 口径与协同文档（v1.0 为现行标准）
├─ config/sources.json          # 采集源清单（H2 维护）
├─ sample/sample_articles.jsonl # 测试用样本文章（占位，非真实抓取）
├─ scripts/demo_h1.py           # H1 最小链路演示：判定→摘要→docx（可选发信）
├─ ai_digest/
│  ├─ config.py                 # 环境加载与全局配置
│  ├─ llm/deepseek.py           # DeepSeek 客户端（模型抽象层）
│  ├─ filter/                   # v1.0 语义判定：prompts + classify + 术语表
│  ├─ summarize/                # 500 字中文摘要/翻译
│  ├─ report/                   # 排序配额 + 公文 docx 生成
│  ├─ deliver/emailer.py        # SMTP 发信（附件）
│  └─ ingest/                   # H2采集底座（已接入H1数据库契约）
```

## 快速开始

```bash
python -m venv .venv                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # 填入 DeepSeek key / SMTP 授权码
python scripts/demo_h1.py            # 跑通：判定 → 摘要 → 生成双合集及逐条原文docx
python scripts/demo_h1.py --send     # 全部生成后一次发到 RECIPIENT
```

## H2采集与融合运行

```powershell
# 校验融合后的来源清单
python -m ai_digest.ingest.run --validate-only

# 单源抓取并导出真实样本
python -m ai_digest.ingest.run --source aisi_uk --days 60 --max-per-source 3 --export data/sample_articles.jsonl

# 默认6路并行；可按网络和代理容量调整
python -m ai_digest.ingest.run --days 2 --workers 8

# H1从真实数据库执行判定→摘要→双合集/逐条原文DOCX（测试时不发邮件）
python scripts/run_daily.py --input db --hours 1440

# 每小时调度所调用的统一入口
python scripts/run_crawl.py
```

采集侧默认遵守域名白名单与 `robots.txt`，同域请求间隔0.3秒，读取 `.env`
中的 `PROXY_PRIMARY`/`PROXY_BACKUP`；旧 `PROXY` 始终作为末级兜底线路，
避免主备都失效时整次采集失败。启动时会做连通性检测，主线路发生连接、代理或
超时故障时自动切换下一条线路。`robots.txt` 能读取时会严格执行禁抓规则；
无法读取（超时/SSL/5xx）时默认放行并告警（可设 `INGEST_ROBOTS_FAIL_CLOSED=true`
改回 fail-closed）。来源失败会写入 `logs/`，不会中断其他来源；4xx 结果不重试，
只对连接/超时/429/5xx 退避重试。语义筛选仍由H1执行。

采集默认通过 `INGEST_WORKERS=6` 并行处理不同来源；同一域名仍共享
`INGEST_REQUEST_INTERVAL` 限速和 robots 缓存。候选链接先排除已入库 URL，再按
标题、路径中的人工智能安全治理相关度和发布时间排序，相关度只影响抓取顺序，
不会直接删除信息。文章发布日期优先于修改时间和 Feed 时间，数据库同时按 URL
和正文指纹去重。连续三个文章页返回 401/403 时会提前停止该来源，避免反复请求。

来源清单现有 53 项、启用 42 项。依据 `Bookmarks (1)` 新增 Ars Technica AI、
BBC科技、CBS News科技、南华早报人工智能专题、日本时报人工智能专题和独立报 AI
专题；前四项使用 RSS，后两项使用专题页。实测持续返回 403 的 FPRI、The Diplomat、
The National Interest、OECD AI、NBR、Center for American Progress，以及反复连接
超时的澳大利亚 AISI 暂停启用；配置仍保留，便于后续替换入口。

正式日报统计窗口为北京时间 `[昨日06:00, 今日06:00)`，07:00生成并单次发送。
输出按“新闻媒体信息”和“机构信息”分目录：每类包含1份摘要合集，以及每篇入选
文章对应的独立原文Word；合集和原文中的链接均为可点击外部超链接。

邮件原文附件方式在 `.env` 中配置：

```dotenv
MAIL_ORIGINALS_ZIP=false
MAIL_ORIGINALS_ZIP_THRESHOLD=10
```

`MAIL_ORIGINALS_ZIP=true` 时，全部原文打成一个 ZIP；`false` 时逐个附上原文。
当新闻媒体与机构两类原文总数**严格超过** `MAIL_ORIGINALS_ZIP_THRESHOLD` 时，
无论开关如何设置都强制使用 ZIP。默认阈值为 10（10 份逐个发送，11 份打包）；
阈值须为非负整数，0 表示只要有原文就打包。两份摘要合集始终作为独立 Word 附件，
不计入阈值。没有原文时只发送合集，不创建空压缩包。
ZIP 保存在当次输出目录的 `原文_日期.zip`，保留原文分类目录，且只包含当次生成的原文；
本地独立 Word 文件仍保留。仅生成报告、不发邮件时不打包。

DeepSeek密钥优先读取环境变量或 `.env`；未配置有效值时，也支持读取项目同级的
`deepseek_API.txt`。密钥文件不得放入压缩包或Git仓库。

## 协作说明

- H1（质量与交付）负责 `filter` / `summarize` / `report` / `deliver`；
- H2（采集底座）负责 `ingest` 与 `config/sources.json`；
- 接口契约（DB schema / 判定 JSON / 源清单格式）见 `docs/摘报系统开发协同分工计划_v1.0.md`。
