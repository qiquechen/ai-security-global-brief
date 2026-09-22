"""批量探测本批新增来源的可用入口（2026-09-22 批次）。

用法：python scripts/probe_new_batch.py
输出：每个来源的首选可用入口与状态码；供写回 config/sources.json 用。
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# id: (中文名, 英文名, 地区, 类型, 候选入口[(kind, url), ...])
CANDIDATES: dict[str, tuple] = {
    # ---- 美国 ----
    "internetsociety": ("国际互联网协会", "Internet Society", "美国", "organization",
                        [("feed", "https://www.internetsociety.org/feed/"), ("page", "https://www.internetsociety.org/blog/")]),
    "world_bank": ("世界银行", "World Bank", "美国", "organization",
                   [("page", "https://www.worldbank.org/en/news/all?displayconttype_exact=Press+Release")]),
    "us_dhs": ("美国国土安全部", "U.S. Department of Homeland Security", "美国", "gov",
               [("feed", "https://www.dhs.gov/news-releases/feed"), ("page", "https://www.dhs.gov/news-releases")]),
    "gsa": ("美国总务管理局", "U.S. General Services Administration", "美国", "gov",
            [("page", "https://www.gsa.gov/about-us/newsroom/news-releases")]),
    "us_crs": ("美国国会研究服务部", "Congressional Research Service", "美国", "gov",
               [("page", "https://www.congress.gov/crs-products")]),
    "us_fcc": ("美国联邦通信委员会", "Federal Communications Commission", "美国", "gov",
               [("page", "https://www.fcc.gov/news-events/headlines")]),
    "solarium": ("美国网络空间日光浴委员会", "Cyberspace Solarium Commission", "美国", "gov_affiliated",
                 [("page", "https://www.cybersolarium.org/news")]),
    "gao": ("美国政府问责局", "U.S. Government Accountability Office", "美国", "gov",
            [("page", "https://www.gao.gov/press-releases")]),
    "cgd": ("美国全球发展中心", "Center for Global Development", "美国", "thinktank",
            [("page", "https://www.cgdev.org/topics/technology")]),
    "gmf": ("德国马歇尔基金会", "German Marshall Fund of the United States", "美国", "thinktank",
            [("page", "https://www.gmfus.org/news")]),
    "tpi": ("美国科技政策研究所", "Technology Policy Institute", "美国", "thinktank",
            [("feed", "https://techpolicyinstitute.org/feed/"), ("page", "https://techpolicyinstitute.org/blog/")]),
    "cdt": ("美国民主与科技中心", "Center for Democracy & Technology", "美国", "thinktank",
            [("feed", "https://cdt.org/feed/"), ("page", "https://cdt.org/insights/")]),
    "eff": ("美国电子前沿基金会", "Electronic Frontier Foundation", "美国", "organization",
            [("feed", "https://www.eff.org/rss/updates.xml"), ("page", "https://www.eff.org/deeplinks")]),
    "sia": ("美国半导体协会", "Semiconductor Industry Association", "美国", "organization",
            [("page", "https://www.semiconductors.org/category/news/")]),
    "ieee_spectrum_ai": ("IEEE《光谱》人工智能", "IEEE Spectrum – AI", "美国", "media",
                         [("feed", "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"),
                          ("page", "https://spectrum.ieee.org/topic/artificial-intelligence/")]),
    # ---- 英国 ----
    "uk_mod": ("英国国防部", "UK Ministry of Defence", "英国", "gov",
               [("page", "https://www.gov.uk/government/organisations/ministry-of-defence")]),
    "iiss": ("英国国际战略研究所", "International Institute for Strategic Studies", "英国", "thinktank",
             [("page", "https://www.iiss.org/publications/"), ("page", "https://www.iiss.org/online-analysis/")]),
    "chatham_house": ("查塔姆研究所", "Chatham House", "英国", "thinktank",
                      [("page", "https://www.chathamhouse.org/publications")]),
    "innovate_uk": ("英国创新署", "Innovate UK", "英国", "gov",
                    [("page", "https://www.ukri.org/news/")]),
    "isrm": ("英国战略风险管理研究所", "Institute of Strategic Risk Management", "英国", "thinktank",
             [("page", "https://www.isrm-uk.org/"), ("page", "https://www.strategic-risk-management.com/")]),
    # ---- 德国 ----
    "de_bmwk": ("德国联邦经济事务和气候行动部", "German Federal Ministry for Economic Affairs and Climate Action", "德国", "gov",
                [("page", "https://www.bmwk.de/Navigation/EN/Press/press.html")]),
    "bitkom": ("德国数字经济协会", "Bitkom", "德国", "organization",
               [("feed", "https://www.bitkom.org/RSS/Presse", ), ("page", "https://www.bitkom.org/Presse")]),
    "swp": ("德国科学与政治基金会", "German Institute for International and Security Affairs (SWP)", "德国", "thinktank",
            [("page", "https://www.swp-berlin.org/en/publications")]),
    "global_policy_forum": ("全球政策论坛", "Global Policy Forum", "德国", "organization",
                            [("page", "https://archive.globalpolicy.org/")]),
    # ---- 欧洲 / 联合国 ----
    "hcss": ("荷兰海牙战略研究中心", "The Hague Centre for Strategic Studies", "荷兰", "thinktank",
             [("page", "https://hcss.nl/publications/")]),
    "wef": ("世界经济论坛", "World Economic Forum", "瑞士", "organization",
            [("feed", "https://www.weforum.org/agenda/feed/"), ("page", "https://www.weforum.org/stories/")]),
    "un_news_ai": ("联合国新闻·人工智能", "UN News – Artificial Intelligence", "国际组织", "organization",
                   [("feed", "https://news.un.org/feed/subscribe/en/news/topic/ai/feed/rss.xml"),
                    ("page", "https://news.un.org/en/tags/artificial-intelligence")]),
    "unctad": ("联合国贸发会议", "UNCTAD", "国际组织", "organization",
               [("page", "https://unctad.org/news")]),
    "un_desa": ("联合国经济和社会事务部", "UN DESA", "国际组织", "organization",
                [("page", "https://www.un.org/development/desa/en/news/")]),
    "broadband_commission": ("联合国宽带数字发展委员会", "Broadband Commission for Sustainable Development", "国际组织", "organization",
                             [("page", "https://www.broadbandcommission.org/insights/")]),
    "ifri": ("法国国际关系研究所", "French Institute of International Relations", "法国", "thinktank",
             [("page", "https://www.ifri.org/en/publications")]),
    "ecipe": ("欧洲国际政治经济中心", "ECIPE", "欧盟", "thinktank",
              [("page", "https://ecipe.org/publications/")]),
    "ceps": ("欧洲政策研究中心", "CEPS", "欧盟", "thinktank",
             [("feed", "https://www.ceps.eu/feed/"), ("page", "https://www.ceps.eu/publications/")]),
    "epc": ("欧洲政策分析中心", "European Policy Centre", "欧盟", "thinktank",
            [("page", "https://www.epc.eu/en/publications")]),
    "bruegel": ("布鲁盖尔", "Bruegel", "欧盟", "thinktank",
                [("feed", "https://www.bruegel.org/rss.xml"), ("page", "https://www.bruegel.org/publications")]),
    "ecfr": ("欧洲外交关系协会", "European Council on Foreign Relations", "欧盟", "thinktank",
             [("page", "https://ecfr.eu/publications/")]),
    # ---- 其他地区 ----
    "imemo": ("俄科院世界经济与国际关系研究所", "IMEMO RAS", "俄罗斯", "thinktank",
              [("page", "https://www.imemo.ru/en/publications")]),
    "au_pmc": ("澳大利亚总理内阁部", "Australian Department of the Prime Minister and Cabinet", "澳大利亚", "gov",
               [("page", "https://www.pmc.gov.au/news")]),
    "accc": ("澳大利亚竞争和消费者委员会", "Australian Competition and Consumer Commission", "澳大利亚", "gov",
             [("page", "https://www.accc.gov.au/media-release")]),
    "aspi": ("澳大利亚战略政策研究所", "Australian Strategic Policy Institute", "澳大利亚", "thinktank",
             [("page", "https://www.aspi.org.au/report")]),
    "lowy": ("澳大利亚洛伊研究所", "Lowy Institute", "澳大利亚", "thinktank",
             [("feed", "https://www.lowyinstitute.org/the-interpreter/rss.xml"),
              ("page", "https://www.lowyinstitute.org/the-interpreter")]),
    "ictc": ("加拿大信息与通信技术委员会", "Information and Communications Technology Council", "加拿大", "organization",
             [("page", "https://www.ictc-ctic.ca/news/")]),
    "cigi": ("加拿大国际治理创新中心", "Centre for International Governance Innovation", "加拿大", "thinktank",
             [("page", "https://www.cigionline.org/publications/")]),
    "inss": ("以色列国家安全研究所", "Institute for National Security Studies", "以色列", "thinktank",
             [("page", "https://www.inss.org.il/publications/")]),
    "jp_soumu": ("日本总务省", "Japan Ministry of Internal Affairs and Communications", "日本", "gov",
                 [("page", "https://www.soumu.go.jp/menu_news/s-news/")]),
    "jp_mext": ("日本文部科学省", "Japan Ministry of Education, Culture, Sports, Science and Technology", "日本", "gov",
                [("page", "https://www.mext.go.jp/en/news/")]),
    "jp_it_hq": ("日本 IT 战略本部", "Japan IT Strategic Headquarters", "日本", "gov",
                 [("page", "https://www.kantei.go.jp/jp/singi/it2/")]),
    "nomura_ri": ("日本野村综合研究所", "Nomura Research Institute", "日本", "organization",
                  [("page", "https://www.nomuraresearch.com/")]),
    "jp_nisc": ("日本国家网络安全中心 NISC", "Japan National center of Incident readiness and Strategy for Cybersecurity", "日本", "gov",
                [("page", "https://www.nisc.go.jp/eng/")]),
    "kr_msit": ("韩国科学技术信息通信部", "Korea Ministry of Science and ICT", "韩国", "gov",
                [("page", "https://www.msit.go.kr/eng/")]),
    "stepi": ("韩国科技政策研究所", "Science and Technology Policy Institute", "韩国", "thinktank",
              [("page", "https://www.stepi.re.kr/")]),
    "fulcrum": ("新加坡 Fulcrum", "Fulcrum (ISEAS)", "新加坡", "thinktank",
                [("feed", "https://fulcrum.sg/feed/"), ("page", "https://fulcrum.sg/")]),
    "rsis": ("新加坡拉惹勒南国际研究院", "S. Rajaratnam School of International Studies", "新加坡", "thinktank",
             [("page", "https://www.rsis.edu.sg/rsis-publication/")]),
    "orf": ("印度观察家研究基金会", "Observer Research Foundation", "印度", "thinktank",
            [("page", "https://www.orfonline.org/research")]),
}


def probe(kind: str, url: str, session: requests.Session) -> str:
    try:
        r = session.get(url, timeout=12, allow_redirects=True)
        ct = (r.headers.get("content-type") or "").lower()
        body = (r.text or "")[:3000].lower()
        is_feed = ("xml" in ct) or body.lstrip().startswith("<?xml") or "<rss" in body or "<feed" in body
        flag = "FEED" if is_feed else "HTML"
        note = ""
        if r.url.rstrip("/") != url.rstrip("/"):
            note = f" -> {r.url[:60]}"
        return f"{r.status_code} {flag}{note}"
    except requests.exceptions.Timeout:
        return "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}"


def load_proxy() -> str:
    """按项目约定取代理：PROXY_PRIMARY > PROXY > PROXY_BACKUP。读项目根目录 .env。"""
    import os
    root = Path(__file__).resolve().parent.parent
    env = {}
    f = root / ".env"
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


def main() -> int:
    proxy = load_proxy()
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
        print(f"使用代理：{proxy}\n")
    else:
        print("未配置代理，直连探测\n")
    jobs = [(sid, kind, url) for sid, v in CANDIDATES.items() for kind, url in v[4]]
    results: dict[str, list] = {sid: [] for sid in CANDIDATES}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for (sid, kind, url), status in zip(jobs, ex.map(lambda j: probe(j[1], j[2], session), jobs)):
            results[sid].append((kind, url, status))
    ok, bad = [], []
    for sid, v in CANDIDATES.items():
        res = results[sid]
        good = [(k, u, s) for k, u, s in res if s.startswith("200")]
        line = f"{sid:22s} {v[0][:14]:16s}"
        if good:
            k, u, s = good[0]
            ok.append((sid, k, u))
            print(f"OK   {line} {k:5s} {s:14s} {u}")
        else:
            bad.append(sid)
            detail = " | ".join(f"{k}:{s}" for k, u, s in res)
            print(f"FAIL {line} {detail}")
    print(f"\n可用 {len(ok)} / 失败 {len(bad)} / 共 {len(CANDIDATES)}")
    if bad:
        print("失败清单:", ", ".join(bad))
    Path("/tmp/probe_ok.json").write_text(json.dumps(ok, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
