"""合规、增量的 RSS/Sitemap/栏目页采集器。"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from .. import config
from .lnc import NativePage
from .models import Article, Candidate, CrawlStats, Source

logger = logging.getLogger("ingest")

TRACKING_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "at_medium", "at_campaign", "module", "pgtype",
}
DATE_META_KEYS = (
    "article:published_time", "datepublished", "date", "dc.date",
    "dcterms.date", "parsely-pub-date", "pubdate",
)
# 明显不属于文章网页的响应类型；其余类型交给 trafilatura/BeautifulSoup 尝试。
_NON_HTML_TYPES = (
    "image/", "video/", "audio/", "font/",
    "application/pdf", "application/zip", "application/gzip",
    "application/x-rar-compressed", "application/x-7z-compressed",
    "application/msword", "application/vnd", "application/octet-stream",
)
# 站点导航/账号类页面，不是文章也不应消耗候选名额（常见于 WP/CDN 站）。
_UTILITY_PATH_MARKERS = (
    "/cdn-cgi/", "/my-account", "/login", "/sign-in", "/signin",
    "/register", "/cart", "/checkout", "/renew-membership",
)
_AI_TERMS = re.compile(
    r"\b(?:ai|artificial intelligence|machine learning|large language models?|llms?|"
    r"generative ai|deepfakes?|algorithms?|chatbots?|autonomous systems?)\b|"
    r"人工智能|机器学习|大模型|深度伪造|算法|自动驾驶",
    re.IGNORECASE,
)
_GOVERNANCE_TERMS = re.compile(
    r"\b(?:security|safety|risks?|regulat(?:ion|or|ory)|governance|policy|laws?|"
    r"privacy|surveillance|cyber|military|defen[cs]e|weapons?|export controls?|"
    r"semiconductors?|chips?|compute|copyright|disinformation|elections?|bias|"
    r"ethics?|standards?|evaluations?|critical infrastructure)\b|"
    r"安全|风险|监管|治理|政策|法律|隐私|网络|国防|武器|出口管制|芯片|算力|"
    r"版权|虚假信息|选举|偏见|伦理|标准|测评|关键基础设施",
    re.IGNORECASE,
)


class FetchError(RuntimeError):
    """可记录、可隔离的来源抓取错误。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RequestCoordinator:
    """在并行来源间共享域名限速与 robots 缓存。"""

    def __init__(self) -> None:
        self.robots: dict[str, RobotFileParser | None] = {}
        self.slots: dict[str, float] = {}
        self.route_index = 0
        self.lock = threading.Lock()


def candidate_relevance_score(title: str, url: str) -> int:
    """用于排列抓取顺序的轻量相关度；只排序，不删除候选。"""
    value = f"{title} {urlparse(url).path.replace('-', ' ')}"
    ai = bool(_AI_TERMS.search(value))
    governance = bool(_GOVERNANCE_TERMS.search(value))
    if ai and governance:
        return 6
    if ai:
        return 4
    if governance:
        return 2
    return 0


def canonicalize_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), "")
    )


def parse_datetime(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    cleaned = str(value).strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(cleaned)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        pass
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _domain_allowed(hostname: str | None, domains: tuple[str, ...]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in domains)


def _is_non_html(content_type: str) -> bool:
    content_type = (content_type or "").split(";")[0].strip().lower()
    return bool(content_type) and (
        content_type.startswith(_NON_HTML_TYPES) or content_type == "application/json"
    )


def url_allowed(source: Source, value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not _domain_allowed(parsed.hostname, source.allowed_domains):
        return False
    # 带业务参数的列表/搜索页通常不是稳定文章地址；仅跟踪参数可被规范化后放行。
    canonical = canonicalize_url(value)
    if urlparse(canonical).query and canonical != canonicalize_url(source.url):
        return False
    path = parsed.path or "/"
    normalized_path = path.rstrip("/") or "/"
    if normalized_path in source.exclude_exact_paths:
        return False
    if any(marker in path for marker in source.exclude_patterns):
        return False
    if source.include_patterns and not any(marker in path for marker in source.include_patterns):
        return canonicalize_url(value) == canonicalize_url(source.url)
    if (
        source.article_url_pattern
        and canonicalize_url(value) != canonicalize_url(source.url)
        and re.search(source.article_url_pattern, path) is None
    ):
        return False
    return True


def load_sources(path: Path) -> list[Source]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    values = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(values, list) or not values:
        raise ValueError("config/sources.json 必须包含非空 sources 数组")
    result: list[Source] = []
    seen: set[str] = set()
    for item in values:
        source_id = str(item.get("id", "")).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", source_id):
            raise ValueError(f"来源 id 无效：{source_id!r}")
        if source_id in seen:
            raise ValueError(f"来源 id 重复：{source_id}")
        seen.add(source_id)
        method = str(item.get("method", "page"))
        if method not in {"rss", "sitemap", "page"}:
            raise ValueError(f"{source_id} method 无效：{method}")
        lnc_mode = str(item.get("lnc_mode", "off")).strip().lower()
        if lnc_mode not in {"off", "fallback", "always"}:
            raise ValueError(f"{source_id} lnc_mode 无效：{lnc_mode}")
        if lnc_mode == "always" and method != "page":
            raise ValueError(f"{source_id} 只有 page 来源可使用 lnc_mode=always")
        article_url_pattern = str(item.get("article_url_pattern", "")).strip()
        if article_url_pattern:
            try:
                re.compile(article_url_pattern)
            except re.error as exc:
                raise ValueError(f"{source_id} article_url_pattern 无效：{exc}") from exc
        homepage = str(item.get("homepage", "")).strip()
        feed = str(item.get("feed", "")).strip()
        entry_url = str(item.get("url", "")).strip()
        if not entry_url:
            entry_url = feed if method in {"rss", "sitemap"} and feed else homepage
        parsed_urls = [urlparse(value) for value in (homepage, entry_url, feed) if value]
        if not homepage or not entry_url or any(p.scheme not in {"http", "https"} or not p.hostname for p in parsed_urls):
            raise ValueError(f"{source_id} 缺少有效 homepage/url/feed")
        domains = tuple(
            str(value).lower().strip().rstrip(".")
            for value in item.get("allowed_domains", [])
            if str(value).strip()
        )
        if not domains:
            domains = tuple(dict.fromkeys(p.hostname.lower() for p in parsed_urls if p.hostname))
        if not all(_domain_allowed(p.hostname, domains) for p in parsed_urls):
            raise ValueError(f"{source_id} 入口 URL 超出 allowed_domains")
        result.append(
            Source(
                id=source_id,
                name=str(item.get("name", source_id)),
                country=str(item.get("country", "")),
                type=str(item.get("type", "")),
                method=method,
                homepage=homepage,
                url=entry_url,
                feed=feed,
                enabled=bool(item.get("enabled", True)),
                name_en=str(item.get("name_en", "")),
                category_hint=str(item.get("category_hint", "")),
                allowed_domains=domains,
                include_patterns=tuple(str(x) for x in item.get("include_patterns", [])),
                exclude_patterns=tuple(str(x) for x in item.get("exclude_patterns", [])),
                exclude_exact_paths=tuple(
                    str(x).rstrip("/") or "/" for x in item.get("exclude_exact_paths", [])
                ),
                max_candidates=max(1, int(item.get("max_candidates", 20))),
                use_discovered_feed=bool(item.get("use_discovered_feed", False)),
                priority=int(item.get("priority", 50)),
                lnc_mode=lnc_mode,
                article_url_pattern=article_url_pattern,
            )
        )
    return result


class Fetcher:
    """带代理、域名限制、robots、限速、重试和大小限制的客户端。"""

    def __init__(self, coordinator: RequestCoordinator | None = None) -> None:
        self.coordinator = coordinator or RequestCoordinator()
        self.session = requests.Session()
        # 只采用项目显式配置的线路，避免系统代理悄悄改变抓取路径。
        self.session.trust_env = False
        self.user_agent = os.getenv(
            "INGEST_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        routes = [
            ("主线路", config.PROXY_PRIMARY),
            ("备用线路", config.PROXY_BACKUP),
        ]
        # 旧配置名 PROXY 作为最后一级兜底：即使 PROXY_PRIMARY 已配置，只要它
        # 与主备都不相同仍会保留，避免 .env 迁移时主线路失效导致整次采集失败。
        legacy = os.getenv("PROXY", "").strip()
        routes.append(("旧PROXY", legacy))
        self._routes = []
        seen_routes: set[str] = set()
        for name, proxy in routes:
            if proxy and proxy not in seen_routes:
                self._routes.append((name, proxy))
                seen_routes.add(proxy)
        if not self._routes:
            self._routes.append(("直连", ""))
        self._route_index = min(self.coordinator.route_index, len(self._routes) - 1)
        self._apply_route(self._route_index)
        self.timeout = float(os.getenv("INGEST_TIMEOUT", "25"))
        self.max_bytes = int(os.getenv("INGEST_MAX_RESPONSE_BYTES", str(4 * 1024 * 1024)))
        self.retries = max(0, int(os.getenv("INGEST_RETRIES", "2")))
        self.interval = max(0.0, float(os.getenv("INGEST_REQUEST_INTERVAL", "0.3")))
        self.respect_robots = os.getenv("INGEST_RESPECT_ROBOTS", "true").lower() not in {"0", "false", "no"}
        self.robots_fail_closed = os.getenv("INGEST_ROBOTS_FAIL_CLOSED", "false").lower() not in {"0", "false", "no"}

    @property
    def active_route_name(self) -> str:
        return self._routes[self._route_index][0]

    def _apply_route(self, index: int) -> None:
        self._route_index = index
        _name, proxy = self._routes[index]
        self.session.proxies.clear()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def _probe_route(self, index: int) -> tuple[bool, str]:
        """在不改变当前线路的前提下验证候选线路是否真的可用。"""
        target = config.INGEST_CONNECTIVITY_TEST_URL
        if not target:
            return True, ""
        previous_index = self._route_index
        try:
            self._apply_route(index)
            response = self.session.get(
                target,
                timeout=min(self.timeout, 10.0),
                allow_redirects=True,
            )
            if response.status_code < 500:
                return True, ""
            return False, f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            return False, exc.__class__.__name__
        finally:
            self._apply_route(previous_index)

    def _switch_to_backup(self) -> bool:
        """代理自身故障时切到一条已探测可用的其他线路。

        从当前线路之后开始并允许环回主线路。这样备用线路中途失效时不会
        把共享协调器永久卡在备用线路上。
        """
        previous_index = self._route_index
        indexes = list(range(previous_index + 1, len(self._routes)))
        indexes.extend(range(0, previous_index))
        for next_index in indexes:
            healthy, reason = self._probe_route(next_index)
            if not healthy:
                logger.warning(
                    "候选网络线路不可用：%s (%s)",
                    self._routes[next_index][0], reason,
                )
                continue
            previous = self.active_route_name
            self._apply_route(next_index)
            with self.coordinator.lock:
                self.coordinator.route_index = next_index
            logger.warning("网络线路故障：%s → %s", previous, self.active_route_name)
            return True
        return False

    def _route_failure_confirmed(self, exc: Exception | None = None) -> bool:
        """确认故障来自当前代理线路，而不是单个目标站点。

        本地代理核心仍在监听时，出口节点故障可能表现为 ConnectionError、
        Timeout，甚至由代理返回 502/503/504。除明确的 ProxyError 外，先用
        独立连通性地址探测当前线路，探测失败才允许全局切线。
        """
        if isinstance(exc, requests.exceptions.ProxyError):
            return True
        healthy, reason = self._probe_route(self._route_index)
        if healthy:
            return False
        logger.warning("当前网络线路确认不可用：%s (%s)", self.active_route_name, reason)
        return True

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """4xx 是站点/风控给出的确定性结果，重试没有意义且会拖慢整体采集；
        只对连接、代理、超时、429 与 5xx 做退避重试。"""
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status is not None and (status >= 500 or status == 429)

    def _get_with_failover(self, value: str, **kwargs) -> requests.Response:
        """执行一次 GET；线路探测确认故障后再切换并重试一次。"""
        try:
            response = self.session.get(value, **kwargs)
        except requests.RequestException as exc:
            route_related = isinstance(
                exc,
                (
                    requests.exceptions.ProxyError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                ),
            )
            if route_related and self._route_failure_confirmed(exc) and self._switch_to_backup():
                return self.session.get(value, **kwargs)
            raise
        if response.status_code in {502, 503, 504}:
            if self._route_failure_confirmed() and self._switch_to_backup():
                return self.session.get(value, **kwargs)
        return response

    def check_connectivity(self, value: str | None = None) -> str:
        """依次探测主备线路，返回首条可用线路名称。"""
        target = value or config.INGEST_CONNECTIVITY_TEST_URL
        if not target:
            return self.active_route_name
        errors: list[str] = []
        available: list[int] = []
        for index, (name, _proxy) in enumerate(self._routes):
            self._apply_route(index)
            try:
                response = self.session.get(
                    target,
                    timeout=min(self.timeout, 10.0),
                    allow_redirects=True,
                )
                if response.status_code < 500:
                    available.append(index)
                    continue
                errors.append(f"{name}=HTTP {response.status_code}")
            except requests.RequestException as exc:
                errors.append(f"{name}={exc.__class__.__name__}")
        if not available:
            self._apply_route(0)
            raise FetchError("主备网络线路均不可用：" + ", ".join(errors))
        selected = available[0]
        self._apply_route(selected)
        with self.coordinator.lock:
            self.coordinator.route_index = selected
        if errors:
            logger.warning("部分网络线路不可用：%s", ", ".join(errors))
        logger.info(
            "网络连通性正常：%s（可用%d/%d）",
            self.active_route_name, len(available), len(self._routes),
        )
        return self.active_route_name

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _wait_slot(self, value: str) -> None:
        parsed = urlparse(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = time.monotonic()
        with self.coordinator.lock:
            scheduled = max(now, self.coordinator.slots.get(origin, 0.0))
            self.coordinator.slots[origin] = scheduled + self.interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)

    def _load_robots(self, value: str) -> RobotFileParser | None:
        parsed = urlparse(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self.coordinator.lock:
            if origin in self.coordinator.robots:
                return self.coordinator.robots[origin]
        robots_url = urljoin(origin + "/", "robots.txt")
        try:
            self._wait_slot(robots_url)
            response = self._get_with_failover(robots_url, timeout=self.timeout)
            if response.status_code == 404:
                parser = RobotFileParser()
                parser.parse([])
            else:
                response.raise_for_status()
                parser = RobotFileParser(robots_url)
                parser.parse(response.text.splitlines())
            with self.coordinator.lock:
                self.coordinator.robots[origin] = parser
        except requests.RequestException as exc:
            logger.warning("robots.txt 不可用：%s (%s)", robots_url, exc)
            with self.coordinator.lock:
                self.coordinator.robots[origin] = None
        return self.coordinator.robots[origin]

    def fetch(self, source: Source, value: str, *, check_robots: bool = True) -> requests.Response:
        if not _domain_allowed(urlparse(value).hostname, source.allowed_domains):
            raise FetchError(f"URL 超出来源域名白名单：{value}")
        if check_robots and self.respect_robots:
            parser = self._load_robots(value)
            if parser is None and self.robots_fail_closed:
                raise FetchError(f"robots.txt 不可用，按 fail-closed 跳过：{value}")
            if parser is not None and not parser.can_fetch(self.user_agent, value):
                raise FetchError(f"robots.txt 不允许抓取：{value}")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                self._wait_slot(value)
                response = self._get_with_failover(
                    value, timeout=self.timeout, allow_redirects=True
                )
                # 少数站点把目录式文章 URL 的末尾斜杠作为必需路由（如 CBS）。
                parsed_value = urlparse(value)
                if response.status_code == 406 and not parsed_value.path.endswith("/"):
                    slash_url = urlunparse(parsed_value._replace(path=parsed_value.path + "/"))
                    self._wait_slot(slash_url)
                    response = self._get_with_failover(
                        slash_url, timeout=self.timeout, allow_redirects=True
                    )
                response.raise_for_status()
                if not _domain_allowed(urlparse(response.url).hostname, source.allowed_domains):
                    raise FetchError(f"重定向超出来源域名白名单：{response.url}")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise FetchError(f"响应超过大小限制：{declared}")
                if len(response.content) > self.max_bytes:
                    raise FetchError(f"响应超过大小限制：{len(response.content)}")
                return response
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                if isinstance(exc, FetchError) or not self._is_retryable(exc):
                    break
                if attempt >= self.retries:
                    break
                delay = min(8.0, 0.75 * (2**attempt))
                logger.warning("抓取失败，%.2f 秒后重试：%s (%s)", delay, value, exc)
                time.sleep(delay)
        status = getattr(getattr(last_error, "response", None), "status_code", None)
        raise FetchError(f"抓取失败：{value}；{last_error}", status_code=status)


def _feed_candidates(content: bytes, base_url: str) -> list[Candidate]:
    parsed = feedparser.parse(content)
    result: list[Candidate] = []
    for entry in parsed.entries:
        link = urljoin(base_url, str(entry.get("link", "")))
        if not link:
            continue
        structured = entry.get("published_parsed") or entry.get("updated_parsed")
        published = None
        if structured:
            try:
                published = datetime.fromtimestamp(calendar.timegm(structured), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                pass
        title = str(entry.get("title", ""))
        result.append(Candidate(
            link, title, published,
            relevance_score=candidate_relevance_score(title, link) + 2,
        ))
    return result


def _sitemap_candidates(content: bytes, base_url: str) -> tuple[list[Candidate], list[str]]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return [], []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def child_text(node, names: set[str]) -> str:
        for child in node.iter():
            if local(child.tag) in names and child.text:
                return child.text.strip()
        return ""

    is_index = local(root.tag) == "sitemapindex"
    candidates: list[Candidate] = []
    children: list[str] = []
    for node in root:
        location = child_text(node, {"loc"})
        if not location:
            continue
        location = urljoin(base_url, location)
        if is_index or local(node.tag) == "sitemap":
            children.append(location)
        else:
            candidates.append(Candidate(
                location,
                published_hint=parse_datetime(child_text(node, {"lastmod"})),
                relevance_score=candidate_relevance_score("", location),
            ))
    return candidates, children


def _page_candidates(html: str, base_url: str) -> tuple[list[Candidate], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    feeds: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        content_type = str(link.get("type", "")).lower()
        if "alternate" in rel and ("rss" in content_type or "atom" in content_type):
            feeds.append(urljoin(base_url, link["href"]))
    result: list[Candidate] = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, str(anchor["href"]))
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"}:
            continue
        path_lower = (parsed_url.path or "/").lower()
        if any(marker in path_lower for marker in _UTILITY_PATH_MARKERS):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title and anchor.find("time") is None:
            continue
        published = None
        # 栏目卡片常有多层 div；只看最近一层会漏掉外层卡片上的发布时间。
        # 最多向上检查四层，避免误取整页其他文章的日期。
        parent = anchor
        for _depth in range(4):
            parent = parent.parent
            if parent is None or getattr(parent, "name", None) in {"main", "body", "html"}:
                break
            if getattr(parent, "name", None) not in {"article", "li", "section", "div"}:
                continue
            time_tag = parent.find("time")
            if time_tag is not None:
                published = parse_datetime(
                    time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
                )
                if published:
                    break
        if published is None:
            match = re.search(
                r"/(20\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$)",
                parsed_url.path,
            )
            if match:
                try:
                    published = datetime(
                        *(int(value) for value in match.groups()), tzinfo=UTC
                    )
                except ValueError:
                    pass
        structure_score = 3 if anchor.find_parent("article") else 0
        if anchor.find_parent(["h1", "h2", "h3"]):
            structure_score = max(structure_score, 2)
        if published:
            structure_score += 1
        result.append(Candidate(
            url, title, published,
            relevance_score=candidate_relevance_score(title, url) + structure_score,
        ))
    return result, list(dict.fromkeys(feeds))


def _native_page_candidates(source: Source, native_crawler) -> list[Candidate]:
    page = native_crawler.crawl(source.url)
    links, _feeds = _page_candidates(page.html, page.url)
    return links


def discover(source: Source, fetcher: Fetcher, native_crawler=None) -> list[Candidate]:
    if native_crawler is not None and source.lnc_mode == "always":
        return _native_page_candidates(source, native_crawler)
    try:
        response = fetcher.fetch(source, source.url)
    except Exception:
        if native_crawler is not None and source.lnc_mode == "fallback" and source.method == "page":
            logger.info("%s 静态入口失败，改用 Crawl4AI 渲染", source.id)
            return _native_page_candidates(source, native_crawler)
        raise
    content_type = response.headers.get("content-type", "").lower()
    if source.method == "rss" or "rss" in content_type or "atom" in content_type:
        return _feed_candidates(response.content, response.url)
    if source.method == "sitemap" or "sitemap" in response.url.lower():
        candidates, children = _sitemap_candidates(response.content, response.url)
        for child_url in children[:5]:
            if not _domain_allowed(urlparse(child_url).hostname, source.allowed_domains):
                continue
            try:
                child = fetcher.fetch(source, child_url)
                nested, _ = _sitemap_candidates(child.content, child.url)
                candidates.extend(nested)
            except FetchError as exc:
                logger.warning("子 sitemap 失败：%s", exc)
        return candidates
    links, feed_urls = _page_candidates(response.text, response.url)
    if source.use_discovered_feed:
        for feed_url in feed_urls[:1]:
            if not _domain_allowed(urlparse(feed_url).hostname, source.allowed_domains):
                continue
            try:
                feed = fetcher.fetch(source, feed_url)
                links = _feed_candidates(feed.content, feed.url) + links
            except FetchError as exc:
                logger.warning("自动发现 feed 失败：%s", exc)
    if native_crawler is not None and source.lnc_mode == "fallback" and len(links) < 3:
        try:
            rendered = _native_page_candidates(source, native_crawler)
            links = rendered + links
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s Crawl4AI 入口增强失败：%s", source.id, exc)
    return links


def _json_ld_metadata(soup: BeautifulSoup) -> tuple[str, str]:
    queue: list[object] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(script.string or script.get_text() or "")
            queue.extend(value if isinstance(value, list) else [value])
        except (json.JSONDecodeError, TypeError):
            continue
    best: tuple[int, str, str] = (-1, "", "")
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if isinstance(graph, list):
            queue.extend(graph)
        for key in ("mainEntity", "mainEntityOfPage"):
            nested = item.get(key)
            if isinstance(nested, dict):
                queue.append(nested)
        title = item.get("headline") or item.get("name") or ""
        date = item.get("datePublished") or ""
        raw_types = item.get("@type", "")
        types = raw_types if isinstance(raw_types, list) else [raw_types]
        is_article = any("article" in str(value).lower() for value in types)
        score = (4 if is_article else 0) + (2 if date else 0) + (1 if title else 0)
        if score > best[0]:
            best = (score, str(title), str(date))
    return best[1], best[2]


def _extract_article(response: requests.Response, candidate: Candidate) -> tuple[str, str, datetime | None, str]:
    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    json_title, json_date = _json_ld_metadata(soup)
    title = ""
    published = None
    canonical = response.url
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical_tag and canonical_tag.get("href"):
        canonical = urljoin(response.url, str(canonical_tag["href"]))
    if published is None:
        for meta in soup.find_all("meta"):
            key = str(meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
            if key in DATE_META_KEYS:
                published = parse_datetime(meta.get("content"))
                if published:
                    break
    if published is None:
        published = parse_datetime(json_date)
    # Anthropic文章没有日期meta；只读取文章页头日期，避免正文引用年份被猜成发布日期。
    if published is None and urlparse(response.url).hostname in {"www.anthropic.com", "anthropic.com"}:
        date_node = soup.select_one('article [class*="PostDetail"][class*="header"] .agate')
        if date_node:
            published = parse_datetime(date_node.get_text(" ", strip=True))
    text = ""
    try:
        text = trafilatura.extract(
            html,
            url=response.url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            output_format="txt",
        ) or ""
        metadata = trafilatura.extract_metadata(html, default_url=response.url)
        if metadata is not None:
            title = getattr(metadata, "title", "") or ""
            if published is None:
                published = parse_datetime(getattr(metadata, "date", "") or "")
            canonical = getattr(metadata, "url", "") or canonical
    except Exception as exc:  # noqa: BLE001
        logger.debug("trafilatura 解析失败：%s", exc)
    if not title:
        title_meta = soup.find("meta", property="og:title")
        title = (
            str(title_meta.get("content", "")) if title_meta else ""
        ) or json_title or candidate.title_hint or (soup.title.get_text(" ", strip=True) if soup.title else "")
    if not text:
        for node in soup(["script", "style", "noscript", "nav", "footer", "aside", "svg"]):
            node.decompose()
        main = soup.find("article") or soup.find("main") or soup.body
        text = main.get_text("\n", strip=True) if main else ""
    if published is None:
        published = candidate.published_hint
    if published is None:
        match = re.search(r"/(20\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$)", response.url)
        if match:
            try:
                published = datetime(*(int(value) for value in match.groups()), tzinfo=UTC)
            except ValueError:
                pass
    title = " ".join(title.split()).strip()
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    return title, text, published, canonical


def _extract_native_article(
    page: NativePage, candidate: Candidate
) -> tuple[str, str, datetime | None, str]:
    """Keep HTML metadata/date parsing but prefer Crawl4AI's pruned Markdown body."""
    response = type("NativeResponse", (), {"text": page.html, "url": page.url})()
    title, static_text, published, canonical = _extract_article(response, candidate)
    metadata = page.metadata
    if not title:
        title = str(metadata.get("title") or metadata.get("og:title") or "").strip()
    if published is None:
        for key in (
            "datePublished", "date_published", "published_time", "publication_date", "date"
        ):
            published = parse_datetime(str(metadata.get(key) or ""))
            if published is not None:
                break
    markdown = "\n".join(
        line.rstrip() for line in page.markdown.splitlines() if line.strip()
    ).strip()
    text = markdown if len(markdown) >= 180 else static_text
    return title, text, published, canonical or page.url


def crawl_source(
    source: Source,
    fetcher: Fetcher,
    start: datetime,
    end: datetime,
    existing_urls: set[str],
    max_items: int | None = None,
    native_crawler=None,
) -> tuple[CrawlStats, list[Article]]:
    stats = CrawlStats(source.id)
    try:
        raw_candidates = discover(source, fetcher, native_crawler=native_crawler)
    except Exception as exc:  # noqa: BLE001
        stats.errors.append(f"入口发现失败：{exc}")
        return stats, []
    stats.raw_discovered = len(raw_candidates)
    limit = min(source.max_candidates, max_items) if max_items else source.max_candidates
    by_url: dict[str, Candidate] = {}
    rejected = getattr(existing_urls, "rejected", set())
    known_dates = getattr(existing_urls, "published_dates", {})
    skipped_rejections = set()
    entry = canonicalize_url(source.url)
    for candidate in raw_candidates:
        url = canonicalize_url(candidate.url)
        if not url or url == entry or not url_allowed(source, url):
            stats.url_filtered += 1
            continue
        if candidate.published_hint and not (start <= candidate.published_hint < end):
            stats.hint_outside += 1
            stats.observed_dates[url] = candidate.published_hint.replace(
                microsecond=0
            ).isoformat()
            continue
        if url in rejected:
            skipped_rejections.add(url)
            stats.blacklisted = len(skipped_rejections)
            continue
        if url in existing_urls:
            stats.existing += 1
            continue
        cached_published = parse_datetime(known_dates.get(url))
        if (
            candidate.published_hint is None
            and cached_published is not None
            and not (start <= cached_published < end)
        ):
            stats.cached_outside += 1
            continue
        normalized = Candidate(
            url, candidate.title_hint,
            candidate.published_hint or cached_published,
            candidate.relevance_score or candidate_relevance_score(candidate.title_hint, url),
        )
        previous = by_url.get(url)
        if previous is None or (
            normalized.relevance_score,
            bool(normalized.published_hint),
            len(normalized.title_hint),
        ) > (
            previous.relevance_score,
            bool(previous.published_hint),
            len(previous.title_hint),
        ):
            by_url[url] = normalized
    stats.eligible = len(by_url)
    candidates = sorted(
        by_url.values(),
        key=lambda item: (
            bool(item.published_hint),
            item.published_hint.timestamp() if item.published_hint else 0,
            item.relevance_score,
        ),
        reverse=True,
    )[:limit]
    stats.discovered = len(candidates)
    result: list[Article] = []
    denied_in_a_row = 0
    for candidate in candidates:
        try:
            stats.fetched += 1
            response = None
            primary_error: Exception | None = None
            title = text = canonical = ""
            published = None
            native_page_url = ""
            if source.lnc_mode != "always" or native_crawler is None:
                try:
                    response = fetcher.fetch(source, candidate.url)
                    if not _is_non_html(response.headers.get("content-type", "")):
                        title, text, published, canonical = _extract_article(response, candidate)
                except Exception as exc:  # noqa: BLE001
                    primary_error = exc
            needs_native = (
                native_crawler is not None
                and source.lnc_mode in {"fallback", "always"}
                and (source.lnc_mode == "always" or primary_error is not None or len(text) < 180)
            )
            if needs_native:
                stats.lnc_attempted += 1
                try:
                    page = native_crawler.crawl(candidate.url)
                    native_page_url = page.url
                    native_result = _extract_native_article(page, candidate)
                    if len(native_result[1]) >= 180:
                        title, text, published, canonical = native_result
                        stats.lnc_succeeded += 1
                        if primary_error is not None or response is not None:
                            stats.lnc_recovered += 1
                        primary_error = None
                except Exception as native_error:  # noqa: BLE001
                    logger.warning("%s Crawl4AI 候选失败：%s", source.id, native_error)
                    if not text:
                        primary_error = primary_error or native_error
            if primary_error is not None:
                raise primary_error
            if response is not None and _is_non_html(response.headers.get("content-type", "")) and not text:
                stats.empty_text += 1
                continue
            final_url = native_page_url or (
                response.url if response is not None else candidate.url
            )
            canonical = canonicalize_url(canonical or final_url)
            if not url_allowed(source, canonical):
                canonical = canonicalize_url(final_url)
            if published is not None:
                published = published.astimezone(UTC)
                published_iso = published.replace(microsecond=0).isoformat()
                stats.observed_dates[candidate.url] = published_iso
                stats.observed_dates[canonical] = published_iso
            if canonical in rejected:
                skipped_rejections.add(canonical)
                stats.blacklisted = len(skipped_rejections)
                continue
            if canonical in existing_urls:
                stats.existing += 1
                continue
            if published is None:
                stats.missing_time += 1
                continue
            if not (start <= published < end):
                stats.outside_window += 1
                continue
            if len(title) < 4 or len(text) < 180:
                stats.empty_text += 1
                continue
            result.append(
                Article(
                    source_id=source.id,
                    url=canonical,
                    title=title,
                    published_at=published.replace(microsecond=0).isoformat(),
                    crawled_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                    text=text,
                    dedup_hash=hashlib.sha256(
                        " ".join(text.lower().split()).encode("utf-8")
                    ).hexdigest(),
                )
            )
            stats.accepted += 1
            denied_in_a_row = 0
        except Exception as exc:  # noqa: BLE001
            if len(stats.errors) < 100:
                stats.errors.append(f"{candidate.url}：{exc}")
            logger.warning("%s 候选失败：%s", source.id, exc)
            if isinstance(exc, FetchError) and exc.status_code in {401, 403}:
                denied_in_a_row += 1
                if denied_in_a_row >= 3:
                    stats.errors.append("连续3个候选被拒绝，提前停止该来源")
                    break
            else:
                denied_in_a_row = 0
    return stats, result
