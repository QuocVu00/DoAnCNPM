# frontend/ai/plate_recognition.py

from __future__ import annotations

import re
from typing import Optional, List, Tuple

# Import an toàn: nếu thiếu lib thì trả None để app không crash
try:
    import easyocr
    import cv2
    import numpy as np
except Exception:
    easyocr = None
    cv2 = None
    np = None


# =========================================================
# Regex biển số VN (khá linh hoạt, đủ dùng cho demo)
# - Ví dụ: 59AB95454, 77X55040, 51F1234, 30E12345...
# =========================================================
PLATE_PATTERNS: List[re.Pattern] = [
    # 2 số + 1-2 chữ + 4-6 số: 59AB95454, 77X55040
    re.compile(r"\b\d{2}[A-Z]{1,2}\d{4,6}\b"),
    # 2 số + 1 chữ + 3-5 số: 51F1234
    re.compile(r"\b\d{2}[A-Z]\d{3,5}\b"),
]

# Một số ký tự OCR hay nhầm
_OCR_FIX_MAP = str.maketrans({
    "O": "0",
    "Q": "0",
    "D": "0",  # đôi khi OCR nhầm D thành 0 (tuỳ font)
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
})


_reader = None


def _ensure_reader():
    """Khởi tạo EasyOCR reader 1 lần."""
    global _reader
    if easyocr is None:
        return None
    if _reader is None:
        # 'en' thường đủ cho biển số dạng Latin
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def _normalize_raw_text(text: str) -> str:
    """
    Chuẩn hoá text OCR:
    - upper
    - bỏ ký tự lạ (giữ A-Z0-9)
    - thay thế ký tự hay nhầm (O->0, I->1,...)
    """
    if not text:
        return ""
    t = text.upper().strip()
    t = re.sub(r"[^A-Z0-9]", "", t)          # bỏ dấu cách, dấu -, ...
    t = t.translate(_OCR_FIX_MAP)           # sửa nhầm phổ biến
    return t


def _extract_plate(normalized: str) -> Optional[str]:
    """Tìm chuỗi biển số hợp lệ trong string đã normalize."""
    if not normalized:
        return None

    for pat in PLATE_PATTERNS:
        m = pat.search(normalized)
        if m:
            return m.group(0)

    # fallback: đôi khi OCR dính liền nhiều ký tự -> thử quét mọi cửa sổ độ dài 7..10
    # (vì biển số thường nằm khoảng đó)
    for L in range(10, 6, -1):
        for i in range(0, max(0, len(normalized) - L + 1)):
            chunk = normalized[i:i+L]
            for pat in PLATE_PATTERNS:
                if pat.fullmatch(chunk):
                    return chunk

    return None


def _decode_bytes_to_bgr(image_bytes: bytes):
    """Decode bytes PNG/JPG -> ảnh BGR (OpenCV)."""
    if cv2 is None or np is None:
        return None
    if not image_bytes:
        return None
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def _preprocess_variants(img_bgr) -> List:
    """
    Tạo vài biến thể ảnh để OCR dễ đọc hơn:
    - ảnh gốc
    - gray
    - threshold
    """
    if cv2 is None:
        return [img_bgr]

    variants = [img_bgr]

    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        variants.append(gray)

        # làm mượt nhẹ rồi threshold
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, th1 = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(th1)

        th2 = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 5
        )
        variants.append(th2)
    except Exception:
        pass

    return variants


def read_plate_from_image(image_bytes: bytes) -> Optional[str]:
    """
    Hàm app.py đang gọi:
        plate_text = read_plate_from_image(img_bytes)

    Input: bytes (ảnh PNG/JPG)
    Output: string biển số (VD: '59AB95454') hoặc None
    """
    if easyocr is None or cv2 is None or np is None:
        print("[WARN] EasyOCR/OpenCV/Numpy chưa cài -> không thể OCR biển số.")
        return None

    img = _decode_bytes_to_bgr(image_bytes)
    if img is None:
        print("[WARN] Không decode được bytes ảnh.")
        return None

    reader = _ensure_reader()
    if reader is None:
        print("[WARN] Không init được EasyOCR reader.")
        return None

    # OCR trên nhiều biến thể ảnh, lấy cái ra được plate trước
    variants = _preprocess_variants(img)

    # Dùng detail=1 để có confidence; ưu tiên chuỗi có conf cao
    best: Tuple[float, Optional[str]] = (0.0, None)

    for idx, im in enumerate(variants):
        try:
            results = reader.readtext(im, detail=1)
        except Exception as e:
            print(f"[WARN] OCR error variant#{idx}:", e)
            continue

        # results: List[(bbox, text, conf)]
        raw_texts = []
        for item in results:
            if len(item) >= 3:
                text = item[1] or ""
                conf = float(item[2] or 0.0)
            else:
                text = str(item)
                conf = 0.0

            raw_texts.append(text)

            norm = _normalize_raw_text(text)
            plate = _extract_plate(norm)
            if plate:
                if conf > best[0]:
                    best = (conf, plate)

        # debug
        print(f"[DEBUG OCR variant#{idx}] raw_texts =", raw_texts)
        if best[1]:
            # nếu đã có biển số conf khá, có thể return sớm
            if best[0] >= 0.35:
                print("[INFO] Plate found (early):", best[1], "conf=", best[0])
                return best[1]

        # fallback: ghép tất cả text lại rồi thử extract
        joined = _normalize_raw_text("".join(raw_texts))
        plate2 = _extract_plate(joined)
        if plate2 and best[1] is None:
            best = (max(best[0], 0.2), plate2)

    if best[1]:
        print("[INFO] Plate found:", best[1], "conf=", best[0])
        return best[1]

    print("[INFO] No valid plate found from OCR after normalization.")
    return None
    