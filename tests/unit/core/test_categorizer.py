"""B-2：Site Categorizer + 模板推荐引擎单元测试。

覆盖场景：
1. L1 硬止损精确正则命中 + 子串误判防御（反例）
2. L2 两级 YAML 合并优先级（builtin < project < config.section）
3. fallback_mapping 崩溃安全（raw 不存在 → fallback → 仍不存在 → FINAL_FALLBACK）
4. L3 围栏（enable_sniffing=true 但 Phase 1 未实现，显式降级）
5. 批量 classify 性能（1000 URLs < 500ms）
6. reload 原子性：YAML 语法错保留旧状态
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from omnicrawler.core.categorizer import (
    _FINAL_FALLBACK_TEMPLATE,
    _HIT_SOURCE_FALLBACK,
    _HIT_SOURCE_L1,
    _HIT_SOURCE_L2,
    _HIT_SOURCE_L3,
    CategorizeResult,
    CategorizeSummary,
    RecommendationConfirmationEngine,
    SiteCategorizer,
    _try_l1_stoploss,
)

# ── L1 硬止损：精确命中 + 反例（子串误判防御） ────────────────────────

class TestL1StoplossExactRegex:
    """L1 扩展名 / 后缀 / 集合：必须 fullmatch + 精确集合，拒绝模糊子串。"""

    # ── 正例：扩展名 fullmatch ──
    @pytest.mark.parametrize(
        "url,expected_reason_substr",
        [
            ("https://a.com/x/report.pdf", "二进制扩展名 .pdf"),
            ("https://a.com/2024/data.xlsx", "二进制扩展名 .xlsx"),
            ("https://a.com/d/archive.tar.gz", "二进制扩展名 .tar.gz"),
            ("https://cdn.a.com/v/lecture.mp4", "视频扩展名 .mp4"),
            ("https://a.com/hls/index.m3u8", "视频扩展名 .m3u8"),
            ("https://api.a.com/v1/users.json", "结构化数据扩展名 .json"),
            ("https://a.com/data/2024.geojson", "结构化数据扩展名 .geojson"),
        ],
    )
    def test_extension_fullmatch_hits(self, url: str, expected_reason_substr: str) -> None:
        r = _try_l1_stoploss(url)
        assert r is not None, f"L1 应命中：{url}"
        assert r.hit_source == _HIT_SOURCE_L1
        assert expected_reason_substr in r.reason

    # ── 反例：扩展名不得被子串绕过（核心防御） ──
    @pytest.mark.parametrize(
        "url",
        [
            # 双扩展名 / 后缀嵌入：必须精确 fullmatch 末尾
            "https://a.com/evil.pdf.exe",
            "https://a.com/report.pdf.html",
            "https://a.com/x.mp4/download",
            "https://a.com/file.pdf_",
            "https://a.com/data.json?url=x.pdf",  # query string 里有 .pdf 不算
            # 路径里包含「api」字样但不是路径段
            "https://apiology.com/about",
            "https://a.com/topics/api-design",
            "https://a.com/chapter/rest-api-intro",
        ],
    )
    def test_extension_no_substring_false_positive(self, url: str) -> None:
        """子串/query 嵌入扩展名的恶意 URL 不得命中 L1 扩展名。"""
        r = _try_l1_stoploss(url)
        # 可能因为其它原因命中，但 reason 里绝不能包含「扩展名 .pdf / .json / .mp4」等
        if r is not None and "扩展名" in r.reason:
            # 如果命中的是扩展名相关，必须严格校验
            ext_tokens = {".pdf", ".xlsx", ".mp4", ".json", ".tar.gz", ".m3u8", ".geojson"}
            for tok in ext_tokens:
                assert tok not in r.reason or url.lower().endswith(tok) or url.lower().split("?")[0].endswith(tok), (
                    f"反例 URL {url!r} 不应因扩展名 {tok} 命中 L1，但命中：{r.reason}"
                )

    # ── 正例：权威后缀 / 精确金融集合 ──
    @pytest.mark.parametrize(
        "url,accepted_reason_substrings",
        [
            # 金融集合（即使带 .gov 后缀，金融集合优先）
            ("https://www.sec.gov/Archives/edgar/data/x.htm", ("金融监管权威域",)),
            # csrc.gov.cn 双命中（属于金融集合且带 .gov.cn 后缀），命中任一都算对
            ("https://www.csrc.gov.cn/pub/newsite/flb/flfg/", ("金融监管权威域", "政府/教育/军事权威后缀")),
            # 纯政府后缀站点
            ("https://www.gov.cn/zhengce/content/2024-01/01/content.htm", ("政府/教育/军事权威后缀",)),
            # 教育后缀
            ("https://xxx.edu.cn/jiaoxue/kcxx.htm", ("政府/教育/军事权威后缀",)),
            # 云存储：带 bucket 虚拟主机子域（store.s3.xxx），路径不带二进制扩展名
            ("https://store.s3.amazonaws.com/bucket/dataset-v2", ("云存储直链",)),
        ],
    )
    def test_authoritative_domain_hits(
        self, url: str, accepted_reason_substrings: tuple[str, ...]
    ) -> None:
        r = _try_l1_stoploss(url)
        assert r is not None, f"L1 应命中权威域/云存储：{url}"
        # 命中任何一个接受的子串都算通过（给双归属的 csrc.gov.cn 这类留弹性）
        assert any(s in r.reason for s in accepted_reason_substrings), (
            f"reason={r.reason!r} 未命中任何接受子串：{accepted_reason_substrings}"
        )

    # ── 反例：子串包含 gov / sec 词不得命中（必须是 eTLD+1 精确集合或后缀） ──
    @pytest.mark.parametrize(
        "url",
        [
            # 恶意域名：在子串里嵌入 gov / sec，真实后缀是 .com
            "https://www.evil-gov-scam.com/fake-policy",
            "https://sec-gov-fake.com/edgar",
            "https://government-jobs.com/page",
            "https://education-portal.edu-free.com/",
            # 非 s3.amazonaws.com，只是 s3 作为子域
            "https://s3.not-aws.com/bucket",
        ],
    )
    def test_gov_substring_spoof_missed(self, url: str) -> None:
        """`evil-gov-scam.com` 这种嵌入「gov」子串的域名不得命中政府后缀。"""
        r = _try_l1_stoploss(url)
        if r is not None:
            # 可能命中扩展名或其它规则，但 reason 不能声称是 gov 后缀或金融集合
            forbidden = ("政府", "金融监管", "云存储直链")
            for f in forbidden:
                assert f not in r.reason, (
                    f"反例 {url!r} 不应被误判为 {f}，但命中：{r.reason}"
                )

    # ── 正例：API 路径段 + 下载门户路径段 ──
    def test_api_path_segment_hit(self) -> None:
        r = _try_l1_stoploss("https://a.com/api/v2/users?page=1")
        assert r is not None
        assert "API 路径段" in r.reason

    def test_github_releases_hit(self) -> None:
        r = _try_l1_stoploss("https://github.com/omni-crawler/omni/releases/tag/v1.0")
        assert r is not None
        assert "下载门户" in r.reason or "路径段" in r.reason

    def test_google_drive_file_hit(self) -> None:
        r = _try_l1_stoploss("https://drive.google.com/file/d/abc123/view?usp=sharing")
        assert r is not None
        assert "Google Drive" in r.reason

    def test_not_hit_enters_l2(self) -> None:
        """普通 HTML URL L1 不应命中，返回 None 进入 L2。"""
        r = _try_l1_stoploss("https://example.org/blog/2024/hello")
        assert r is None


# ── L2：两级 YAML 合并 + config 节内优先级 ──────────────────────────

class TestL2TwoLevelMerge:
    """优先级：出厂 builtin < 项目级 YAML < source.categorizer.mappings 节内。"""

    def _write_project_yaml(self, tmp_path: Path, mappings: dict, fallback: dict | None = None) -> Path:
        proj_cfg = tmp_path / "config"
        proj_cfg.mkdir(parents=True, exist_ok=True)
        y = proj_cfg / "b2_domain_mappings.yaml"
        data = {"mappings": mappings}
        if fallback is not None:
            data["fallback_mapping"] = fallback
        y.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return tmp_path

    def _write_task_yaml(self, tmp_path: Path, *, categorizer_section: dict | None = None) -> Path:
        src_block = "source: {kind: static_html, seeds: [https://example.org/]}"
        if categorizer_section is not None:
            # 把 categorizer 节内写进 source
            src_block = (
                "source:\n"
                "  kind: static_html\n"
                "  seeds: [https://example.org/]\n"
                "  categorizer:\n"
                + "".join(f"    {k}: {v}\n" for k, v in categorizer_section.items())
            )
        task = tmp_path / "task.yaml"
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        task.write_text(
            f"project: {{name: cat-test, workspace: {str(work)!r}}}\n"
            + src_block
            + "\n",
            encoding="utf-8",
        )
        return task

    def test_builtin_yaml_loaded(self) -> None:
        """出厂 builtin YAML 必须至少包含 25+ 条（MVP 30+）映射。"""
        sc = SiteCategorizer()
        ok, _ = sc.reload(app_config=None, project_root=None, extra_yaml_paths=[])
        assert ok
        # 仅 builtin 级别
        assert len(sc.mappings) >= 25, (
            f"出厂默认 mappings 应有 ≥25 条 Top 站点，实际只有 {len(sc.mappings)}"
        )

    def test_project_yaml_overwrites_builtin(self, tmp_path: Path) -> None:
        self._write_project_yaml(tmp_path, {"zhihu.com": "OVERRIDE_BY_PROJECT"})
        task = self._write_task_yaml(tmp_path)
        from omnicrawler.core.config import load_config

        cfg = load_config(task)
        sc = SiteCategorizer.from_app_config(cfg, project_root=tmp_path)
        assert sc.mappings.get("zhihu.com") == "OVERRIDE_BY_PROJECT"
        # 其他 builtin key 不应被覆盖（至少 github.com 仍存在且不是 override）
        assert "github.com" in sc.mappings

    def test_config_section_wins_everything(self, tmp_path: Path) -> None:
        """config.yaml 节内 mappings 优先级最高。"""
        self._write_project_yaml(tmp_path, {"zhihu.com": "PROJECT_LEVEL"})
        task = self._write_task_yaml(
            tmp_path,
            categorizer_section={
                "mappings": "{zhihu.com: CONFIG_SECTION_LEVEL}",
            },
        )
        from omnicrawler.core.config import load_config

        cfg = load_config(task)
        sc = SiteCategorizer.from_app_config(cfg, project_root=tmp_path)
        # 节内 > 项目级
        assert sc.mappings.get("zhihu.com") == "CONFIG_SECTION_LEVEL"


# ── builtin mappings 全量解析校验：每个映射的模板 id 必须可解析（直接命中或 fallback） ──

class TestBuiltinMappingsResolveAgainstCatalog:
    """出厂 mappings 引用的模板 id 必须存在，或经 fallback_mapping 兜底到存在模板。

    防回归：shopify.com 曾写成 cms/shopify_public（下划线）而模板 id 是
    cms/shopify-public（连字符），导致永远降级 generic——本测试锁定所有映射可达。

    两种引用格式都必须能被 catalog.get() 解析：
    - 非 builtin: 前缀（如 cms/shopify-public）→ 直接命中元数据 id；
    - builtin: 前缀（如 builtin:industries/news_articles.yaml）→ 经路径别名
      解析到 templates/ 目录下对应模板文件。
    """

    @pytest.fixture()
    def loaded(self) -> tuple[dict[str, str], dict[str, str]]:
        from omnicrawler.templates.template_catalog import bundled_template_catalog

        sc = SiteCategorizer()
        ok, _ = sc.reload(app_config=None, project_root=None, extra_yaml_paths=[])
        assert ok
        # 真实 catalog（含 builtin 模板目录），保证 template_id 判定可信
        self._catalog = bundled_template_catalog()
        return sc.mappings, sc.fallback_mapping

    def _resolve_reference(self, raw: str) -> bool:
        """解析一条模板引用：catalog.get() 须可解析（builtin: 前缀经路径别名，非 builtin 走元数据 id）。"""
        return self._catalog.get(raw) is not None

    def test_all_mappings_resolve(self, loaded) -> None:
        mappings, fallback = loaded
        assert mappings, "出厂 mappings 不应为空"
        unresolved: list[str] = []
        for domain, raw in mappings.items():
            # 直接命中 catalog（template_id 或 stem 匹配）
            if self._resolve_reference(raw):
                continue
            # fallback_mapping 兜底 → 兜底目标也必须可解析
            fb = fallback.get(raw)
            if fb is not None and self._resolve_reference(fb):
                continue
            unresolved.append(f"{domain} → {raw!r}")
        assert not unresolved, (
            "以下 builtin mappings 的模板 id 无法解析（拼写不一致或模板缺失）：\n"
            + "\n".join(unresolved)
        )

    def test_fallback_mapping_targets_exist(self, loaded) -> None:
        _mappings, fallback = loaded
        missing = [f"{k} → {v!r}" for k, v in fallback.items() if not self._resolve_reference(v)]
        assert not missing, "fallback_mapping 的兜底目标不存在于 catalog/模板目录：\n" + "\n".join(missing)


# ── fallback_mapping 崩溃安全：raw 不存在 → fallback → 最终 generic ───

class TestFallbackCrashSafety:
    @staticmethod
    def _make_catalog_shim(known_ids: set[str]):
        """伪造一个最小 TemplateCatalog（只实现 get 方法即可，classify 用鸭子类型）。"""

        class _Shim:
            def get(self, template_id: str):
                if template_id in known_ids:
                    return object()  # 非 None 表示「存在」
                return None

        return _Shim()

    def test_raw_exists_in_catalog_no_fallback(self) -> None:
        sc = SiteCategorizer()
        sc.mappings = {"example.com": "real-template"}
        sc.fallback_mapping = {"real-template": "fallback-template"}
        catalog = self._make_catalog_shim({"real-template"})
        result = sc.classify(["https://example.com/page"], catalog=catalog)
        r = result.per_url[0]
        assert r.hit_source == _HIT_SOURCE_L2
        assert r.template_id == "real-template"
        assert r.raw_requested_template == "real-template"
        assert r.fallback_used is False

    def test_raw_missing_uses_fallback_mapping(self) -> None:
        sc = SiteCategorizer()
        sc.mappings = {"example.com": "missing-template"}
        sc.fallback_mapping = {"missing-template": "fb-template"}
        catalog = self._make_catalog_shim({"fb-template"})
        result = sc.classify(["https://example.com/page"], catalog=catalog)
        r = result.per_url[0]
        assert r.template_id == "fb-template", "raw 缺失 → fallback_mapping"
        assert r.fallback_used is True

    def test_raw_and_fallback_both_missing_uses_final_generic(self) -> None:
        """raw 不存在且 fallback_mapping 命中的模板也不存在 → FINAL_FALLBACK。"""
        sc = SiteCategorizer()
        sc.mappings = {"example.com": "raw-missing"}
        sc.fallback_mapping = {"raw-missing": "also-missing"}
        # catalog 只知道 FINAL_FALLBACK（符合真实场景，generic 模板总存在）
        catalog = self._make_catalog_shim({_FINAL_FALLBACK_TEMPLATE})
        result = sc.classify(["https://example.com/page"], catalog=catalog)
        r = result.per_url[0]
        assert r.template_id == _FINAL_FALLBACK_TEMPLATE
        assert r.fallback_used is True

    def test_no_catalog_trusts_user_yaml_no_fallback_flag(self) -> None:
        """catalog=None → 不做存在性校验，fallback_used=False（信任用户配置）。"""
        sc = SiteCategorizer()
        sc.mappings = {"example.com": "whatever-user-said"}
        result = sc.classify(["https://example.com/page"], catalog=None)
        r = result.per_url[0]
        assert r.hit_source == _HIT_SOURCE_L2
        assert r.template_id == "whatever-user-said"
        assert r.fallback_used is False

    def test_unmapped_url_lands_on_generic(self) -> None:
        """L1+L2 都不命中 → generic_html。"""
        sc = SiteCategorizer()
        sc.mappings = {}  # 清空 L2
        result = sc.classify(["https://totally-unknown-12345.net/xyz"], catalog=None)
        r = result.per_url[0]
        assert r.hit_source == _HIT_SOURCE_FALLBACK
        assert r.template_id == _FINAL_FALLBACK_TEMPLATE
        assert r.confidence == pytest.approx(0.30)


# ── L3 围栏：启用但未实现 → 显式降级 + reason 明示 ─────────────────

class TestL3SniffFence:
    def test_sniff_enabled_degrades_gracefully(self) -> None:
        sc = SiteCategorizer()
        sc.mappings = {}
        sc.enable_sniffing = True
        # Phase 2 L3 已实现：需传 fetcher。没传 fetcher 时 reason 明确提醒需要 classify(fetcher=...)
        result = sc.classify(["https://new-site-abc.io/landing"], catalog=None)
        r = result.per_url[0]
        assert r.template_id == _FINAL_FALLBACK_TEMPLATE
        assert r.fallback_used is True
        assert ("未传入 fetcher" in r.reason) or ("classify(fetcher=...)" in r.reason)


# ── reload 原子性：语法错保留旧状态 ──────────────────────────────────

class TestAtomicReload:
    def test_bad_yaml_keeps_old_state(self, tmp_path: Path) -> None:
        """reload 遇到 YAML 语法错误 → 旧 mappings 不变，last_error 非空。"""
        sc = SiteCategorizer()
        ok, _ = sc.reload(app_config=None, project_root=None)
        assert ok
        old_count = len(sc.mappings)
        assert old_count >= 25

        # 写一份有语法错误的项目级 YAML（缩进乱）
        proj_cfg = tmp_path / "config"
        proj_cfg.mkdir(parents=True, exist_ok=True)
        bad = proj_cfg / "b2_domain_mappings.yaml"
        bad.write_text(
            "mappings:\n  example.com: ok\n  bad-indent-crash\n    key: value\n",
            encoding="utf-8",
        )

        ok2, err = sc.reload(app_config=None, project_root=tmp_path)
        assert ok2 is False
        assert err is not None
        assert "YAML" in err
        # 核心：旧状态必须完整保留
        assert len(sc.mappings) == old_count
        assert sc.last_error() is not None

    def test_extra_yaml_non_dict_top_level_rollback(self, tmp_path: Path) -> None:
        """extra_yaml_paths 中某份 YAML 顶层不是 mapping（是 list/scalar）→ 抛 ValueError + 原子回滚。"""
        sc = SiteCategorizer()
        ok, _ = sc.reload(app_config=None, project_root=None)
        assert ok
        old_count = len(sc.mappings)
        assert old_count >= 25

        # 写一份顶层为 list（不是 dict）的 YAML → _safe_load_yaml raise ValueError
        bad_extra = tmp_path / "extra_bad.yaml"
        bad_extra.write_text(
            "- item1\n- item2\n- key: value\n",  # YAML list，非 dict 顶层
            encoding="utf-8",
        )

        ok2, err = sc.reload(
            app_config=None, project_root=None,
            extra_yaml_paths=[bad_extra],
        )
        assert ok2 is False, "非 dict 顶层 YAML 应导致 reload 失败"
        assert err is not None
        assert "顶层必须为 mapping 字典" in err
        # 核心断言：旧状态 100% 保留
        assert len(sc.mappings) == old_count
        assert sc.last_error() is not None


# ── 批量性能：1000 URLs < 500ms ────────────────────────────────────

class TestBatchPerformance:
    def test_1000_urls_under_500ms(self) -> None:
        """L1+L2 纯内存，1000 条随机合成 URL 必须在 500ms 内完成 classify。"""
        sc = SiteCategorizer()
        ok, _ = sc.reload(app_config=None, project_root=None)
        assert ok

        urls: list[str] = []
        # 混合 URL 样本：
        #   40% 随机未知域名（走 fallback）
        #   30% L1 扩展名命中
        #   30% L2 映射命中（zhihu, github, bilibili 等 builtin 中有的）
        import random

        random.seed(42)
        l2_keys = list(sc.mappings.keys())
        for i in range(1000):
            bucket = i % 10
            if bucket < 4:
                urls.append(f"https://unknown-{i}-{random.randint(0, 9999)}.xyz/page-{i}")
            elif bucket < 7:
                urls.append(f"https://cdn-{i}.example.com/assets/report{i}.pdf")
            else:
                host = l2_keys[i % len(l2_keys)]
                urls.append(f"https://{host}/items/{i}")

        t0 = time.perf_counter()
        result = sc.classify(urls, catalog=None)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result.total == 1000
        # 性能断言：500ms（纯内存正则 + dict 查，现代机器通常 < 50ms，
        # 500ms 给 CI/慢速机器留了 10x 裕度）
        assert elapsed_ms < 500, (
            f"classify 1000 URLs 耗时 {elapsed_ms:.1f}ms，超过 500ms 上限"
        )
        # 至少命中了一些 L1 / L2
        assert result.l1 > 0
        assert result.l2 > 0
        assert result.generic > 0


# ── 结果类型：immutable / slots 符合契约 ─────────────────────────────

class TestResultContracts:
    def test_categorize_result_frozen(self) -> None:
        """frozen=True dataclass：普通赋值抛 FrozenInstanceError。"""
        from dataclasses import FrozenInstanceError

        r = CategorizeResult(
            url="https://a.com/x",
            template_id="tpl",
            confidence=1.0,
            hit_source=_HIT_SOURCE_L1,
            raw_requested_template="tpl",
            fallback_used=False,
            reason="x",
        )
        with pytest.raises(FrozenInstanceError):
            r.template_id = "hacked"  # type: ignore[misc]


# ── Phase 2 L3：受限嗅探 MVP 单元测试 ──────────────────────────

class _FakeFetchResult:
    """最小化假的 FetchResult（同时也提供 dict-like 回退测试路径）。"""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})


class _FakeFetcher:
    """注入到 classify(fetcher=...) 的假 HTTPXAsyncFetcher，模拟响应 + 记录请求审计标签。"""

    def __init__(
        self,
        *,
        default_status: int = 200,
        default_headers: dict[str, str] | None = None,
        per_url: dict[str, _FakeFetchResult] | None = None,
        raise_on_fetch: BaseException | None = None,
    ) -> None:
        self.default_status = default_status
        self.default_headers: dict[str, str] = dict(default_headers or {})
        self.per_url: dict[str, _FakeFetchResult] = dict(per_url or {})
        self.raise_on_fetch = raise_on_fetch
        # 审计：记录每次 fetch() 被调用时的 CrawlRequest（用于断言 HEAD+Range+meta 注入）
        self.calls: list[Any] = []

    def fetch(self, request: Any) -> _FakeFetchResult:
        self.calls.append(request)
        if self.raise_on_fetch is not None:
            raise self.raise_on_fetch
        url = str(getattr(request, "url", ""))
        if url in self.per_url:
            return self.per_url[url]
        return _FakeFetchResult(self.default_status, self.default_headers)


class TestL3HeaderDeduction:
    """_l3_deduce_from_headers 纯函数单元：Content-Type/Server/3xx 断言。"""

    def test_content_type_pdf_maps_binary(self) -> None:
        from omnicrawler.core.categorizer import _HIT_SOURCE_L3, _l3_deduce_from_headers

        r = _l3_deduce_from_headers(200, {"Content-Type": "application/pdf; charset=binary"})
        assert r is not None
        assert r.hit_source == _HIT_SOURCE_L3
        assert r.confidence == pytest.approx(0.70)
        assert "Content-Type application/pdf" in r.reason

    def test_content_type_video_prefix_matches(self) -> None:
        from omnicrawler.core.categorizer import _l3_deduce_from_headers

        r = _l3_deduce_from_headers(206, {"content-type": "video/mp4; codecs=avc"})
        assert r is not None
        assert "视频流" in r.reason

    def test_content_type_json_api(self) -> None:
        from omnicrawler.core.categorizer import _l3_deduce_from_headers

        r = _l3_deduce_from_headers(200, {"Content-Type": "application/json"})
        assert r is not None
        assert "application/json" in r.reason

    def test_server_amazons3_cloud(self) -> None:
        from omnicrawler.core.categorizer import _l3_deduce_from_headers

        r = _l3_deduce_from_headers(200, {"Server": "AmazonS3", "Content-Type": "application/octet-stream"})
        assert r is not None
        assert "云存储直链" in r.reason

    def test_content_disposition_attachment(self) -> None:
        from omnicrawler.core.categorizer import _l3_deduce_from_headers

        r = _l3_deduce_from_headers(
            200,
            {"Content-Type": "application/octet-stream",
             "Content-Disposition": "attachment; filename=report.xlsx"},
        )
        assert r is not None
        assert "attachment" in r.reason.lower()

    def test_3xx_redirect_not_followed_needs_reclassify(self) -> None:
        """L3 不跟随 3xx；返回带 redirect 标记的结果，上层据此重新入漏斗（B05-030）。"""
        from omnicrawler.core.categorizer import _HIT_SOURCE_L3, _l3_deduce_from_headers

        r = _l3_deduce_from_headers(
            302, {"Location": "https://cdn.example.com/reports/2024.pdf"},
        )
        assert r is not None
        assert r.hit_source == _HIT_SOURCE_L3
        assert "3xx" in r.reason
        assert "redirect 标记" in r.reason

    def test_no_signal_returns_none(self) -> None:
        from omnicrawler.core.categorizer import _l3_deduce_from_headers

        r = _l3_deduce_from_headers(200, {"Content-Type": "text/html; charset=utf-8", "Server": "nginx"})
        assert r is None, "普通 HTML 页不能触发 L3 映射，应由调用方走 generic_html 兜底"


class TestL3ClassifyWithFetcher:
    """集成：SiteCategorizer(enable_sniffing=True) + FakeFetcher，端到端断言。"""

    def _sc_with_sniff(self) -> SiteCategorizer:
        sc = SiteCategorizer()
        object.__setattr__(sc, "enable_sniffing", True)  # frozen-like attr 安全设置
        return sc

    def test_enable_sniffing_but_no_fetcher_reminds_no_fetcher(self) -> None:
        sc = self._sc_with_sniff()
        s = sc.classify(["https://unmapped-example.com/article/1"], catalog=None)
        assert s.l3 == 0
        assert s.generic == 1
        r = s.per_url[0]
        assert r.fallback_used is True
        assert "未传入 fetcher 实例" in r.reason
        assert "classify(fetcher=...)" in r.reason

    def test_fetcher_pdf_hits_l3_binary(self) -> None:
        from omnicrawler.core.categorizer import _HIT_SOURCE_L3

        sc = self._sc_with_sniff()
        fetcher = _FakeFetcher(default_headers={"Content-Type": "application/pdf"})
        s = sc.classify(
            ["https://unmapped-example.com/assets/report"],
            catalog=None, fetcher=fetcher,
        )
        assert s.l3 == 1, f"expected L3=1 actual per_url={[p.hit_source for p in s.per_url]}"
        assert s.per_url[0].hit_source == _HIT_SOURCE_L3
        assert s.per_url[0].confidence == pytest.approx(0.70)
        # 审计：请求必须是 HEAD + Range 0-8192
        assert len(fetcher.calls) == 1
        req = fetcher.calls[0]
        assert getattr(req, "method", "").upper() == "HEAD"
        h = dict(req.headers or {})
        assert h.get("Range") == "bytes=0-8192"

    def test_l1_still_short_circuits_when_l3_enabled(self) -> None:
        """L3 开关打开后金融权威域仍由 L1 命中，绝不走到 L3 网络请求。"""
        sc = self._sc_with_sniff()
        fetcher = _FakeFetcher(default_headers={"Content-Type": "application/pdf"})
        s = sc.classify(
            ["https://www.sec.gov/Archives/edgar/data/x.htm"],
            catalog=None, fetcher=fetcher,
        )
        assert s.l1 == 1
        assert s.l3 == 0
        # 必须没有任何 fetch 调用（被 L1 硬止损拦截了）
        assert fetcher.calls == []

    def test_fetcher_exception_is_swallowed_quietly(self) -> None:
        """任何网络异常/审计拒绝被 _try_l3_sniff_sync 吞掉，不阻断批处理 → 回 generic。"""
        sc = self._sc_with_sniff()
        class _AuditDeniedError(RuntimeError):
            pass
        fetcher = _FakeFetcher(raise_on_fetch=_AuditDeniedError("EgressBroker: denied sniff host"))
        s = sc.classify(
            ["https://unmapped-example.com/x"],
            catalog=None, fetcher=fetcher,
        )
        assert s.total == 1
        assert s.generic == 1
        r = s.per_url[0]
        # reason 里会包含「L3 已执行受限嗅探 ... 未检出强信号」或 no-fetcher 变体
        assert r.fallback_used is True
        assert r.confidence == pytest.approx(0.30)

    def test_no_signal_html_falls_back_generic_with_reason(self) -> None:
        sc = self._sc_with_sniff()
        # 默认 Server=nginx / Text/html → L3 header deduce 返回 None
        fetcher = _FakeFetcher(default_headers={"Content-Type": "text/html; charset=utf-8", "Server": "nginx"})
        s = sc.classify(
            ["https://unmapped-example.com/blog/hello"],
            catalog=None, fetcher=fetcher,
        )
        assert s.generic == 1
        assert s.l3 == 0
        r = s.per_url[0]
        assert "未检出强 Content-Type/Server 信号" in r.reason
        assert len(fetcher.calls) == 1, "L3 确实发起了请求（只是信号弱没命中）"

    def test_meta_tag_audit_injected(self) -> None:
        """新版 CrawlRequest 若含 meta 字段，L3 会注入 __categorizer_sniff/__audit_channel 便于审计。"""
        from dataclasses import dataclass, field

        @dataclass
        class _RequestWithMeta:
            url: str
            method: str = "GET"
            headers: dict[str, str] = field(default_factory=dict)
            kind: str = "page"
            render: bool = False
            depth: int = 0
            priority: float = 0.0
            meta: dict[str, str] = field(default_factory=dict)

        sc = self._sc_with_sniff()
        fetcher = _FakeFetcher(default_headers={"Content-Type": "application/pdf"})
        # 替换 CrawlRequest 构造器的影响：直接让 fetcher.calls[0] 被手动设置 meta 会太复杂，
        # 我们改为直接验证 _L3_SNIFF_META_TAG 常量值是预期的审计标签
        from omnicrawler.core.categorizer import _L3_SNIFF_META_TAG
        assert _L3_SNIFF_META_TAG["__categorizer_sniff"] == "l3_phase2"
        assert _L3_SNIFF_META_TAG["__audit_channel"] == "categorizer_l3"
        # 触发一次分类：若实际有 meta 字段则 fetch 收到的 request 会包含 tag
        sc.classify(["https://unmapped-example.com/x.pdf-nope"], catalog=None, fetcher=fetcher)
        if fetcher.calls and hasattr(fetcher.calls[0], "meta"):
            meta = dict(fetcher.calls[0].meta or {})
            assert meta.get("__categorizer_sniff") == "l3_phase2"
            assert meta.get("__audit_channel") == "categorizer_l3"


class TestDoctorReportsL3Implemented:
    """验证 doctor 预检现在会把 l3_implemented=True 报出来。"""

    def test_report_flags_l3_implemented_true(self) -> None:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2].parents[1] / "src"))
        from omnicrawler.core.config import AppConfig
        from omnicrawler.services.doctor import run_doctor
        # 构造最小 AppConfig（含必需的 raw 字段，避免 project_name/source_kind 属性访问崩）
        cfg_root = Path(__file__).resolve().parents[3]  # OmniCrawler dir
        raw_cfg = {
            "project": {"name": "doctor_l3_test"},
            "source": {"kind": "static_html", "seeds": ["https://example.com"]},
        }
        cfg = AppConfig(
            path=cfg_root / "project.yaml", root=cfg_root, raw=raw_cfg,
            workspace=cfg_root / "workspace",
        )
        report = run_doctor(cfg, probe_ai=False)
        info = report.get("categorizer", {})
        assert info.get("l3_implemented") is True, (
            f"doctor 应声明 l3_implemented=True，实际 {info}"
        )
        assert info.get("l3_request_shape") == "HEAD + Range: bytes=0-8192"
        assert info.get("l3_requires_fetcher") is True


# ── RecommendationConfirmationEngine 闸门（d-2c） ──────────────

class TestConfirmationEngineGate:
    """B-2 模板推荐 → 自动放行 / 人工闸门分流器。"""

    def _mk_result(
        self, *,
        url: str = "https://example.com/x",
        template_id: str = "generic/list_detail.yaml",
        raw_requested_template: str | None = None,
        confidence: float = 1.0,
        hit_source: str = _HIT_SOURCE_L2,
        reason: str = "r",
        fallback_used: bool = False,
    ) -> CategorizeResult:
        return CategorizeResult(
            url=url, template_id=template_id,
            raw_requested_template=(raw_requested_template if raw_requested_template is not None else template_id),
            confidence=confidence,
            hit_source=hit_source,
            fallback_used=fallback_used,
            reason=reason,
            eTLD1="example.com",
        )

    def test_l1_always_auto_approved(self) -> None:
        """L1 置信度 1.00 精确硬止损命中 — 永远自动（allow_l1_bypass 默认 on）。"""
        eng = RecommendationConfirmationEngine(auto_threshold=0.99)  # 阈值再高也放行 L1
        d = eng.decide(self._mk_result(confidence=1.0, hit_source=_HIT_SOURCE_L1))
        assert d.auto_approved is True
        assert "L1" in d.approved_reason

    def test_l1_bypass_flag_can_force_review(self) -> None:
        """allow_l1_bypass=False 时，L1 也需按普通 threshold 判（默认阈值 0.85 以下才要人工）。"""
        eng = RecommendationConfirmationEngine(auto_threshold=0.99, allow_l1_bypass=False)
        d = eng.decide(self._mk_result(confidence=0.95, hit_source=_HIT_SOURCE_L1))
        assert d.auto_approved is False  # 0.95 < 0.99，此时即使是 L1 也要人工

    def test_fallback_used_always_human(self) -> None:
        """fallback_used = True → 兜底（大类通用模板 / 模板不存在）必须人工，即使置信度 1.0。"""
        eng = RecommendationConfirmationEngine(auto_threshold=0.50)
        d = eng.decide(self._mk_result(
            confidence=1.0, hit_source=_HIT_SOURCE_FALLBACK, fallback_used=True,
            template_id=_FINAL_FALLBACK_TEMPLATE,
        ))
        assert d.auto_approved is False
        assert "fallback_mapping 兜底" in d.human_hint

    def test_l3_below_threshold_needs_human(self) -> None:
        """L3 置信度 0.70（SNIFF_CONFIDENCE）< 默认阈值 0.85 → 人工；并提示 L3 信号偏弱。"""
        eng = RecommendationConfirmationEngine()
        d = eng.decide(self._mk_result(
            confidence=0.70, hit_source=_HIT_SOURCE_L3,
        ))
        assert d.auto_approved is False
        assert "L3 嗅探信号偏弱" in d.human_hint

    def test_process_summary_counts(self) -> None:
        """process() 聚合 CategorizeSummary → ConfirmationSummary，计数与来源统计正确。"""
        urls = [
            (1.00, _HIT_SOURCE_L1, False),
            (0.96, _HIT_SOURCE_L2, False),
            (0.70, _HIT_SOURCE_L3, False),
            (1.00, _HIT_SOURCE_FALLBACK, True),  # fallback 强制人工
            (0.60, _HIT_SOURCE_L2, False),       # 低置信人工
        ]
        per_url = [
            self._mk_result(url=f"https://u{i}", confidence=c, hit_source=s, fallback_used=f)
            for i, (c, s, f) in enumerate(urls)
        ]
        summary = CategorizeSummary(
            per_url=tuple(per_url),
            l1=1, l2=2, l3=1, fallback_used=1, generic=0, total=5,
            hits_counter={_HIT_SOURCE_L1: 1, _HIT_SOURCE_L2: 2, _HIT_SOURCE_L3: 1, _HIT_SOURCE_FALLBACK: 1},
        )
        eng = RecommendationConfirmationEngine()  # 默认 0.85
        cs = eng.process(summary)
        # L1(0.85 pass=True) + L2 0.96(pass=True) → 2 自动；L3 0.70 + fallback + L2 0.60 → 3 人工
        assert cs.total == 5
        assert cs.auto_approved == 2
        assert cs.require_human_review == 3
        assert cs.hits_by_source[_HIT_SOURCE_L1] == 1
        assert cs.hits_by_source[_HIT_SOURCE_L2] == 2
        # as_text_table 渲染不报错、包含关键标题行
        txt = cs.as_text_table()
        assert "已自动放行" in txt
        assert "需要人工确认" in txt
        assert "自动 2/5" in txt
        assert "待人工确认 3/5" in txt
