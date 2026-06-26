import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import concurrent.futures

from pdf.ocr import get_ocr_worker, OCRWorker

def test_home_env_var_isolated():
    # pdf.ocr sets HOME at the top
    assert os.environ["HOME"] == str(Path(".venv").resolve())

@patch('pdf.ocr.PaddleOCR')
def test_singleton_initialization(mock_paddleocr):
    # Mock behavior of PaddleOCR
    mock_ocr_instance = MagicMock()
    mock_paddleocr.return_value = mock_ocr_instance
    mock_ocr_instance.ocr.return_value = [[[[[0,0], [1,0], [1,1], [0,1]], ("test text", 0.99)]]]

    worker1 = get_ocr_worker()
    worker2 = get_ocr_worker()
    
    # Ensure singleton
    assert worker1 is worker2
    
    # OCR shouldn't be initialized until first use
    assert worker1._ocr is None
    
    # First use
    result1 = worker1.process_image("dummy.jpg")
    
    # Initialization happened
    mock_paddleocr.assert_called_once()
    
    # Second use
    result2 = worker1.process_image("dummy2.jpg")
    
    # No additional initialization
    mock_paddleocr.assert_called_once()

def test_concurrency_limit():
    worker = get_ocr_worker()
    
    cpu_count = os.cpu_count() or 1
    expected_limit = max(1, min(cpu_count // 2, 2))
    
    assert isinstance(worker.executor, concurrent.futures.ThreadPoolExecutor)
    assert worker.executor._max_workers == expected_limit
