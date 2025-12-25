# ai/plate_recognition.py

import re
from typing import Optional

try:
    import easyocr
    import cv2
except ImportError:
    easyocr = None
    cv2 = None


PLATE_REGEX = re.compile(r"\d{2}[A-Z0-9]{1,2}\d{4,5}")


def _normalize_plate(text: str) -> Optional[str]:
    """
    Lọc chuỗi OCR → chỉ lấy phần giống biển số VN.
    """
    if not text:
        return None

    # Bỏ khoảng trắng, dấu gạch…
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text).upper()

    match = PLATE_REGEX.search(cleaned)
    if match:
        return match.group(0)
    return None


def read_plate_from_image(image_path: str) -> Optional[str]:
    """
    Đọc biển số từ file ảnh.
    Trả về: chuỗi biển số chuẩn hóa hoặc None nếu thất bại.
    """
    if easyocr is None or cv2 is None:
        print("[WARN] EasyOCR / OpenCV chưa được cài, trả về giả lập.")
        # giả lập để test front-end
        return "59A12345"

    reader = easyocr.Reader(['en'], gpu=False)
    result = reader.readtext(image_path, detail=0)

    for text in result:
        plate = _normalize_plate(text)
        if plate:
            return plate

    return None


def read_plate_from_frame(frame) -> Optional[str]:
    """
    Đọc biển số trực tiếp từ frame OpenCV (numpy array).
    """
    if easyocr is None:
        print("[WARN] EasyOCR chưa được cài, trả về giả lập.")
        return "59A12345"

    # Tùy chỉnh theo yêu cầu: crop vùng biển số, resize…
    reader = easyocr.Reader(['en'], gpu=False)
    result = reader.readtext(frame, detail=0)

    for text in result:
        plate = _normalize_plate(text)
        if plate:
            return plate

    return None
