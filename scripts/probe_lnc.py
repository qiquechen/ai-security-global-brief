"""探测被 403/反爬挡住的来源能否通过 Crawl4AI 浏览器渲染救回。

用法（在项目根目录、venv 激活后）：
  python scripts/probe_lnc.py                 # 测全部「停用且原因是 403」的来源
  python scripts/probe_lnc.py --ids wef,ifri  # 只测指定来源
  python scripts/probe_lnc.py --limit 5       # 只测前 5 个

输出：每个来源的静态请求状态 vs 浏览器渲染结果，以及"浏览器救回"清单。
本脚本只读配置、不写数据库、不改 sources.json。
首次使用前需已执行 `crawl4ai-setup` 安装浏览器。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
}


def load_proxy() -> str:
    import os
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("PROXY_PRIMARY", "PROXY", "PROXY_BACKUP"):
        v = os.getenv(k) or env.get(k) or ""
        if v:
            return v
    return ""


def static_probe(session: requests.Session, url: str) -> str:
    try:
        r = session.get(url, timeout=20, allow_redirects=True)
        return str(r.status_code)
    except requests.exceptions.Timeout:
        return "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return f"ERR:{type(exc).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="逗号分隔的来源 id")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data
    targets = [s for s in sources
               if not s.get("enabled") and "403" in (s.get("disabled_reason") or "")]
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        targets = [s for s in targets if s["id"] in want]
    if args.limit:
        targets = targets[: args.limit]

    proxy = load_proxy()
    session = requests.Session()
    session.trust_env = False
    session.headers.update(HEADERS)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
        print(f"静态请求使用代理：{proxy}")
    else:
        print("静态请求直连（未配置代理）")

    print(f"共 {len(targets)} 个来源待测。浏览器渲染较慢，请耐心等待。\n")

    try:
        from ai_digest.ingest.lnc import Crawl4AIBackend
    except Exception as exc:  # noqa: BLE001
        print(f"无法加载 Crawl4AI：{exc}")
        return 2

    rescued, still_bad = [], []
    with Crawl4AIBackend() as backend:
        for s in targets:
            sid, url = s["id"], s["url"]
            stat = static_probe(session, url)
            try:
                page = backend.crawl(url)
                code = page.status_code
                n = len((page.markdown or page.html or "").strip())
            except Exception as exc:  # noqa: BLE001
                code, n = None, 0
                note = f"{type(exc).__name__}"
            else:
                note = ""
            ok = (code == 200 and n >= 180)
            flag = "✅ 浏览器救回" if ok else "❌ 仍不可用"
            if ok:
                rescued.append(sid)
            else:
                still_bad.append(sid)
            extra = f" ({note})" if note else ""
            print(f"{flag}  {sid:18s} 静态={stat:12s} 浏览器={code} 正文={n}字{extra}")

    print(f"\n浏览器救回 {len(rescued)} / {len(targets)}：{rescued}")
    print(f"仍不可用 {len(still_bad)}：{still_bad}")
    print("\n下一步：把救回的这些在 config/sources.json 里 enabled 改回 true（已配好 lnc_mode=fallback）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
