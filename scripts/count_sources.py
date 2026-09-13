"""统计 config/sources.json 的源数量与分布，用于同步文档中的数字。

用法（venv 激活）：
  python scripts\\count_sources.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.json"


def main() -> int:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    sources = data.get("sources", []) if isinstance(data, dict) else data
    total = len(sources)
    enabled = [s for s in sources if s.get("enabled", True)]
    print(f"配置源总数: {total}")
    print(f"启用: {len(enabled)}")
    print(f"停用: {total - len(enabled)}")
    print()
    print("类型分布（总数/启用/停用）:")
    types = sorted({str(s.get("type", "")) for s in sources})
    for t in types:
        group = [s for s in sources if str(s.get("type", "")) == t]
        on = sum(1 for s in group if s.get("enabled", True))
        print(f"  {t or '(空)'}: {len(group)} / {on} / {len(group) - on}")
    print()
    print("采集方式分布（总数/启用/停用）:")
    for m in ("page", "rss", "sitemap"):
        group = [s for s in sources if str(s.get("method", "page")) == m]
        on = sum(1 for s in group if s.get("enabled", True))
        print(f"  {m}: {len(group)} / {on} / {len(group) - on}")
    print()
    print("地区分布（总数/启用/停用）:")
    countries = sorted({str(s.get("country", "") or "(空)") for s in sources})
    for c in countries:
        group = [s for s in sources if str(s.get("country", "") or "(空)") == c]
        on = sum(1 for s in group if s.get("enabled", True))
        print(f"  {c}: {len(group)} / {on} / {len(group) - on}")
    print()
    cap = sum(int(s.get("max_candidates", 20)) for s in enabled)
    print(f"启用源每轮候选上限之和: {cap}")
    print()
    print("停用清单:")
    for s in sources:
        if not s.get("enabled", True):
            print(f"  {s.get('id')}: {s.get('disabled_reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
