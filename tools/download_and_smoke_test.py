"""Download the PaddleOCR model set and run an offline smoke test (B12-002).

改名自 ``download_ocr_models.py``：本工具只做**功能冒烟**（PPStructureV3 推理空白
合成图），**不是密码学完整性校验**——模型经 CDN 下载、无哈希钉，供应链篡改不可
检出。语义以 ``smoke_verified`` 为准，勿误当作 ``verified``。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def configure_cache(cache: Path, source: str) -> None:
    cache = cache.resolve()
    generic = cache.parent / "shared-cache"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
    os.environ["PADDLE_HOME"] = str(generic / "paddle")
    os.environ["XDG_CACHE_HOME"] = str(generic)
    os.environ["HF_HOME"] = str(generic / "huggingface")
    os.environ["MODELSCOPE_CACHE"] = str(generic / "modelscope")
    os.environ["PADDLE_PDX_MODEL_SOURCE"] = source
    if sys.platform == "win32":
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    cache.mkdir(parents=True, exist_ok=True)


def download_and_smoke_test(cache: Path, source: str) -> dict[str, object]:
    configure_cache(cache, source)
    import numpy as np
    from paddleocr import PPStructureV3
    from PIL import Image, ImageDraw

    pipeline = PPStructureV3(
        lang="ch",
        device="cpu",
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        use_table_recognition=True,
        use_formula_recognition=False,
        use_chart_recognition=False,
    )
    image = Image.new("RGB", (720, 120), "white")
    ImageDraw.Draw(image).text((20, 30), "OmniCrawler OCR 1.0 verification", fill="black")
    results = list(pipeline.predict(np.asarray(image)))

    official = cache / "official_models"
    models = sorted(path.name for path in official.iterdir() if path.is_dir()) if official.is_dir() else []
    total = sum(path.stat().st_size for path in cache.rglob("*") if path.is_file())
    if not models:
        raise RuntimeError(f"PaddleOCR initialized but no offline models were found under {official}")
    report = {
        "schema": 1,
        "backend": "PPStructureV3",
        "source": source,
        "models": models,
        "bytes": total,
        "prediction_results": len(results),
        # F43：区分"完整验证"与"冒烟验证"——关 formula/chart 且仅推理空白合成图时
        # 只算 smoke_verified，不再无条件声称 verified
        "smoke_verified": True,
        "verified_dimensions": {"models_present": len(models), "predictions": len(results)},
    }
    (cache / "omnicrawler-model-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cache", type=Path)
    parser.add_argument("--source", choices=["aistudio", "huggingface", "modelscope"], default="aistudio")
    args = parser.parse_args()
    print(json.dumps(download_and_smoke_test(args.cache, args.source), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
