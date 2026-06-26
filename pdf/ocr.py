import os
from pathlib import Path
import threading
import concurrent.futures

# Isolate the model download by setting HOME to the project's .venv
os.environ["HOME"] = str(Path(".venv").resolve())

try:
    from paddleocr import PaddleOCR
except ImportError:
    PaddleOCR = None

class OCRWorker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(OCRWorker, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._ocr = None
            self._init_lock = threading.Lock()
            cpu_count = os.cpu_count() or 1
            max_workers = max(1, min(cpu_count // 2, 2))
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
            self._initialized = True

    def _get_ocr(self):
        if self._ocr is None:
            with self._init_lock:
                if self._ocr is None:
                    self._ocr = PaddleOCR(use_angle_cls=True, lang='korean')
        return self._ocr

    def process_image(self, image_path: str):
        future = self.executor.submit(self._do_process, image_path)
        return future.result()

    def _do_process(self, image_path: str):
        ocr = self._get_ocr()
        return ocr.ocr(image_path, cls=True)

def get_ocr_worker() -> OCRWorker:
    return OCRWorker()
