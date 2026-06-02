"""
JARVIS Browser Automation — skills/browser/browser.py
Full browser control via Playwright.
Supports: Chrome, Firefox, WebKit (Safari-engine)
Can: navigate, click, type, scrape, screenshot, fill forms,
     execute JS, handle logins, download files, watch for changes.
"""

import os
import re
import time
import asyncio
import logging
import base64
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger("jarvis.browser")


@dataclass
class PageInfo:
    url: str
    title: str
    text: str           # Visible text content
    links: list[dict]   # [{text, href}]
    screenshot: Optional[str] = None   # base64 PNG


@dataclass
class BrowserResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    screenshot: Optional[str] = None
    url: Optional[str] = None


class JarvisBrowser:
    """
    JARVIS Browser — full async Playwright wrapper.
    One persistent browser instance, multiple pages supported.
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.browser_type = cfg.get("browser", "chromium")   # chromium | firefox | webkit
        self.headless = cfg.get("headless", False)            # False = visible browser
        self.download_path = Path(cfg.get("download_path", "~/Downloads")).expanduser()
        self.timeout = cfg.get("timeout", 30000)              # ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self):
        """Launch the browser."""
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

            launcher = getattr(self._playwright, self.browser_type)
            self._browser = await launcher.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                accept_downloads=True,
            )
            self._context.set_default_timeout(self.timeout)
            self._page = await self._context.new_page()
            self._started = True
            logger.info(f"Browser started ({self.browser_type}, headless={self.headless})")
        except ImportError:
            raise RuntimeError("playwright not installed: pip install playwright && playwright install chromium")

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._started = False
        logger.info("Browser closed.")

    async def _ensure_started(self):
        if not self._started:
            await self.start()

    # ── Navigation ─────────────────────────────────────────

    async def goto(self, url: str, wait: str = "domcontentloaded") -> BrowserResult:
        """Navigate to a URL."""
        await self._ensure_started()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            await self._page.goto(url, wait_until=wait)
            await self._page.wait_for_load_state("networkidle", timeout=10000)
            return BrowserResult(success=True, url=self._page.url)
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    async def go_back(self):
        await self._page.go_back()

    async def go_forward(self):
        await self._page.go_forward()

    async def reload(self):
        await self._page.reload()

    # ── Reading ────────────────────────────────────────────

    async def get_page_info(self, screenshot: bool = False) -> PageInfo:
        """Extract all useful info from current page."""
        await self._ensure_started()

        title = await self._page.title()
        url = self._page.url

        # Clean visible text
        text = await self._page.evaluate("""() => {
            const body = document.body;
            const scripts = body.querySelectorAll('script, style, nav, footer, header');
            scripts.forEach(el => el.remove());
            return body.innerText.replace(/\\s+/g, ' ').trim().substring(0, 8000);
        }""")

        # Extract links
        links = await self._page.evaluate("""() =>
            [...document.querySelectorAll('a[href]')]
                .filter(a => a.innerText.trim())
                .slice(0, 30)
                .map(a => ({text: a.innerText.trim(), href: a.href}))
        """)

        ss = None
        if screenshot:
            ss = await self._screenshot_b64()

        return PageInfo(url=url, title=title, text=text, links=links, screenshot=ss)

    async def get_text(self, selector: str = "body") -> str:
        """Get text from a specific element."""
        try:
            return await self._page.inner_text(selector)
        except Exception:
            return ""

    async def get_html(self, selector: str = "body") -> str:
        """Get HTML of an element."""
        try:
            return await self._page.inner_html(selector)
        except Exception:
            return ""

    async def get_all_text(self) -> str:
        """Get cleaned full page text (good for LLM processing)."""
        info = await self.get_page_info()
        return f"Page: {info.title}\nURL: {info.url}\n\n{info.text}"

    # ── Interaction ────────────────────────────────────────

    async def click(self, selector: str) -> BrowserResult:
        """Click an element by CSS selector or text."""
        try:
            # Try CSS selector first, then text match
            if selector.startswith(("//", "text=", "role=")):
                await self._page.click(selector)
            else:
                try:
                    await self._page.click(selector)
                except Exception:
                    await self._page.get_by_text(selector).first.click()
            return BrowserResult(success=True)
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    async def type_into(self, selector: str, text: str, clear: bool = True) -> BrowserResult:
        """Type text into an input field."""
        try:
            await self._page.click(selector)
            if clear:
                await self._page.fill(selector, "")
            await self._page.type(selector, text, delay=30)
            return BrowserResult(success=True)
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    async def press(self, key: str):
        """Press a keyboard key (Enter, Tab, Escape, etc.)"""
        await self._page.keyboard.press(key)

    async def scroll(self, direction: str = "down", amount: int = 500):
        """Scroll the page."""
        delta = amount if direction == "down" else -amount
        await self._page.mouse.wheel(0, delta)

    async def hover(self, selector: str):
        await self._page.hover(selector)

    async def select_option(self, selector: str, value: str) -> BrowserResult:
        """Select a dropdown option."""
        try:
            await self._page.select_option(selector, value=value)
            return BrowserResult(success=True)
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    async def upload_file(self, selector: str, file_path: str) -> BrowserResult:
        """Upload a file via input[type=file]."""
        try:
            await self._page.set_input_files(selector, file_path)
            return BrowserResult(success=True)
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    # ── Smart web tasks ────────────────────────────────────

    async def web_search(self, query: str, engine: str = "google") -> PageInfo:
        """Open a search engine and get results."""
        urls = {
            "google": f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "bing":   f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            "duckduckgo": f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
        }
        await self.goto(urls.get(engine, urls["google"]))
        return await self.get_page_info()

    async def fill_form(self, fields: dict[str, str], submit_selector: str = None) -> BrowserResult:
        """
        Fill a form with field selector → value mapping.
        fields = {"#username": "alice", "#password": "secret"}
        """
        for selector, value in fields.items():
            result = await self.type_into(selector, value)
            if not result.success:
                return result
            await asyncio.sleep(0.1)
        if submit_selector:
            return await self.click(submit_selector)
        return BrowserResult(success=True)

    async def wait_for(self, selector: str = None, text: str = None, timeout: int = 10000) -> bool:
        """Wait for an element or text to appear."""
        try:
            if selector:
                await self._page.wait_for_selector(selector, timeout=timeout)
            elif text:
                await self._page.wait_for_function(
                    f"document.body.innerText.includes('{text}')", timeout=timeout
                )
            return True
        except Exception:
            return False

    async def scrape_table(self, selector: str = "table") -> list[dict]:
        """Extract a table as a list of dicts."""
        try:
            return await self._page.evaluate(f"""() => {{
                const table = document.querySelector('{selector}');
                if (!table) return [];
                const headers = [...table.querySelectorAll('th')].map(th => th.innerText.trim());
                return [...table.querySelectorAll('tbody tr')].map(row => {{
                    const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
                    return Object.fromEntries(headers.map((h, i) => [h, cells[i] || '']));
                }});
            }}""")
        except Exception:
            return []

    async def execute_js(self, script: str) -> Any:
        """Run arbitrary JavaScript on the page."""
        return await self._page.evaluate(script)

    # ── Screenshots ────────────────────────────────────────

    async def screenshot(self, path: str = None, full_page: bool = False) -> str:
        """Take a screenshot. Returns file path."""
        await self._ensure_started()
        if not path:
            path = str(Path.home() / f"Pictures/jarvis_browser_{int(time.time())}.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        await self._page.screenshot(path=path, full_page=full_page)
        return path

    async def _screenshot_b64(self) -> str:
        data = await self._page.screenshot(type="png")
        return base64.b64encode(data).decode()

    # ── Multi-tab ──────────────────────────────────────────

    async def new_tab(self, url: str = None) -> BrowserResult:
        """Open a new tab."""
        self._page = await self._context.new_page()
        if url:
            return await self.goto(url)
        return BrowserResult(success=True)

    async def close_tab(self):
        """Close current tab."""
        await self._page.close()
        pages = self._context.pages
        if pages:
            self._page = pages[-1]

    async def list_tabs(self) -> list[str]:
        return [p.url for p in self._context.pages]

    # ── Natural language command handler ───────────────────

    async def handle(self, command: str) -> str:
        """Parse and execute natural language browser commands."""
        cmd = command.lower().strip()

        if m := re.search(r"(?:go to|open|navigate to|visit)\s+([\w\-\.]+\.\w{2,}[\S]*)", cmd):
            result = await self.goto(m.group(1))
            return f"Opened {self._page.url}" if result.success else f"Failed: {result.error}"

        if m := re.search(r"search\s+(?:for\s+)?(.+?)(?:\s+on\s+(\w+))?$", cmd):
            query, engine = m.group(1), m.group(2) or "google"
            info = await self.web_search(query, engine)
            return f"Searched '{query}' on {engine}.\n\n{info.text[:1000]}"

        if re.search(r"screenshot|capture|snap", cmd):
            path = await self.screenshot()
            return f"Screenshot saved: {path}"

        if re.search(r"scroll down", cmd):
            await self.scroll("down")
            return "Scrolled down."

        if re.search(r"scroll up", cmd):
            await self.scroll("up")
            return "Scrolled up."

        if re.search(r"go back|back", cmd):
            await self.go_back()
            return "Went back."

        if re.search(r"close tab", cmd):
            await self.close_tab()
            return "Tab closed."

        if re.search(r"new tab", cmd):
            await self.new_tab()
            return "New tab opened."

        if re.search(r"read (?:the )?page|what(?:'s| is) on (?:the )?(?:screen|page)", cmd):
            info = await self.get_page_info()
            return f"{info.title}\n{info.url}\n\n{info.text[:2000]}"

        if m := re.search(r"click\s+(?:on\s+)?['\"]?(.+?)['\"]?$", cmd):
            result = await self.click(m.group(1))
            return "Clicked." if result.success else f"Could not click: {result.error}"

        if m := re.search(r"type\s+['\"](.+?)['\"]", cmd):
            await self._page.keyboard.type(m.group(1))
            return f"Typed: {m.group(1)}"

        return None   # Not a browser command
