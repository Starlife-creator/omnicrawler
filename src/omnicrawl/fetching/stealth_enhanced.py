"""反检测增强 — 指纹随机化 + 代理轮换 + 人类行为模拟。

功能:
    - 浏览器指纹随机化（User-Agent / Viewport / WebGL / Canvas / 字体 / 时区 / 语言）
    - 代理轮换策略（轮询 / 随机 / 加权 / 按域绑定）
    - 人类行为模拟（鼠标移动 / 随机延迟 / 滚动模式 / 打字速度）
    - 自动集成到 BrowserFetcher

用法:
    from omnicrawl.fetching.stealth_enhanced import StealthEnhancer
    enhancer = StealthEnhancer(proxy_list=["http://p1:8080", "http://p2:8080"])
    enhanced_config = enhancer.randomize()
"""

from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

# ── 指纹库 ────────────────────────────────────────────────────────────

# 常用 User-Agent（按 OS/浏览器分类）
_USER_AGENTS = [
    # Windows + Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Windows + Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    # Windows + Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # macOS + Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # macOS + Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    # Linux + Chrome
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# 常见屏幕分辨率
_RESOLUTIONS = [
    (1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600),
    (1440, 900), (1680, 1050), (1366, 768), (1536, 864),
    (1280, 720), (1280, 800), (1280, 1024),
]

# 常见语言设置
_LANGUAGES = [
    ["zh-CN", "zh", "en-US", "en"],
    ["en-US", "en", "zh-CN", "zh"],
    ["zh-CN", "zh", "en"],
    ["en-US", "en"],
    ["ja-JP", "ja", "en-US", "en"],
    ["ko-KR", "ko", "en-US", "en"],
]

# 常见时区
_TIMEZONES = [
    "Asia/Shanghai", "Asia/Tokyo", "Asia/Seoul",
    "America/New_York", "America/Los_Angeles", "America/Chicago",
    "Europe/London", "Europe/Berlin", "Europe/Paris",
    "Australia/Sydney", "Pacific/Auckland",
]

# WebGL 供应商/渲染器（用于 Canvas 指纹伪装）
_WEBGL_VENDORS = [
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
]

# Canvas 指纹噪声种子
_CANVAS_NOISE_SEEDS = list(range(1000))

# 平台
_PLATFORMS = [
    "Win32", "Win32", "Win32", "MacIntel", "MacIntel", "Linux x86_64",
]


@dataclass
class Fingerprint:
    """浏览器指纹配置。"""
    user_agent: str = ""
    viewport_width: int = 1920
    viewport_height: int = 1080
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False
    languages: list[str] = field(default_factory=lambda: ["zh-CN", "zh", "en"])
    timezone: str = "Asia/Shanghai"
    platform: str = "Win32"
    webgl_vendor: str = ""
    webgl_renderer: str = ""
    canvas_noise: int = 0
    hardware_concurrency: int = 8
    device_memory: int = 8
    color_depth: int = 24
    pixel_depth: int = 24
    # 额外 HTTP 头
    accept_language: str = ""
    sec_ch_ua: str = ""
    sec_ch_ua_platform: str = ""


# ── 代理轮换 ──────────────────────────────────────────────────────────

class ProxyRotator:
    """代理轮换器。"""

    def __init__(self, proxies: list[str] | None = None) -> None:
        self._proxies: list[str] = list(proxies) if proxies else []
        self._index: int = 0
        self._lock = threading.Lock()
        self._usage: dict[str, int] = {}      # 使用计数
        self._failures: dict[str, int] = {}    # 失败计数
        self._domain_binding: dict[str, str] = {}  # 域名 → 代理绑定

    def add(self, proxy: str) -> None:
        with self._lock:
            if proxy not in self._proxies:
                self._proxies.append(proxy)

    def remove(self, proxy: str) -> None:
        with self._lock:
            if proxy in self._proxies:
                self._proxies.remove(proxy)
            self._usage.pop(proxy, None)
            self._failures.pop(proxy, None)

    def next_round_robin(self) -> str | None:
        """轮询模式。"""
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            self._usage[proxy] = self._usage.get(proxy, 0) + 1
            return proxy

    def next_random(self) -> str | None:
        """随机模式。"""
        with self._lock:
            if not self._proxies:
                return None
            # 排除最近失败的
            healthy = [p for p in self._proxies if self._failures.get(p, 0) < 3]
            if not healthy:
                healthy = self._proxies
                for p in self._proxies:
                    self._failures[p] = 0
            proxy = random.choice(healthy)
            self._usage[proxy] = self._usage.get(proxy, 0) + 1
            return proxy

    def next_for_domain(self, domain: str) -> str | None:
        """按域名绑定代理（同一域名始终使用同一代理）。"""
        with self._lock:
            if domain in self._domain_binding:
                return self._domain_binding[domain]
            if not self._proxies:
                return None
            proxy = random.choice(self._proxies)
            self._domain_binding[domain] = proxy
            self._usage[proxy] = self._usage.get(proxy, 0) + 1
            return proxy

    def report_failure(self, proxy: str) -> None:
        with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1

    def report_success(self, proxy: str) -> None:
        with self._lock:
            self._failures[proxy] = max(0, self._failures.get(proxy, 0) - 1)

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def stats(self) -> dict[str, Any]:
        return {"total": len(self._proxies), "usage": dict(self._usage), "failures": dict(self._failures)}


# ── 人类行为模拟 ──────────────────────────────────────────────────────

class HumanBehavior:
    """人类行为模拟器 — 生成真实的延迟、滚动和鼠标模式。"""

    @staticmethod
    def think_delay(min_ms: int = 200, max_ms: int = 3000) -> float:
        """模拟人类思考时间（对数正态分布）。"""
        mu = math.log((min_ms + max_ms) / 2)
        sigma = 0.3
        delay = random.lognormvariate(mu, sigma)
        return max(min_ms, min(delay, max_ms)) / 1000  # 转换为秒

    @staticmethod
    def type_delay(char_count: int) -> float:
        """模拟打字延迟（每字符 50-200ms，含随机停顿）。"""
        base = char_count * random.uniform(0.05, 0.2)
        pauses = random.randint(0, char_count // 10) * random.uniform(0.5, 2.0)
        return base + pauses

    @staticmethod
    def scroll_pattern(page_height: int, viewport_height: int) -> list[tuple[int, float]]:
        """生成人类滚动模式：(滚动距离, 停顿秒数)。"""
        patterns: list[tuple[int, float]] = []
        remaining = page_height - viewport_height
        if remaining <= 0:
            return patterns
        pos = 0
        while pos < remaining:
            # 随机滚动距离（300-800px）
            scroll = random.randint(300, min(800, remaining - pos))
            pos += scroll
            # 随机停顿（1-5 秒阅读时间）
            pause = random.uniform(1.0, 5.0)
            patterns.append((scroll, pause))
        return patterns

    @staticmethod
    def mouse_path(from_x: int, from_y: int, to_x: int, to_y: int, steps: int = 30) -> list[tuple[int, int]]:
        """生成贝塞尔曲线鼠标路径。"""
        cp1_x = from_x + random.randint(-100, 100)
        cp1_y = from_y + random.randint(-100, 100)
        cp2_x = to_x + random.randint(-50, 50)
        cp2_y = to_y + random.randint(-50, 50)
        path: list[tuple[int, int]] = []
        for i in range(steps + 1):
            t = i / steps
            x = (1-t)**3 * from_x + 3*(1-t)**2*t * cp1_x + 3*(1-t)*t**2 * cp2_x + t**3 * to_x
            y = (1-t)**3 * from_y + 3*(1-t)**2*t * cp1_y + 3*(1-t)*t**2 * cp2_y + t**3 * to_y
            path.append((int(x), int(y)))
        return path


# ── 主增强器 ──────────────────────────────────────────────────────────

class StealthEnhancer:
    """反检测增强器 — 一键生成随机指纹 + 代理。"""

    def __init__(
        self,
        proxy_list: list[str] | None = None,
        seed: int | None = None,
    ) -> None:
        self._rng = random.Random(seed or int(time.time() * 1000))
        self.rotator = ProxyRotator(proxy_list)
        self._generation: int = 0

    def randomize(self, *, domain: str = "") -> Fingerprint:
        """生成一个随机浏览器指纹。"""
        self._generation += 1
        ua = self._rng.choice(_USER_AGENTS)
        w, h = self._rng.choice(_RESOLUTIONS)
        vendor, renderer = self._rng.choice(_WEBGL_VENDORS)

        # 从 UA 推断平台
        platform = "Win32"
        if "Macintosh" in ua or "Mac OS" in ua:
            platform = "MacIntel"
        elif "Linux" in ua or "X11" in ua:
            platform = "Linux x86_64"

        try:
            lang_str = ",".join(self._rng.choice(_LANGUAGES))
        except Exception:
            lang_str = "zh-CN,zh,en"

        return Fingerprint(
            user_agent=ua,
            viewport_width=w, viewport_height=h,
            device_scale_factor=self._rng.choice([1.0, 1.0, 1.25, 1.5, 2.0]),
            languages=self._rng.choice(_LANGUAGES),
            timezone=self._rng.choice(_TIMEZONES),
            platform=platform,
            webgl_vendor=vendor, webgl_renderer=renderer,
            canvas_noise=self._rng.randint(0, 999),
            hardware_concurrency=self._rng.choice([4, 8, 12, 16]),
            device_memory=self._rng.choice([4, 8, 16]),
            accept_language=lang_str,
            sec_ch_ua=f'"Chromium";v="{self._rng.randint(125, 131)}", "Not=A?Brand";v="24", "Google Chrome";v="{self._rng.randint(125, 131)}"',
            sec_ch_ua_platform=f'"{platform}"',
        )

    def proxy_for_domain(self, domain: str) -> str | None:
        return self.rotator.next_for_domain(domain)

    def report_proxy_result(self, proxy: str, success: bool) -> None:
        if success:
            self.rotator.report_success(proxy)
        else:
            self.rotator.report_failure(proxy)

    # ── Playwright 集成 ──────────────────────────────────────────────

    def apply_to_playwright_context(self, context: Any, fingerprint: Fingerprint | None = None) -> None:
        """将指纹应用到 Playwright BrowserContext。"""
        fp = fingerprint or self.randomize()

        # 设置视口
        try:
            page = context.pages[0] if hasattr(context, "pages") and context.pages else context.new_page()
            page.set_viewport_size({"width": fp.viewport_width, "height": fp.viewport_height})
        except Exception as exc:
            LOGGER.warning("设置视口尺寸失败: %s", exc)

        # 注入指纹脚本
        init_script = f"""
        // === OmniCrawler Stealth Enhancer ===
        Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => {fp.hardware_concurrency}}});
        Object.defineProperty(navigator, 'deviceMemory', {{get: () => {fp.device_memory}}});
        Object.defineProperty(navigator, 'platform', {{get: () => '{fp.platform}'}});
        Object.defineProperty(navigator, 'languages', {{get: () => [{', '.join(repr(lang) for lang in fp.languages)}]}});
        Object.defineProperty(navigator, 'language', {{get: () => '{fp.languages[0]}'}});

        // 覆盖时区
        Date.prototype.getTimezoneOffset = function() {{
            const tz = '{fp.timezone}';
            const offsets = {{'Asia/Shanghai': -480, 'Asia/Tokyo': -540, 'America/New_York': 300, 'Europe/London': 0}};
            return offsets[tz] || -480;
        }};

        // 覆盖屏幕分辨率
        Object.defineProperty(screen, 'width', {{get: () => {fp.viewport_width}}});
        Object.defineProperty(screen, 'height', {{get: () => {fp.viewport_height}}});
        Object.defineProperty(screen, 'colorDepth', {{get: () => {fp.color_depth}}});
        Object.defineProperty(screen, 'pixelDepth', {{get: () => {fp.pixel_depth}}});

        // Canvas 指纹噪声
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imageData.data.length; i += 4) {{
                    imageData.data[i] ^= {fp.canvas_noise % 256};
                }}
            }}
            return origToDataURL.apply(this, arguments);
        }};

        // WebGL 指纹噪声
        try {{
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {{
                if (p === 37445) return '{fp.webgl_vendor}';
                if (p === 37446) return '{fp.webgl_renderer}';
                return getParam.call(this, p);
            }};
        }} catch(e) {{}}
        """
        try:
            context.add_init_script(init_script)
        except Exception as exc:
            LOGGER.warning("注入指纹脚本失败: %s", exc)

    # ── Selenium 集成 ─────────────────────────────────────────────────

    @staticmethod
    def apply_to_selenium_options(options: Any, fingerprint: Fingerprint | None = None) -> None:
        """将指纹应用到 Selenium ChromeOptions。"""
        enhancer = StealthEnhancer()
        fp = fingerprint or enhancer.randomize()
        options.add_argument(f"user-agent={fp.user_agent}")
        options.add_argument(f"--window-size={fp.viewport_width},{fp.viewport_height}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)


# ── 便捷函数 ──────────────────────────────────────────────────────────

_global_enhancer: StealthEnhancer | None = None


def get_stealth_enhancer() -> StealthEnhancer:
    global _global_enhancer
    if _global_enhancer is None:
        _global_enhancer = StealthEnhancer()
    return _global_enhancer


def random_fingerprint() -> Fingerprint:
    return get_stealth_enhancer().randomize()


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="反检测增强器 — 生成随机浏览器指纹")
    parser.add_argument("--count", type=int, default=1, help="生成指纹数量")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    enhancer = StealthEnhancer()
    for _ in range(args.count):
        fp = enhancer.randomize()
        if args.json:
            import dataclasses
            print(json.dumps(dataclasses.asdict(fp), ensure_ascii=False, indent=2))
        else:
            print(f"UA: {fp.user_agent[:80]}...")
            print(f"Viewport: {fp.viewport_width}x{fp.viewport_height}")
            print(f"Platform: {fp.platform} | Timezone: {fp.timezone}")
            print(f"Languages: {fp.languages}")
            print(f"WebGL: {fp.webgl_vendor}")
            print("---")


if __name__ == "__main__":
    main()
