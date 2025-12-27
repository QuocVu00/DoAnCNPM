# ai/face_recognition.py

import os
import pickle
from typing import Optional, Dict, Any, List

try:
    import face_recognition
    import cv2
except ImportError:
    face_recognition = None
    cv2 = None


ENCODINGS_FILE = os.path.join(os.path.dirname(__file__), "face_encodings.pickle")


def load_known_faces() -> Dict[str, Any]:
    """
    Load file lưu embeddings khuôn mặt cư dân.
    Format: { resident_id: { 'encoding': [...], 'meta': {...} } }
    """
    if not os.path.exists(ENCODINGS_FILE):
        return {}

    with open(ENCODINGS_FILE, "rb") as f:
        data = pickle.load(f)
    return data


def save_known_faces(data: Dict[str, Any]) -> None:
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump(data, f)


def add_resident_face(resident_id: str, image_path: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """
    Admin upload ảnh khuôn mặt cư dân → gọi hàm này để lưu encoding.
    """
    if face_recognition is None:
        print("[WARN] face_recognition chưa cài, giả lập lưu thành công.")
        data = load_known_faces()
        data[resident_id] = {"encoding": [0.0] * 128, "meta": meta or {}}
        save_known_faces(data)
        return True

    image = face_recognition.load_image_file(image_path)
    encodings = face_recognition.face_encodings(image)
    if not encodings:
        print("[ERROR] Không tìm thấy khuôn mặt trong ảnh.")
        return False

    encoding = encodings[0]

    data = load_known_faces()
    data[resident_id] = {"encoding": encoding, "meta": meta or {}}
    save_known_faces(data)
    return True


def identify_resident_from_frame(frame, tolerance: float = 0.5) -> Optional[str]:
    """
    Nhận diện cư dân từ frame webcam.
    Trả về resident_id hoặc None.
    """
    if face_recognition is None:
        print("[WARN] face_recognition chưa cài → trả giả lập resident_id='1'")
        return "1"

    known = load_known_faces()
    if not known:
        return None

    known_ids: List[str] = []
    known_encodings = []

    for rid, info in known.items():
        known_ids.append(rid)
        known_encodings.append(info["encoding"])

    rgb_frame = frame[:, :, ::-1]  # BGR -> RGB
    boxes = face_recognition.face_locations(rgb_frame)
    encodings = face_recognition.face_encodings(rgb_frame, boxes)

    for encoding in encodings:
        matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=tolerance)
        if True in matches:
            idx = matches.index(True)
            return known_ids[idx]

    return None
