from __future__ import annotations

import concurrent.futures
import inspect
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / ".paddleocr"
CACHE_ENV = "PDF_STUDY_PADDLEOCR_CACHE"
PADDLEOCR_CACHE_ENV = "PADDLEOCR_HOME"
PADDLE_CACHE_ENVS = (PADDLEOCR_CACHE_ENV, "PADDLE_PDX_CACHE_HOME")
PADDLE_SOURCE_CHECK_ENV = "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"
DET_MODEL_ENV = "PDF_STUDY_PADDLEOCR_DET_MODEL"
DET_LIMIT_SIDE_LEN_ENV = "PDF_STUDY_PADDLEOCR_DET_LIMIT_SIDE_LEN"
CPU_THREADS_ENV = "PDF_STUDY_PADDLEOCR_CPU_THREADS"

# pdf-study가 사용자에게 제공하는 OCR 언어는 두 가지로 한정한다. 환경변수로
# 임의의 인식 모델을 주입하면 작업별 언어 선택 계약을 우회하므로 허용하지 않는다.
OCR_LANGUAGE_MODELS = {
    "korean": "korean_PP-OCRv5_mobile_rec",
    "english": "en_PP-OCRv5_mobile_rec",
}

PaddleOCRFactory = Callable[..., Any]

_workers: dict[str, "OCRWorker"] = {}
_worker_lock = threading.Lock()
_chapter_executor: concurrent.futures.ThreadPoolExecutor | None = None
_chapter_executor_lock = threading.Lock()
_AUTO_CPU_COUNT = object()


def resolve_model_cache_dir(cache_dir: str | os.PathLike[str] | None = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir).expanduser().resolve()

    configured = os.environ.get(CACHE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()

    return DEFAULT_CACHE_DIR


def prepare_model_cache(cache_dir: str | os.PathLike[str] | None = None) -> Path:
    resolved = resolve_model_cache_dir(cache_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    for env_name in PADDLE_CACHE_ENVS:
        os.environ[env_name] = str(resolved)
    os.environ.setdefault(PADDLE_SOURCE_CHECK_ENV, "True")
    return resolved


def recognition_model_name(ocr_language: str) -> str:
    try:
        return OCR_LANGUAGE_MODELS[ocr_language]
    except KeyError as exc:
        raise ValueError(f"unsupported OCR language: {ocr_language!r}") from exc


def required_model_names(ocr_language: str = "korean") -> list[str]:
    return [
        os.environ.get(DET_MODEL_ENV, "PP-OCRv5_mobile_det"),
        recognition_model_name(ocr_language),
    ]


def model_cache_status(
    cache_dir: str | os.PathLike[str] | None = None,
    ocr_language: str = "korean",
) -> dict[str, Any]:
    resolved = resolve_model_cache_dir(cache_dir)
    models = []
    all_cached = True
    for name in required_model_names(ocr_language):
        model_dir = resolved / "official_models" / name
        cached = (
            (model_dir / "inference.json").exists()
            and (model_dir / "inference.pdiparams").exists()
        )
        all_cached = all_cached and cached
        models.append({
            "name": name,
            "path": str(model_dir),
            "cached": cached,
        })
    return {
        "cache_dir": str(resolved),
        "ocr_language": ocr_language,
        "models": models,
        "all_cached": all_cached,
    }


def models_cached(
    cache_dir: str | os.PathLike[str] | None = None,
    ocr_language: str = "korean",
) -> bool:
    return bool(model_cache_status(cache_dir, ocr_language)["all_cached"])


def calculate_ocr_worker_limit(cpu_count: int | None | object = _AUTO_CPU_COUNT) -> int:
    if cpu_count is _AUTO_CPU_COUNT:
        cpu_count = os.cpu_count()
    if not cpu_count or cpu_count <= 1:
        return 1
    return 2


def _load_paddleocr_factory() -> PaddleOCRFactory:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is not installed. Run scripts/setup_mcp.sh to prepare the MCP environment."
        ) from exc
    return PaddleOCR


def _paddleocr_kwargs(factory: PaddleOCRFactory, ocr_language: str = "korean") -> dict[str, Any]:
    desired_kwargs: dict[str, Any] = {
        "device": "cpu",
        # PaddleOCR 3.x enables document orientation/unwarping helpers by
        # default. They load extra models and are too memory-heavy for local
        # book-page OCR on small machines.
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_detection_model_name": os.environ.get(
            DET_MODEL_ENV, "PP-OCRv5_mobile_det"
        ),
        "text_recognition_model_name": recognition_model_name(ocr_language),
        "text_recognition_batch_size": 1,
        "text_det_limit_side_len": int(
            os.environ.get(DET_LIMIT_SIDE_LEN_ENV, "960")
        ),
        "text_det_limit_type": "max",
        "cpu_threads": int(os.environ.get(CPU_THREADS_ENV, "2")),
    }
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return desired_kwargs

    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs:
        return desired_kwargs

    return {
        name: value
        for name, value in desired_kwargs.items()
        if name in parameters
    }


def _iter_prediction_items(prediction: Any) -> Iterable[Any]:
    if prediction is None:
        return ()
    if isinstance(prediction, dict):
        return (prediction,)
    if isinstance(prediction, (str, bytes)):
        return ()
    if isinstance(prediction, Iterable):
        return prediction
    return (prediction,)


def extract_rec_texts(prediction: Any) -> list[str]:
    texts: list[str] = []
    for item in _iter_prediction_items(prediction):
        rec_texts = None
        if isinstance(item, dict):
            rec_texts = item.get("rec_texts")
        else:
            rec_texts = getattr(item, "rec_texts", None)

        if rec_texts is None:
            continue

        for text in rec_texts:
            if text is None:
                continue
            normalized = str(text).strip()
            if normalized:
                texts.append(normalized)
    return texts


def get_chapter_executor() -> concurrent.futures.ThreadPoolExecutor:
    """프로세스 전역 챕터 OCR executor.

    set_chapters 호출이 여러 개 겹쳐도 동시에 OCR되는 챕터 수는
    calculate_ocr_worker_limit() 상한을 넘지 않는다.
    """
    global _chapter_executor
    if _chapter_executor is None:
        with _chapter_executor_lock:
            if _chapter_executor is None:
                _chapter_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=calculate_ocr_worker_limit()
                )
    return _chapter_executor


def submit_chapter_ocr(
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> concurrent.futures.Future[Any]:
    return get_chapter_executor().submit(fn, *args, **kwargs)


def _reset_chapter_executor_for_tests() -> None:
    global _chapter_executor
    with _chapter_executor_lock:
        executor = _chapter_executor
        _chapter_executor = None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


class OCRWorker:
    def __init__(
        self,
        *,
        ocr_language: str = "korean",
        ocr_factory: PaddleOCRFactory | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        max_workers: int | None = None,
    ) -> None:
        recognition_model_name(ocr_language)
        self.ocr_language = ocr_language
        self._ocr_factory = ocr_factory
        self._cache_dir = cache_dir
        self._factory: PaddleOCRFactory | None = None
        self._thread_state = threading.local()
        self._init_lock = threading.Lock()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers or calculate_ocr_worker_limit()
        )

    def _get_ocr(self) -> Any:
        ocr = getattr(self._thread_state, "ocr", None)
        if ocr is not None:
            return ocr

        with self._init_lock:
            prepare_model_cache(self._cache_dir)
            if self._factory is None:
                self._factory = self._ocr_factory or _load_paddleocr_factory()
            ocr = self._factory(**_paddleocr_kwargs(self._factory, self.ocr_language))

        self._thread_state.ocr = ocr
        return ocr

    def process_image(self, image_path: str | os.PathLike[str]) -> str:
        future = self.executor.submit(self._extract_text, image_path)
        return future.result()

    def prepare(self) -> dict[str, Any]:
        before = model_cache_status(self._cache_dir, self.ocr_language)
        started = time.perf_counter()
        future = self.executor.submit(self._get_ocr)
        future.result()
        elapsed = time.perf_counter() - started
        after = model_cache_status(self._cache_dir, self.ocr_language)
        return {
            "cache_dir": after["cache_dir"],
            "models": after["models"],
            "all_cached": after["all_cached"],
            "download_required": not before["all_cached"],
            "model_loaded": True,
            "elapsed_sec": round(elapsed, 3),
        }

    def _extract_text(self, image_path: str | os.PathLike[str]) -> str:
        ocr = self._get_ocr()
        if not hasattr(ocr, "predict"):
            raise RuntimeError("The installed PaddleOCR object does not provide predict(image_path).")

        prediction = ocr.predict(str(image_path))
        return "\n".join(extract_rec_texts(prediction))


def get_ocr_worker(ocr_language: str = "korean") -> OCRWorker:
    recognition_model_name(ocr_language)
    with _worker_lock:
        worker = _workers.get(ocr_language)
        if worker is None:
            worker = OCRWorker(ocr_language=ocr_language)
            _workers[ocr_language] = worker
        return worker
