from __future__ import annotations

import importlib
import os
import sys
import types
import concurrent.futures
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_ocr_module(monkeypatch):
    """Import pdf.ocr with a fake paddleocr module so tests never load models."""
    fake_paddleocr = types.ModuleType("paddleocr")
    fake_paddleocr.PaddleOCR = object
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    sys.modules.pop("pdf.ocr", None)
    return importlib.import_module("pdf.ocr")


def test_import_does_not_rewrite_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    load_ocr_module(monkeypatch)

    assert os.environ["HOME"] == str(home)


def test_default_cache_is_project_local_and_can_be_overridden(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)

    assert ocr.resolve_model_cache_dir() == ROOT / ".paddleocr"

    custom_cache = tmp_path / "ocr-cache"
    monkeypatch.setenv("PDF_STUDY_PADDLEOCR_CACHE", str(custom_cache))

    assert ocr.resolve_model_cache_dir() == custom_cache


def test_model_cache_status_tracks_required_models(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)
    cache_dir = tmp_path / "models"

    status = ocr.model_cache_status(cache_dir)
    assert status["all_cached"] is False
    assert {model["name"] for model in status["models"]} == {
        "PP-OCRv5_mobile_det",
        "korean_PP-OCRv5_mobile_rec",
    }

    for model in status["models"]:
        model_dir = Path(model["path"])
        model_dir.mkdir(parents=True)
        (model_dir / "inference.json").write_text("{}", encoding="utf-8")
        (model_dir / "inference.pdiparams").write_bytes(b"params")

    assert ocr.models_cached(cache_dir) is True


def test_worker_initializes_paddleocr_for_cpu_and_local_cache(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)
    calls: list[dict[str, object]] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def predict(self, image_path):
            assert image_path == "page-1.jpg"
            return [{"rec_texts": ["첫 문장", "second line"]}]

    cache_dir = tmp_path / "models"
    worker = ocr.OCRWorker(ocr_factory=FakePaddleOCR, cache_dir=cache_dir, max_workers=1)

    assert worker.process_image("page-1.jpg") == "첫 문장\nsecond line"
    assert calls == [
        {
            "device": "cpu",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_detection_model_name": "PP-OCRv5_mobile_det",
            "text_recognition_model_name": "korean_PP-OCRv5_mobile_rec",
            "text_recognition_batch_size": 1,
            "text_det_limit_side_len": 960,
            "text_det_limit_type": "max",
            "cpu_threads": 2,
        }
    ]
    assert os.environ["PADDLEOCR_HOME"] == str(cache_dir)
    assert os.environ["PADDLE_PDX_CACHE_HOME"] == str(cache_dir)
    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
    assert cache_dir.is_dir()


def test_worker_allows_lightweight_model_overrides(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)
    monkeypatch.setenv("PDF_STUDY_PADDLEOCR_DET_MODEL", "PP-OCRv6_tiny_det")
    monkeypatch.setenv("PDF_STUDY_PADDLEOCR_REC_MODEL", "custom_rec")
    monkeypatch.setenv("PDF_STUDY_PADDLEOCR_DET_LIMIT_SIDE_LEN", "736")
    monkeypatch.setenv("PDF_STUDY_PADDLEOCR_CPU_THREADS", "1")
    calls: list[dict[str, object]] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def predict(self, image_path):
            return [{"rec_texts": ["ok"]}]

    worker = ocr.OCRWorker(
        ocr_factory=FakePaddleOCR,
        cache_dir=tmp_path / "models",
        max_workers=1,
    )

    assert worker.process_image("page-1.jpg") == "ok"
    assert calls[0]["text_detection_model_name"] == "PP-OCRv6_tiny_det"
    assert calls[0]["text_recognition_model_name"] == "custom_rec"
    assert calls[0]["text_det_limit_side_len"] == 736
    assert calls[0]["cpu_threads"] == 1


def test_worker_prepare_loads_model_and_reports_download_need(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)
    calls: list[dict[str, object]] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            cache_dir = Path(os.environ["PADDLEOCR_HOME"])
            for name in ocr.required_model_names():
                model_dir = cache_dir / "official_models" / name
                model_dir.mkdir(parents=True, exist_ok=True)
                (model_dir / "inference.json").write_text("{}", encoding="utf-8")
                (model_dir / "inference.pdiparams").write_bytes(b"params")

        def predict(self, image_path):
            return [{"rec_texts": ["ok"]}]

    worker = ocr.OCRWorker(
        ocr_factory=FakePaddleOCR,
        cache_dir=tmp_path / "models",
        max_workers=1,
    )

    data = worker.prepare()

    assert calls
    assert data["download_required"] is True
    assert data["model_loaded"] is True
    assert data["all_cached"] is True


def test_worker_keeps_paddleocr_instance_thread_local(monkeypatch, tmp_path):
    ocr = load_ocr_module(monkeypatch)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    instance_ids: list[int] = []
    active_predicts = 0
    max_active_predicts = 0

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            with lock:
                self.instance_id = len(instance_ids) + 1
                instance_ids.append(self.instance_id)

        def predict(self, image_path):
            nonlocal active_predicts, max_active_predicts
            with lock:
                active_predicts += 1
                max_active_predicts = max(max_active_predicts, active_predicts)
            barrier.wait(timeout=5)
            with lock:
                active_predicts -= 1
            return [{"rec_texts": [f"{self.instance_id}:{image_path}"]}]

    worker = ocr.OCRWorker(
        ocr_factory=FakePaddleOCR,
        cache_dir=tmp_path / "models",
        max_workers=2,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker.process_image, ["a.jpg", "b.jpg"]))

    result_pairs = {tuple(result.split(":", 1)) for result in results}
    assert {int(instance_id) for instance_id, _ in result_pairs} == {1, 2}
    assert {image_path for _, image_path in result_pairs} == {"a.jpg", "b.jpg"}
    assert sorted(instance_ids) == [1, 2]
    assert max_active_predicts == 2


def test_prediction_rec_texts_are_extracted_from_common_shapes(monkeypatch):
    ocr = load_ocr_module(monkeypatch)

    object_result = types.SimpleNamespace(rec_texts=["alpha", "beta"])
    assert ocr.extract_rec_texts([object_result]) == ["alpha", "beta"]
    assert ocr.extract_rec_texts({"rec_texts": ["gamma", ""]}) == ["gamma"]


def test_ocr_worker_limit_is_testable(monkeypatch):
    ocr = load_ocr_module(monkeypatch)

    assert ocr.calculate_ocr_worker_limit(None) == 1
    assert ocr.calculate_ocr_worker_limit(0) == 1
    assert ocr.calculate_ocr_worker_limit(1) == 1
    assert ocr.calculate_ocr_worker_limit(2) == 2
    assert ocr.calculate_ocr_worker_limit(16) == 2
