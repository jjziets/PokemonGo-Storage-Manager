"""OCR engine abstraction — supports Tesseract and PaddleOCR.

Tesseract: fast, good for clean text on white backgrounds
PaddleOCR: slower but best accuracy on complex/animated backgrounds

Usage: tries Tesseract first, falls back to PaddleOCR if Tesseract fails.
"""

import logging
import re
import warnings
import os
import sys
import io
import cv2
import numpy as np
from PIL import Image

warnings.filterwarnings('ignore')
os.environ['GLOG_minloglevel'] = '3'
os.environ['FLAGS_log_prefix'] = '0'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

log = logging.getLogger(__name__)

# Singleton PaddleOCR instance (thread-safe)
import threading
_paddle_lock = threading.Lock()
_paddle_reader = None
_paddle_available = None


def _get_paddle():
    global _paddle_reader, _paddle_available
    if _paddle_available is not None:
        return _paddle_reader if _paddle_available else None
    with _paddle_lock:
        if _paddle_available is not None:
            return _paddle_reader if _paddle_available else None
        try:
            # Suppress ALL stdout/stderr during model loading
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()

            from paddleocr import PaddleOCR
            _paddle_reader = PaddleOCR(lang='en', ocr_version='PP-OCRv4')

            sys.stdout, sys.stderr = old_stdout, old_stderr
            _paddle_available = True
            log.info("PaddleOCR loaded")
        except Exception as e:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            _paddle_available = False
            log.warning("PaddleOCR failed: %s", e)
    return _paddle_reader if _paddle_available else None


def read_text_paddle(crop: np.ndarray) -> str:
    """Read text from an image crop using PaddleOCR.

    Args:
        crop: RGB numpy array

    Returns the detected text or empty string.
    """
    reader = _get_paddle()
    if not reader:
        return ""

    try:
        # Ensure RGB (PaddleOCR doesn't handle RGBA)
        if len(crop.shape) == 3 and crop.shape[2] == 4:
            crop = crop[:, :, :3]
        result = reader.ocr(crop)
        if result:
            for item in result:
                if isinstance(item, dict) and 'rec_texts' in item:
                    texts = item['rec_texts']
                    if texts:
                        return " ".join(texts).strip()
                # Legacy format fallback
                elif isinstance(item, list):
                    for line in item:
                        if line and len(line) >= 2:
                            text = line[1][0] if isinstance(line[1], tuple) else line[1]
                            return str(text).strip()
    except Exception as e:
        log.debug("PaddleOCR error: %s", e)

    return ""


def read_number_paddle(crop: np.ndarray) -> int:
    """Read a number from an image crop using PaddleOCR.

    Returns the number or -1 if not found.
    """
    text = read_text_paddle(crop)
    digits = re.sub(r'[^0-9]', '', text)
    if digits:
        return int(digits)
    return -1


def read_text_from_crop(crop: np.ndarray, whitelist: str = "",
                        use_paddle_fallback: bool = True) -> str:
    """Read text from a preprocessed image crop.

    Args:
        crop: grayscale or binary numpy array
        whitelist: character whitelist for Tesseract
        use_paddle_fallback: try PaddleOCR if Tesseract fails

    Returns the best text read.
    """
    import pytesseract

    # Tesseract first (fast)
    config = "--psm 7"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"

    text = pytesseract.image_to_string(crop, config=config).strip()

    # If Tesseract got a good result, use it
    if text and len(text) >= 2:
        return text

    # Try PaddleOCR as fallback
    if use_paddle_fallback:
        # PaddleOCR needs RGB, convert if grayscale
        if len(crop.shape) == 2:
            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB)
        else:
            crop_rgb = crop
        paddle_text = read_text_paddle(crop_rgb)
        if paddle_text and len(paddle_text) >= len(text):
            log.debug("PaddleOCR fallback: '%s' (Tesseract had: '%s')", paddle_text, text)
            return paddle_text

    return text


def read_number_from_crop(crop: np.ndarray, use_easyocr_fallback: bool = True) -> int:
    """Read a number from a preprocessed image crop.

    Returns the number or -1 if not found.
    Uses PaddleOCR as fallback (parameter name kept for compatibility).
    """
    # PaddleOCR needs RGB (not RGBA or grayscale)
    if len(crop.shape) == 2:
        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB)
    elif crop.shape[2] == 4:
        crop_rgb = crop[:, :, :3]
    else:
        crop_rgb = crop

    result = read_number_paddle(crop_rgb)
    if result > 0:
        return result

    # Tesseract fallback
    import pytesseract
    text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    digits = re.sub(r'[^0-9]', '', text)
    if digits:
        return int(digits)

    return -1
