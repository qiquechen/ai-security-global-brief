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
│  ├─ deliver/                   # 预备清单（prepared）与 SMTP 发信（附件）
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

## 测试

```powershell
python -m unittest discover -s tests
```

测试全部以标准库 `unittest` 编写（`tests/` 下 8 个文件、50 个用例），**不需要额外安装 pytest**；网络与模型调用均以 mock 替代，可离线运行。

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

来源清单现有 **135 项**（启用 100、停用 35）。2026-09-13 本轮新增 15 项（补齐 40 家清单缺项 +
官方/国际源 + 纽约时报科技），并修复 `independent_ai` 白名单（站点已迁移到
`the-independent.com`）。同日依据 `scripts/probe_candidates.py` 的探测结果，
把 `csis`、`chicago_council`、`cato`、`rand`、`heritage` 切换到探测到的可用 RSS 入口：
前四者已恢复为 success；`heritage` 的 RSS 可正常读取，但文章正文页持续 403、拿不到正文，
已停用待更换入口。这印证了一点：**切 RSS 只能拿到标题与链接，正文仍要回抓原页面，
站点若在正文页做反爬，单靠换 feed 救不回来**。
实测持续 403 或超时的 FPRI、The Diplomat、The National Interest、NBR、
Center for American Progress、USCBC、Coe AI、UNESCO AI、Lawfare、OpenAI 新闻、
NYT 科技等暂停启用；配置保留，便于后续替换入口。

2026-09-22 按用户提供的《主要跟踪智库及媒体》166 项跟踪清单补充来源：逐条审核后纳入 53 项（`docs/来源_166项审核表.md`），审核口径为只排除明显与 AI 安全治理无关者，地区不再限于美欧，日本、韩国、新加坡、印度、巴西、南非、以色列、马来西亚、俄罗斯、澳大利亚、加拿大均纳入。新源单源候选上限统一设为 10，低于原库的 15~35。其中 16 项探测返回 403（Cloudflare 反爬，RSS 入口同样被挡）暂未启用；其余首次探测超时的来源复测大多可达——连接情况受网络路径波动影响较大，上线前需在部署网络下复测一遍。

正式日报统计窗口为北京时间 `[昨日06:00, 今日06:00)`，06:05提前准备，07:00读取就绪文件发送。
输出按“新闻媒体信息”和“机构信息”分目录：每类包含1份摘要合集，以及每篇入选
文章对应的独立原文Word；合集和原文中的链接均为可点击外部超链接。

## 提前整理与按时发送（指令详解）

整理（筛选、摘要、生成双合集与原文、准备邮件清单）和发送邮件是两个独立步骤。
准备阶段可以提前执行并把本期邮件固定下来；发送阶段只读取已完成的本期清单，
不调用模型、不查询数据库、不生成或压缩任何文件。因此整理耗时变长也不会挤占
既定发送时间，也不会出现“到点现场生成、生成失败漏发”的情况。

### 1. 只整理，不发送

```powershell
# 读取06:00截止窗口（[昨日06:00, 今日06:00)），生成合集、原文和邮件清单，不发信
python scripts/run_daily.py --input db

# 调试用滚动窗口：最近48小时；正式运行不要传 --hours
python scripts/run_daily.py --input db --hours 48

# 整理完成后，把本期邮件清单路径原子写入就绪指针，供稍后发送
python scripts/run_daily.py --input db --ready-file data/manual-ready.txt
```

带 `--ready-file` 时，脚本先删除旧指针再开始整理，只有整理成功才写入新指针；
整理中途失败会保留“无就绪文件”的状态，避免误发上一期旧批次。每次整理都写入
独立子目录（`report_output/<日期>/<时分秒>_<随机串>/`），不会覆盖待发送附件。

### 2. 发送前预检（可选）

```powershell
# 只校验清单和附件：打印主题、正文、准备时间、附件清单与大小，并核对SHA-256；不发信
python scripts/send_ready.py --ready-file data/manual-ready.txt --check
```

`--check` 不连接 SMTP、不需要模型密钥，适合在发送前确认本期内容与附件是否完整。

### 3. 按时发送

```powershell
# 读取指定就绪指针并发送
python scripts/send_ready.py --ready-file data/manual-ready.txt

# 不传参数时，自动读取当天的 data/ready-daily-YYYYMMDD.txt
python scripts/send_ready.py

# 等价入口：直接把清单绝对路径交给独立发送命令
python scripts/run_daily.py --send-prepared "C:\path\to\report_output\...\mail_manifest.json"
```

发送阶段会逐份核对附件的SHA-256：附件缺失或内容变动会报错中止，不会发出不完整邮件。
重复执行同一清单会再次发信，请按需执行。`--send-prepared` 不可与
`--send`/`--hours`/`--at`/`--ready-file` 混用。

### 4. 整理并立即发送（仅联调）

```powershell
python scripts/run_daily.py --input db --send
```

`--send` 等价于“整理成功后立刻发送本期的同一清单”，保留用于一次性联调；
正式运行请使用上面第1、3步的分离方式。

### 5. 定时任务

`scripts/install_task.ps1`（管理员运行）注册两个互相独立的每日任务：

- `AIDigestPrepare`：每天 06:05 执行 `scripts/prepare_daily.py`，整理并将清单写入
  `data/ready-daily-YYYYMMDD.txt`。
- `AIDigestDaily`：每天 07:00 执行 `scripts/send_ready.py`，只读取当天就绪指针发送。

统计窗口仍截止 06:00。若整理耗时超过发送时间，应依据日志提前安排整理或后移发送时间；
就绪文件缺失时发送任务直接报错，不回退到历史批次，也不现场生成。

### 6. 退出码与耗时

- 退出码：`0` 成功；`2` 缺少 DeepSeek/必要配置；`3` 样例入口无可处理文章；异常退出为 `1`。
- 整理结束时控制台会打印“摘要生成耗时”和“本次整理总耗时”，发送结束会打印“发送完成，耗时”。
- 阶段耗时同时写入 `logs/operations-YYYYMMDD.jsonl`，可用 `python scripts/inspect_operations.py` 查看。

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

筛选和摘要共用 `DEEPSEEK_CHAT_MODEL=deepseek-v4-flash`，通过
`DEEPSEEK_FILTER_THINKING_MODE=disabled` 在筛选阶段关闭思考，
`DEEPSEEK_SUMMARY_THINKING_MODE=disabled` 默认在摘要生成时关闭思考；超长摘要二次压缩固定关闭思考。
两个阶段可以分别设置为 `enabled` 或 `disabled`。
`DEEPSEEK_THINKING_MODE` 仅作为未指定模式的通用客户端调用默认值，不覆盖阶段配置。
已移除未被调用流程使用的 `DEEPSEEK_REASONER_MODEL`。模型名称与思考模式分别配置，
不再依赖旧模型别名。开启思考时不发送 temperature 参数，详见
[DeepSeek 思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。
日志会记录请求模型、思考模式和服务端响应模型。修改 `.env` 后需重启运行进程；
同名系统环境变量优先于 `.env`，部署时应同步检查。

## 剔除记录与到期清理

筛选明确返回 `in_scope=false` 时，系统在同一事务中删除数据库中的文章原文，
并将规范化 URL、剔除理由和 UTC 时间写入 `rejected_articles` 表。
后续筛选跳过该 URL，采集跨来源共用此排除集合，在下载正文前跳过已知 URL；
新别名只有在解析到相同规范 URL 后才能识别。模型请求失败或判定格式无效时保留文章重试，
不进入黑名单；收录后因数量配额未进入日报的文章也不进入黑名单。

`REJECTION_RETENTION_DAYS=7` 控制保留天数（正整数）。每次采集及批量筛选启动时
自动删除已满保留天数的记录，重复命中不延长有效期。程序停运期间不会后台清理，
下次运行补做清理；到期 URL 可再次采集，但已删除的原文不会自动恢复。
现有旧日志不会自动转成黑名单，下一次明确判定后开始记录。
该机制不删除历史 Word 或已导出的 JSONL；重新导入的 URL 仍受有效期内黑名单拦截。

## 协作说明

- H1（质量与交付）负责 `filter` / `summarize` / `report` / `deliver`；
- H2（采集底座）负责 `ingest` 与 `config/sources.json`；
- 接口契约（DB schema / 判定 JSON / 源清单格式）见 `docs/摘报系统开发协同分工计划_v1.0.md`。


## 2026-09-09：提前准备与操作日志

摘要默认关闭思考模式（现有 `.env` 同步调整），目标正文300—400字符，硬上限500字符；保留核心事实、关键数字、限定条件与政策状态。首次超长或为空时最多重写一次，重写固定关闭思考，仍不合格则报错并记录结果，不再硬截断正文。合格摘要按输入、提示词、模型、思考配置缓存到 `data/summary_cache/`，配置或内容改变自动失效。事实准确性仍需人工抽查；实际耗时与返工率以真实运行日志为准。

准备阶段（不加 `--send`）会完成筛选、摘要、双合集、原文及必要压缩包，并生成 `mail_manifest.json`（主题、正文、附件相对路径及SHA-256）。每次准备使用独立子目录，避免覆盖待发送附件。示例：

```powershell
python scripts/run_daily.py --input db --hours 48 --ready-file data/manual-ready.txt
python scripts/send_ready.py --ready-file data/manual-ready.txt
# 或将上一命令输出的清单绝对路径传给独立发送入口：
python scripts/run_daily.py --send-prepared "<邮件清单绝对路径>"
```

发送入口无需模型密钥，不查询数据库、不调用模型、不生成Word或压缩包；附件缺失或校验失败则中止。手工重复发送同一清单会再次发信，请按需执行。旧 `--send` 保留用于显式的一次性生成并投递。

测试定时脚本：半点抓取成功后立即准备最近48小时数据；整点读取本小时专用就绪文件。准备未完成则该次发送报错，不回退到历史批次，也不现场生成。正式任务安装脚本改为06:05准备、07:00发送；重新安装后才更新系统中的正式任务。统计窗口仍截止06:00。准备耗时超过发送时间时，应依据日志提前安排准备或调整发送时间。

结构化操作日志：`logs/operations-YYYYMMDD.jsonl`，按UTC日期追加，每行一条JSON；`time`为带时区时间，`run_id`关联一次操作，`stage/status`表示阶段与开始/完成/失败，`elapsed_seconds`记录耗时。带`exit_code`的命令应结合退出码判断业务是否成功。异常记录类型，不记录密钥或模型思考过程。

- `crawl`：整个抓取命令时间；`crawl.result`：抓取窗口、合计新增、重复、黑名单跳过、错误及抓取入库耗时。
- `crawl.source`：每个来源抓取耗时、状态、各类计数、错误和抓取文章标题/链接。`blacklisted`表示本来源实际命中并跳过的唯一URL数，与普通已入库重复`existing`分开统计；跨来源可能重复计数。
- `classification` / `classification.article`：判定阶段和单篇耗时、判定结果、黑名单跳过与调用失败数。
- `summary` / `summary.article`：摘要阶段和单篇耗时、字数、首次字数、重写次数、缓存命中及最终摘要；失败保留不合格重写结果。
- `llm.request`：每次模型请求耗时、尝试序号、模型与思考开关；不再叠加SDK自动重试。
- `report.files` / `mail.prepare` / `mail.send`：生成文件、准备附件和发送的耗时与结果。发送失败和就绪文件缺失亦有日志。

日志可直接用文本编辑器逐行查看，或在PowerShell中查询最近20条：

```powershell
Get-Content logs/operations-*.jsonl | Select-Object -Last 20 | ForEach-Object { $_ | ConvertFrom-Json } | Format-List
```


## 分类保底与历史补足

正式数据库出报要求媒体、机构各至少5篇合格材料，总篇数默认仍为20。先按重要度排序；同重要度内机构的来源优先级乘以1.2，再考虑时间。先为两类各保留5个名额，再按排序分配剩余名额，避免机构材料在总数截断时被挤出。

初始窗口不足时，从窗口起点逐日向前扩展，仅处理尚未达到保底数量的类别。先读取该历史日的数据库材料；仍不足时，按该日时间窗抓取缺少类别的启用来源，再读取新入库材料。整个选择过程按URL去重，同一URL不重复判定。范围与质量判定不放宽，黑名单仍生效，不用不相关内容凑数。

扩展后，邮件正文及合集的统计说明记录原窗口、向前扩展天数和实际覆盖起点；文章保留真实发布时间。操作日志新增`selection`与`selection.expand`，包含每轮缺少类别、数量、候选数与补抓结果。最多向前扩展30天；若来源的公开列表不提供足够历史文章或合格文章不足，则报错并停止文件准备和发送。历史网页可发现范围不保证覆盖完整30天。失败的重新准备会清除旧就绪指针，避免误发旧批次。

环境配置：`REPORT_MIN_PER_GROUP=5`、`REPORT_MAX_EXPANSION_DAYS=30`、`REPORT_INSTITUTION_WEIGHT=1.2`。总篇数上限必须不少于两类保底数量之和。`--input sample`仅用于固定样例联调，明确关闭配额要求，不抓取真实历史；正式数据库入口默认执行新规则。已发送的邮件不受影响，也不会自动补发。

## 2026-09-10：摘要完整性审核、响应详情与西方AI来源扩充

针对“字数合格但正文结束在半句话”的问题，新增三层检查：模型响应结束原因、正文结构检查（类型、500字符上限、完整句尾、括号引号配对）、基于原文输入的语义与事实审核。语义审核可识别“残句后补句号”、否定丢失、政策状态改变等问题。审核未通过时携带原文和问题重写一次，重写后再次审核；仍不合格则中止准备，不缓存、不导出残缺摘报。旧摘要缓存自动失效，只有按新版规则审核通过的结果才能复用。

普通新摘要增加一次关闭思考的审核调用；审核通过的缓存命中不调用模型。审核仍使用同一配置模型，不能保证发现所有事实错误；当前生成与审核沿用最多6000字符的原文输入，不等同于全文事实核查。

对比 GitHub 变体 `Liux628/ai-digest` 的 `ffdd6e67da12a4c7042b44e98a3b39c3a1fe1526`：融合其 token 用量输出，沿用本地分阶段日志与缓存；未采用变体中压缩失败后 `body[:500]` 的做法。`llm.request` 现在记录请求/响应模型、响应ID、finish_reason、输入/输出/合计token、服务端报告的缓存token和推理token、耗时、完整最终响应。缺失用量记为null，不冒充0；失败重试的已报告用量同样保留。日志不保存密钥和思考正文。

`LLM_LOG_RESPONSE=true` 默认开启模型原始最终响应日志，可设false关闭该字段；既有摘要与审核业务结果仍记入阶段日志。每条模型调用的耗时不含调用间退避，文章/整体操作耗时包含退避。

```powershell
# 最近一个运行：模型、token、失败次数及阶段耗时
python scripts/inspect_operations.py
# 指定运行，并展开每次模型响应
python scripts/inspect_operations.py --run-id <日志中的run_id> --responses
```

汇总包含失败重试；missing_calls表示服务端未报告该项用量的调用数，known_total只是已知合计。llm_elapsed_seconds是请求耗时之和；阶段相互嵌套，不应把所有阶段耗时相加当作总耗时。

来源总数从53增至67，启用从42增至54。新增启用12项：OpenAI新闻与研究、Anthropic研究与新闻、Google DeepMind、Stanford HAI、METR、Apollo Research、GovAI、Hugging Face博客，以及TechCrunch AI、MIT Technology Review AI、WIRED AI。继续遵守域名白名单、robots和原有安全治理筛选标准，不因扩源降低收录门槛。采集入口及正文已做单样本在线验证；OpenAI复测出现间歇403，可用性随站点与网络变化。新增但暂不启用2项：Lawfare RSS返回403；Anthropic对齐博客缺少稳定日期元数据，通用解析可能误取历史日期，等待日期适配。Anthropic主站研究/新闻已增加文章页头日期适配，避免将正文引用的历史年份误当发布日期。来源明细见config/sources.json。

真实链路样例：METR安全事件文章生成204字符中文摘要，审核通过，2次模型请求，无重写；服务端响应模型为deepseek-flash，输入3646、输出158、总计3804 token；模型请求合计3.719秒。数据仅代表这次实测，不是性能承诺。此次未发送邮件，也未更新已准备或已发送的历史文件；重启已有常驻进程后生效，下一次独立脚本启动直接读取新代码。
