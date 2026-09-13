"""Crawl4AI-backed browser renderer for the LLM-native ingestion path.

The public crawler is synchronous, while Crawl4AI is asynchronous.  This
adapter owns one browser on a dedicated event loop and exposes a small,
thread-safe synchronous API to the existing source worker pool.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse, urlunparse

from .. import config

logger = logging.getLogger("ingest.lnc")


class NativeCrawlError(RuntimeError):
    """A Crawl4AI page navigation or extraction failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class NativePage:
    url: str
    html: str
    markdown: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


def _proxy_config(value: str) -> dict[str, str] | None:
    """Convert a proxy URL without leaking credentials into the server field."""
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        return {"server": value}
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    result = {"server": urlunparse((parsed.scheme, host, "", "", "", ""))}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result


class Crawl4AIBackend:
    """One reusable headless browser shared by all configured source workers."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="crawl4ai-loop", daemon=True
        )
        self._crawler = None
        self._run_config = None
        self._lock = None
        self._closed = False
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        try:
            future.result(timeout=config.INGEST_LNC_START_TIMEOUT)
        except Exception:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()
            self._closed = True
            raise

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start(self) -> None:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
            from crawl4ai.async_configs import ProxyConfig
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        except ImportError as exc:
            raise RuntimeError(
                "未安装 Crawl4AI；请执行 pip install -r requirements.txt 和 crawl4ai-setup"
            ) from exc

        browser_config = BrowserConfig(
            browser_type="chromium",
            headless=True,
            verbose=False,
            user_agent=os.getenv("INGEST_USER_AGENT", ""),
            ignore_https_errors=False,
        )
        proxy_values = [config.PROXY_PRIMARY, config.PROXY_BACKUP]
        legacy_proxy = os.getenv("PROXY", "").strip()
        proxy_values.append(legacy_proxy)
        proxy_routes = []
        seen_routes: set[str] = set()
        for value in proxy_values:
            if value and value not in seen_routes:
                proxy_routes.append(ProxyConfig.from_dict(_proxy_config(value)))
                seen_routes.add(value)
        markdown_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=config.INGEST_LNC_PRUNING_THRESHOLD
            ),
            options={"ignore_links": False},
        )
        self._run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            check_robots_txt=True,
            word_count_threshold=10,
            excluded_tags=["nav", "footer", "aside", "form", "noscript"],
            exclude_external_links=True,
            remove_overlay_elements=True,
            process_iframes=False,
            wait_until="domcontentloaded",
            page_timeout=config.INGEST_LNC_PAGE_TIMEOUT_MS,
            delay_before_return_html=config.INGEST_LNC_RENDER_DELAY,
            scan_full_page=config.INGEST_LNC_SCAN_FULL_PAGE,
            markdown_generator=markdown_generator,
            proxy_config=proxy_routes or None,
            max_retries=1 if len(proxy_routes) > 1 else 0,
        )
        self._crawler = AsyncWebCrawler(
            config=browser_config,
            base_directory=str(config.DATA_DIR / "crawl4ai"),
        )
        await self._crawler.start()
        self._lock = asyncio.Lock()

    async def _crawl(self, url: str) -> NativePage:
        if self._crawler is None or self._run_config is None or self._lock is None:
            raise NativeCrawlError("Crawl4AI 浏览器尚未就绪")
        # AsyncWebCrawler is kept on one event loop and serialized because the
        # normal SDK instance is not documented as safe for overlapping arun calls.
        async with self._lock:
            result = await self._crawler.arun(url=url, config=self._run_config)
        status_code = getattr(result, "redirected_status_code", None) or getattr(
            result, "status_code", None
        )
        if not getattr(result, "success", False):
            raise NativeCrawlError(
                str(getattr(result, "error_message", "") or "Crawl4AI 抓取失败"),
                status_code=status_code,
            )
        markdown_result = getattr(result, "markdown", None)
        if isinstance(markdown_result, str):
            raw_markdown = markdown_result
            fit_markdown = ""
        else:
            raw_markdown = str(getattr(markdown_result, "raw_markdown", "") or "")
            fit_markdown = str(getattr(markdown_result, "fit_markdown", "") or "")
        markdown = fit_markdown if len(fit_markdown.strip()) >= 180 else raw_markdown
        final_url = str(
            getattr(result, "redirected_url", "")
            or getattr(result, "url", "")
            or url
        )
        html = str(
            getattr(result, "html", "")
            or getattr(result, "cleaned_html", "")
            or ""
        )
        return NativePage(
            url=final_url,
            html=html,
            markdown=markdown,
            status_code=status_code,
            headers=dict(getattr(result, "response_headers", None) or {}),
            metadata=dict(getattr(result, "metadata", None) or {}),
        )

    def crawl(self, url: str) -> NativePage:
        if self._closed:
            raise NativeCrawlError("Crawl4AI 浏览器已关闭")
        future = asyncio.run_coroutine_threadsafe(self._crawl(url), self._loop)
        timeout = config.INGEST_LNC_PAGE_TIMEOUT_MS / 1000 + 15
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            raise NativeCrawlError(f"Crawl4AI 超时：{url}") from exc

    async def _close(self) -> None:
        if self._crawler is not None:
            await self._crawler.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
            future.result(timeout=20)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 Crawl4AI 浏览器失败：%s", exc)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._loop.close()

    def __enter__(self) -> "Crawl4AIBackend":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
