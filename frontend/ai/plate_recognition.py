# frontend/ai/plate_recognition.py

import re
from typing import Optional

try:
    import easyocr
    import cv2
    import numpy as np
except ImportError:
    easyocr = None
    cv2 = None
    np = None

# Một số pattern cơ bản cho biển số VN
# Ví dụ: 77X55040, 59AB95454, 51F1234
PLATE_PATTERNS = [
    re.compile(r"\d{2}[A-Z0-9]{1,3}\d{3,5}"),  # 77X55040, 59AB9545, 51F1234
    re.compile(r"[A-Z0-9]{6,9}")               # fallback: chuỗi 6–9 ký tự liền nhau
]


def _normalize_plate(candidate: str) -> Optional[str]:
    """
    Nhận 1 chuỗi thô, làm sạch rồi cố gắng trích ra biển số.

    - Bỏ hết ký tự không phải chữ hoặc số.
    - Thử từng regex trong PLATE_PATTERNS.
    - Nếu không khớp, nhưng độ dài 6–9 thì vẫn coi là biển số tạm.
    """
    if not candidate:
        return None

    cleaned = re.sub(r"[^A-Za-z0-9]", "", candidate).upper()
    if not cleaned:
        return None

    # Thử từng pattern
    for rx in PLATE_PATTERNS:
        m = rx.search(cleaned)
        if m:
            return m.group(0)

    # Fallback: nếu độ dài ở khoảng hợp lý thì chấp nhận luôn
    if 6 <= len(cleaned) <= 9:
        return cleaned

    return None


def read_plate_from_image(image_bytes: bytes) -> Optional[str]:
    """
    Đọc biển số từ dữ liệu ảnh dạng bytes (PNG/JPEG…).

    - image_bytes: kết quả base64.b64decode(...) từ dataURL browser.
    - Trả về: chuỗi biển số chuẩn hóa, hoặc None nếu không nhận diện được.
    """
    if easyocr is None or cv2 is None or np is None:
        print("[WARN] EasyOCR/OpenCV/Numpy chưa cài, không thể đọc biển số.")
        return None

    if not image_bytes:
        return None

    # Giải mã bytes -> ảnh OpenCV
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        print("[WARN] Không decode được ảnh từ bytes.")
        return None

    # Gọi EasyOCR
    reader = easyocr.Reader(['en'], gpu=False)
    results = reader.readtext(frame, detail=0)  # chỉ lấy text

    print("[DEBUG] OCR texts:", results)

    if not results:
        print("[INFO] No OCR text detected.")
        return None

    # 1) Thử từng text riêng lẻ
    for text in results:
        plate = _normalize_plate(text)
        if plate:
            print("[INFO] Plate from single text:", plate)
            return plate

    # 2) Ghép tất cả text lại rồi lọc
    joined_raw = "".join(results)              # ví dụ: "77 - X55 0 4 0"
    plate = _normalize_plate(joined_raw)
    if plate:
        print("[INFO] Plate from joined text:", plate)
        return plate

    # 3) Ghép sau khi bỏ khoảng trắng từng đoạn
    joined_no_space = "".join(t.replace(" ", "") for t in results)
    plate = _normalize_plate(joined_no_space)
    if plate:
        print("[INFO] Plate from joined no-space text:", plate)
        return plate

    print("[INFO] No valid plate found from OCR after normalization.")
    return None
