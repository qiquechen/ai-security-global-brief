"""根据 config/sources.json 生成源清单统计，写入 docs/源清单统计.md。

用法（venv 激活）：
  python scripts\\gen_source_docs.py

生成内容：总数/启用/停用、类型分布、采集方式分布、地区分布、逐源明细表。
每次增删来源后重跑本脚本，即可让文档数据源部分保持最新。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.json"
OUT = ROOT / "docs" / "源清单统计.md"

# 地区归一（配置里中英文混用）
REGION = {"美国": "美国", "US": "美国", "英国": "英国", "UK": "英国",
          "德国": "德国", "欧盟": "欧盟", "EU": "欧盟",
          "国际组织": "国际组织", "INT": "国际组织", "加拿大": "加拿大",
          "澳大利亚": "澳大利亚", "中国香港": "中国香港", "日本": "日本",
          "JP": "日本"}


def _table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def main() -> int:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    sources = data.get("sources", []) if isinstance(data, dict) else data
    total = len(sources)
    enabled = [s for s in sources if s.get("enabled", True)]
    disabled = [s for s in sources if not s.get("enabled", True)]

    parts = ["# 数据源清单统计（自动生成）", "",
             "> 本文件由 `scripts/gen_source_docs.py` 依据 `config/sources.json` 自动生成，请勿手工编辑。",
             "> 增删来源后重跑该脚本即可更新。", "",
             f"生成时间基准：以当前 `config/sources.json` 为准。", "",
             _table(["指标", "数量"], [
                 ["配置源总数", total], ["启用", len(enabled)], ["停用", len(disabled)],
                 ["启用源每轮候选上限之和",
                  sum(int(s.get("max_candidates", 20)) for s in enabled)]]), ""]

    # 类型分布
    trows = []
    for t in sorted({str(s.get("type", "")) for s in sources}):
        g = [s for s in sources if str(s.get("type", "")) == t]
        on = sum(1 for s in g if s.get("enabled", True))
        trows.append([t or "(空)", len(g), on, len(g) - on])
    parts += ["## 按类型分布", "", _table(["类型", "总数", "启用", "停用"], trows), ""]

    # 采集方式分布
    mrows = []
    for m in ("page", "rss", "sitemap"):
        g = [s for s in sources if str(s.get("method", "page")) == m]
        on = sum(1 for s in g if s.get("enabled", True))
        mrows.append([m, len(g), on, len(g) - on])
    parts += ["## 按采集方式分布", "", _table(["方式", "总数", "启用", "停用"], mrows), ""]

    # 地区分布
    counter = Counter()
    on_counter = Counter()
    for s in sources:
        region = REGION.get(str(s.get("country", "")).strip(), str(s.get("country", "")) or "未标注")
        counter[region] += 1
        if s.get("enabled", True):
            on_counter[region] += 1
    rrows = [[r, counter[r], on_counter[r]] for r in sorted(counter)]
    parts += ["## 按地区分布", "", _table(["地区", "总数", "启用"], rrows), ""]

    # 逐源明细
    drows = []
    for s in sources:
        entry = s.get("url") or s.get("feed") or s.get("homepage") or ""
        drows.append([
            f"`{s.get('id', '')}`", s.get("name", ""), s.get("country", ""),
            s.get("type", ""), s.get("method", "page"),
            "启用" if s.get("enabled", True) else "停用",
            s.get("priority", 50), s.get("max_candidates", 20),
            f"[入口]({entry})" if entry else "",
        ])
    parts += ["## 逐源明细", "",
              _table(["id", "名称", "地区", "类型", "方式", "状态", "优先级", "候选上限", "入口"], drows), ""]

    # 停用原因
    parts += ["## 停用原因", "", _table(
        ["id", "原因"], [[f"`{s.get('id')}`", s.get("disabled_reason", "")] for s in disabled]), ""]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"已生成：{OUT}")
    print(f"共 {total} 项，启用 {len(enabled)}，停用 {len(disabled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
