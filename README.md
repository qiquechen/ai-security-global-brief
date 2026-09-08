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
python scripts/demo_h1.py            # 跑通：判定 → 摘要 → 生成 demo 摘报 docx
python scripts/demo_h1.py --send     # 额外把 docx 发到 RECIPIENT
```

## H2采集与融合运行

```powershell
# 校验融合后的来源清单
python -m ai_digest.ingest.run --validate-only

# 单源抓取并导出真实样本
python -m ai_digest.ingest.run --source aisi_uk --days 60 --max-per-source 3 --export data/sample_articles.jsonl

# H1从真实数据库执行判定→摘要→DOCX（测试时不发邮件）
python scripts/run_daily.py --input db --hours 1440

# 每小时调度所调用的统一入口
python scripts/run_crawl.py
```

采集侧默认遵守域名白名单与 `robots.txt`，同域请求间隔0.3秒，读取 `.env`
中的 `PROXY`；来源失败会写入 `logs/`，不会中断其他来源。语义筛选仍由H1执行。

DeepSeek密钥优先读取环境变量或 `.env`；未配置有效值时，也支持读取项目同级的
`deepseek_API.txt`。密钥文件不得放入压缩包或Git仓库。

## 协作说明

- H1（质量与交付）负责 `filter` / `summarize` / `report` / `deliver`；
- H2（采集底座）负责 `ingest` 与 `config/sources.json`；
- 接口契约（DB schema / 判定 JSON / 源清单格式）见 `docs/摘报系统开发协同分工计划_v1.0.md`。
