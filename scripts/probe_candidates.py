"""批量探测"顽固源"的可用 RSS / sitemap 入口。

用法（venv 激活、v2rayN 开着）：
  python scripts\\probe_candidates.py

对每个源：先试人工列出的候选地址，再自动尝试常见 RSS/sitemap 路径。
输出每个地址的状态码与类型（rss / sitemap / html / 其它），便于挑选可用入口。
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 人工候选：按经验给出的可能入口（可能过期，靠探测验证）
CURATED = {
    "heritage": [
        "https://www.heritage.org/rss",
        "https://www.heritage.org/feeds/all",
        "https://www.heritage.org/news/feed",
    ],
    "stimson": ["https://www.stimson.org/feed/", "https://www.stimson.org/feed"],
    "national_interest": [
        "https://nationalinterest.org/feed",
        "https://nationalinterest.org/rss.xml",
    ],
    "the_diplomat": ["https://thediplomat.com/feed/", "https://thediplomat.com/feed"],
    "fpri": ["https://www.fpri.org/feed/", "https://www.fpri.org/rss.xml"],
    "nbr": ["https://www.nbr.org/feed/", "https://www.nbr.org/feed"],
    "americanprogress": [
        "https://www.americanprogress.org/feed/",
        "https://www.americanprogress.org/feed",
    ],
    "cato": ["https://www.cato.org/rss", "https://www.cato.org/rss/all"],
    "rand": [
        "https://www.rand.org/topics/artificial-intelligence.xml",
        "https://www.rand.org/news.xml",
    ],
    "us_commerce": [
        "https://www.commerce.gov/rss",
        "https://www.commerce.gov/news/rss",
    ],
    "wilson_center": [
        "https://www.wilsoncenter.org/feed",
        "https://www.wilsoncenter.org/rss.xml",
    ],
    "csis": ["https://www.csis.org/analysis/feed", "https://www.csis.org/rss.xml"],
    "belfer": ["https://www.belfercenter.org/rss.xml"],
    "chicago_council": ["https://globalaffairs.org/rss.xml", "https://globalaffairs.org/feed"],
    "the_economist": [
        "https://www.economist.com/latest/rss.xml",
        "https://www.economist.com/international/rss.xml",
    ],
    "oecd_ai": ["https://oecd.ai/en/rss", "https://oecd.ai/en/feed"],
    "coe_ai": [
        "https://www.coe.int/en/web/artificial-intelligence/rss",
        "https://www.coe.int/en/web/portal/rss",
    ],
    "unesco_ai": ["https://www.unesco.org/en/rss", "https://www.unesco.org/en/newsroom/rss"],
    "uscbc": ["https://www.uschina.org/feed", "https://www.uschina.org/rss.xml"],
    "mckinsey_gi": [
        "https://www.mckinsey.com/featured-insights/rss",
        "https://www.mckinsey.com/rss",
    ],
    "lawfare": ["https://www.lawfaremedia.org/rss.xml", "https://www.lawfaremedia.org/feed"],
    "govai": ["https://www.governance.ai/rss.xml", "https://www.governance.ai/feed"],
}

# 自动补测的常见路径
COMMON_PATHS = ["/feed/", "/feed", "/rss", "/rss.xml", "/atom.xml", "/index.xml", "/sitemap.xml"]

# 域名 -> 用于自动补测的站点根
DOMAIN_ROOT = {
    "heritage": "https://www.heritage.org",
    "stimson": "https://www.stimson.org",
    "national_interest": "https://nationalinterest.org",
    "the_diplomat": "https://thediplomat.com",
    "fpri": "https://www.fpri.org",
    "nbr": "https://www.nbr.org",
    "americanprogress": "https://www.americanprogress.org",
    "cato": "https://www.cato.org",
    "csis": "https://www.csis.org",
    "belfer": "https://www.belfercenter.org",
    "chicago_council": "https://globalaffairs.org",
    "coe_ai": "https://www.coe.int",
    "unesco_ai": "https://www.unesco.org",
    "uscbc": "https://www.uschina.org",
    "mckinsey_gi": "https://www.mckinsey.com",
    "oecd_ai": "https://oecd.ai",
    "lawfare": "https://www.lawfaremedia.org",
    "govai": "https://www.governance.ai",
}


def load_env(root: Path) -> dict:
    env: dict = {}
    dotenv = root / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def classify(text: str, content_type: str) -> str:
    head = text[:400].lower()
    if "<urlset" in head or "<sitemapindex" in head:
        return "sitemap"
    if "<rss" in head or "<feed" in head or "application/rss" in content_type or "atom" in content_type:
        return "rss"
    if "html" in content_type:
        return "html"
    return "?"


def probe(session: requests.Session, url: str) -> str:
    try:
        r = session.get(url, timeout=20, allow_redirects=True)
        kind = classify(r.text or "", r.headers.get("content-type", "").lower())
        note = ""
        if r.url.rstrip("/") != url.rstrip("/"):
            note = f" -> {r.url[:70]}"
        return f"{r.status_code} {kind}{note}"
    except requests.exceptions.Timeout:
        return "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}"


def main() -> int:
    env = load_env(ROOT)
    proxy = env.get("PROXY_PRIMARY") or env.get("PROXY", "")
    proxies = {"http": proxy, "https": proxy} if proxy.strip() else None
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "User-Agent": env.get("INGEST_USER_AGENT", DEFAULT_UA),
        "Accept": "application/rss+xml,application/atom+xml,application/xml,text/html;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if proxies:
        session.proxies.update(proxies)

    for sid, urls in CURATED.items():
        print(f"\n===== {sid} =====")
        for url in urls:
            print(f"  {probe(session, url):28s} {url}")
        root = DOMAIN_ROOT.get(sid)
        if root:
            for path in COMMON_PATHS:
                print(f"  {probe(session, root + path):28s} {root + path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
