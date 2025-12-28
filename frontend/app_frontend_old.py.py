from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
)
from datetime import datetime
import random
import string

app = Flask(__name__)
app.secret_key = "smart_parking_secret"

# ======= DATA GIẢ ĐỂ DEMO =======

# Cư dân demo
RESIDENTS = [
    {
        "id": 1,
        "username": "qvu1803",
        "password": "18032005",
        "full_name": "Nguyễn Văn A",
        "floor": 3,
        "room": "305",
        "plate_number": "59A12345",
        "vehicle_type": "motorbike",
        "status": "active",
        "backup_code": "ABC123",  # mã cố định
        "is_in_parking": True,
        "citizen_id": "012345678901",
        "email": "a@example.com",
        "phone": "0909123456",
    },
    {
        "id": 2,
        "username": "nluong1801",
        "password": "18012005",
        "full_name": "Trần Thị B",
        "floor": 5,
        "room": "502",
        "plate_number": "51F56789",
        "vehicle_type": "car",
        "status": "active",
        "backup_code": "XYZ789",
        "is_in_parking": False,
        "citizen_id": "012345678902",
        "email": "b@example.com",
        "phone": "0909987654",
    },
]

# Log ra/vào cư dân demo
RESIDENT_LOGS = [
    {
        "resident_id": 1,
        "timestamp": "2025-12-26 08:00",
        "event_type": "IN",
        "plate_number": "59A12345",
    },
    {
        "resident_id": 1,
        "timestamp": "2025-12-26 18:00",
        "event_type": "OUT",
        "plate_number": "59A12345",
    },
]

# Vé khách ngoài: ticket_code -> session info
GUEST_SESSIONS = {}  # ticket_code : dict

# Trò chuyện admin <-> cư dân
# mỗi message: {resident_id, sender ('resident'|'admin'), content, time}
CHATS = []


# ========== HELPER ==========
def generate_ticket_code(length=6):
    return "".join(random.choices(string.digits, k=length))


def remove_vietnamese_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt, trả về chuỗi chỉ còn a-z0-9."""
    import re

    mapping = {
        ord("à"): "a",
        ord("á"): "a",
        ord("ả"): "a",
        ord("ã"): "a",
        ord("ạ"): "a",
        ord("ă"): "a",
        ord("ằ"): "a",
        ord("ắ"): "a",
        ord("ẳ"): "a",
        ord("ẵ"): "a",
        ord("ặ"): "a",
        ord("â"): "a",
        ord("ầ"): "a",
        ord("ấ"): "a",
        ord("ẩ"): "a",
        ord("ẫ"): "a",
        ord("ậ"): "a",
        ord("è"): "e",
        ord("é"): "e",
        ord("ẻ"): "e",
        ord("ẽ"): "e",
        ord("ẹ"): "e",
        ord("ê"): "e",
        ord("ề"): "e",
        ord("ế"): "e",
        ord("ể"): "e",
        ord("ễ"): "e",
        ord("ệ"): "e",
        ord("ì"): "i",
        ord("í"): "i",
        ord("ỉ"): "i",
        ord("ĩ"): "i",
        ord("ị"): "i",
        ord("ò"): "o",
        ord("ó"): "o",
        ord("ỏ"): "o",
        ord("õ"): "o",
        ord("ọ"): "o",
        ord("ô"): "o",
        ord("ồ"): "o",
        ord("ố"): "o",
        ord("ổ"): "o",
        ord("ỗ"): "o",
        ord("ộ"): "o",
        ord("ơ"): "o",
        ord("ờ"): "o",
        ord("ớ"): "o",
        ord("ở"): "o",
        ord("ỡ"): "o",
        ord("ợ"): "o",
        ord("ù"): "u",
        ord("ú"): "u",
        ord("ủ"): "u",
        ord("ũ"): "u",
        ord("ụ"): "u",
        ord("ư"): "u",
        ord("ừ"): "u",
        ord("ứ"): "u",
        ord("ử"): "u",
        ord("ữ"): "u",
        ord("ự"): "u",
        ord("ỳ"): "y",
        ord("ý"): "y",
        ord("ỷ"): "y",
        ord("ỹ"): "y",
        ord("ỵ"): "y",
        ord("đ"): "d",
        ord("À"): "a",
        ord("Á"): "a",
        ord("Ả"): "a",
        ord("Ã"): "a",
        ord("Ạ"): "a",
        ord("Ă"): "a",
        ord("Ằ"): "a",
        ord("Ắ"): "a",
        ord("Ẳ"): "a",
        ord("Ẵ"): "a",
        ord("Ặ"): "a",
        ord("Â"): "a",
        ord("Ầ"): "a",
        ord("Ấ"): "a",
        ord("Ẩ"): "a",
        ord("Ẫ"): "a",
        ord("Ậ"): "a",
        ord("È"): "e",
        ord("É"): "e",
        ord("Ẻ"): "e",
        ord("Ẽ"): "e",
        ord("Ẹ"): "e",
        ord("Ê"): "e",
        ord("Ề"): "e",
        ord("Ế"): "e",
        ord("Ể"): "e",
        ord("Ễ"): "e",
        ord("Ệ"): "e",
        ord("Ì"): "i",
        ord("Í"): "i",
        ord("Ỉ"): "i",
        ord("Ĩ"): "i",
        ord("Ị"): "i",
        ord("Ò"): "o",
        ord("Ó"): "o",
        ord("Ỏ"): "o",
        ord("Õ"): "o",
        ord("Ọ"): "o",
        ord("Ô"): "o",
        ord("Ồ"): "o",
        ord("Ố"): "o",
        ord("Ổ"): "o",
        ord("Ỗ"): "o",
        ord("Ộ"): "o",
        ord("Ơ"): "o",
        ord("Ờ"): "o",
        ord("Ớ"): "o",
        ord("Ở"): "o",
        ord("Ỡ"): "o",
        ord("Ợ"): "o",
        ord("Ù"): "u",
        ord("Ú"): "u",
        ord("Ủ"): "u",
        ord("Ũ"): "u",
        ord("Ụ"): "u",
        ord("Ư"): "u",
        ord("Ừ"): "u",
        ord("Ứ"): "u",
        ord("Ử"): "u",
        ord("Ữ"): "u",
        ord("Ự"): "u",
        ord("Ỳ"): "y",
        ord("Ý"): "y",
        ord("Ỷ"): "y",
        ord("Ỹ"): "y",
        ord("Ỵ"): "y",
        ord("Đ"): "d",
    }
    text = text.translate(mapping)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def generate_username_from_name(full_name: str) -> str:
    """
    Lấy chữ đệm + tên (không dấu, thường, liền nhau).
    Ví dụ: 'Võ Ngọc Lượng' -> 'ngocluong'
    """
    parts = full_name.strip().split()
    if not parts:
        return "user"
    if len(parts) <= 2:
        core = parts[-1]
    else:
        core = "".join(parts[1:])  # chữ đệm + tên
    base = remove_vietnamese_diacritics(core)
    if not base:
        base = "user"
    # đảm bảo không trùng
    username = base
    suffix = 1
    existing = {r["username"] for r in RESIDENTS}
    while username in existing:
        suffix += 1
        username = f"{base}{suffix}"
    return username


def generate_password_from_phone(phone: str) -> str:
    """Lấy 8 số cuối của SĐT. Nếu không đủ 8 số thì dùng toàn bộ."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) >= 8:
        return digits[-8:]
    return digits or "12345678"


def get_chat_messages(resident_id: int):
    """Lấy danh sách tin nhắn của cư dân theo thứ tự thời gian."""
    return [m for m in CHATS if m["resident_id"] == resident_id]


# =========================================================
# 1. LOGIN CHUNG (TRANG ĐẦU TIÊN) + LOGOUT
# =========================================================
@app.route("/", methods=["GET", "POST"])
def login():
    """
    Trang đăng nhập chung.
    - admin / 123456  -> vào trang admin cư dân
    - qvu1803 / 18032005  -> cư dân 1
    - nluong1801 / 18012005 -> cư dân 2
    """
    role = session.get("role")
    if request.method == "GET" and role:
        if role == "admin":
            return redirect(url_for("admin_residents"))
        if role == "resident":
            return redirect(url_for("resident_dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # ==== Check tài khoản ADMIN ====
        if username == "admin" and password == "123456":
            session.clear()
            session["role"] = "admin"
            session["admin_logged_in"] = True
            return redirect(url_for("admin_residents"))

        # ==== Check tài khoản CƯ DÂN ====
        for r in RESIDENTS:
            if (
                r["username"] == username
                and r["password"] == password
                and r["status"] == "active"
            ):
                session.clear()
                session["role"] = "resident"
                session["resident_id"] = r["id"]
                return redirect(url_for("resident_dashboard"))

        return render_template("login.html", error="Sai tài khoản hoặc mật khẩu")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Đăng xuất cho cả admin và cư dân."""
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# 2. DECORATOR KIỂM TRA QUYỀN
# =========================================================
def admin_required(func):
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


def resident_required(func):
    def wrapper(*args, **kwargs):
        if session.get("role") != "resident":
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


# =========================================================
# 3. TRANG CỔNG BÃI XE (CHO NHÂN VIÊN GATE)
# =========================================================
@app.route("/gate")
@admin_required
def gate_page():
    # giả sử cổng bãi xe chỉ admin/nhân viên dùng
    return render_template("gate.html")


# ---------- API GATE ----------
@app.route("/gate/guest/checkin", methods=["POST"])
def gate_guest_checkin():
    ticket = generate_ticket_code()
    now = datetime.now()

    GUEST_SESSIONS[ticket] = {
        "plate_number": "CHUA_OCR",  # demo, sau này dùng OCR
        "checkin_time": now,
        "checkout_time": None,
        "amount": 0,
        "status": "IN",
    }
    return jsonify({"success": True, "ticket_code": ticket})


@app.route("/gate/guest/checkout", methods=["POST"])
def gate_guest_checkout():
    data = request.get_json() or {}
    ticket = data.get("ticket_code", "")
    session_info = GUEST_SESSIONS.get(ticket)

    if not session_info or session_info["status"] == "OUT":
        return jsonify({"success": False, "message": "Ticket invalid"})

    now = datetime.now()
    checkin = session_info["checkin_time"]
    hours = (now - checkin).total_seconds() / 3600
    if hours < 0.5:
        hours = 0.5  # tối thiểu 0.5 giờ

    price_per_hour = 5000
    amount = int(hours * price_per_hour)

    session_info["checkout_time"] = now
    session_info["amount"] = amount
    session_info["status"] = "OUT"

    return jsonify({"success": True, "hours": round(hours, 2), "amount": amount})


@app.route("/gate/resident/face", methods=["POST"])
def gate_resident_face():
    """
    Demo: luôn nhận ra cư dân id=1.
    Sau này nhóm bạn nối thật với AI khuôn mặt.
    """
    res = RESIDENTS[0]
    return jsonify(
        {
            "success": True,
            "resident_id": res["id"],
            "resident_name": res["full_name"],
        }
    )


@app.route("/gate/resident/backup-login", methods=["POST"])
def gate_resident_backup_login():
    data = request.get_json() or {}
    code = data.get("backup_code", "")

    for r in RESIDENTS:
        if r["backup_code"] == code and r["status"] == "active":
            return jsonify({"success": True, "resident_name": r["full_name"]})

    return jsonify({"success": False})


# =========================================================
# 4. KHU VỰC ADMIN
# =========================================================
@app.route("/admin/residents", methods=["GET"])
@admin_required
def admin_residents():
    return render_template("admin/residents.html", residents=RESIDENTS)


@app.route("/admin/residents/create", methods=["POST"])
@admin_required
def admin_create_resident():
    global RESIDENTS
    new_id = max(r["id"] for r in RESIDENTS) + 1 if RESIDENTS else 1

    full_name = request.form.get("full_name", "").strip()
    floor = int(request.form.get("floor", 0) or 0)
    room = request.form.get("room", "").strip()
    plate_number = request.form.get("plate_number", "").strip()
    vehicle_type = request.form.get("vehicle_type", "motorbike")
    citizen_id = request.form.get("citizen_id", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    # Tự tạo username & password
    username = generate_username_from_name(full_name)
    password = generate_password_from_phone(phone)

    res = {
        "id": new_id,
        "username": username,
        "password": password,
        "full_name": full_name,
        "floor": floor,
        "room": room,
        "plate_number": plate_number,
        "vehicle_type": vehicle_type,
        "status": "active",
        "backup_code": "CODE" + str(new_id),
        "is_in_parking": False,
        "citizen_id": citizen_id,
        "email": email,
        "phone": phone,
    }
    RESIDENTS.append(res)
    # TODO: xử lý file face_image và gọi thêm ai.add_resident_face(...)
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/<int:resident_id>/reset_backup", methods=["POST"])
@admin_required
def admin_reset_backup_code(resident_id):
    for r in RESIDENTS:
        if r["id"] == resident_id:
            r["backup_code"] = generate_ticket_code(6)
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/<int:resident_id>/disable", methods=["POST"])
@admin_required
def admin_disable_resident(resident_id):
    for r in RESIDENTS:
        if r["id"] == resident_id:
            r["status"] = "disabled"
    return redirect(url_for("admin_residents"))


@app.route("/admin/guests")
@admin_required
def admin_guests():
    date_str = request.args.get("date")
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    plate = request.args.get("plate", "")
    ticket_code = request.args.get("ticket_code", "")
    status = request.args.get("status", "")

    guests = []
    for code, sess in GUEST_SESSIONS.items():
        # lọc theo ngày (so sánh checkin)
        checkin = sess["checkin_time"]
        if checkin.strftime("%Y-%m-%d") != date_str:
            continue
        if plate and plate not in sess.get("plate_number", ""):
            continue
        if ticket_code and ticket_code != code:
            continue
        if status and status != sess["status"]:
            continue

        hours = 0
        if sess["checkout_time"]:
            hours = (sess["checkout_time"] - sess["checkin_time"]).total_seconds() / 3600

        guests.append(
            {
                "plate_number": sess.get("plate_number", ""),
                "ticket_code": code,
                "checkin_time": sess["checkin_time"].strftime("%H:%M"),
                "checkout_time": sess["checkout_time"].strftime("%H:%M")
                if sess["checkout_time"]
                else None,
                "hours": hours,
                "amount": sess["amount"],
                "status": sess["status"],
            }
        )

    revenue = sum(g["amount"] for g in guests)
    summary = {"date": date_str, "count": len(guests), "revenue": revenue}
    filters = {"plate": plate, "ticket_code": ticket_code, "status": status}

    return render_template(
        "admin/guests.html",
        guests=guests,
        summary=summary,
        selected_date=date_str,
        filters=filters,
    )


@app.route("/admin/report")
@admin_required
def admin_report_page():
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("admin/report.html", today=today)


@app.route("/admin/report/daily")
@admin_required
def admin_report_daily():
    date_str = request.args.get("date")
    detail = request.args.get("detail") == "1"

    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # dữ liệu demo từ GUEST_SESSIONS
    guest_sessions = []
    for code, sess in GUEST_SESSIONS.items():
        if sess["checkin_time"].strftime("%Y-%m-%d") != date_str:
            continue
        guest_sessions.append((code, sess))

    revenue = sum(s["amount"] for _, s in guest_sessions)

    data = {
        "success": True,
        "date": date_str,
        "resident_count": len(RESIDENTS),
        "guest_count": len(guest_sessions),
        "revenue": revenue,
    }

    if detail:
        sessions = []
        for code, s in guest_sessions:
            sessions.append(
                {
                    "plate_number": s.get("plate_number", ""),
                    "ticket_code": code,
                    "checkin_time": s["checkin_time"].strftime("%H:%M"),
                    "checkout_time": s["checkout_time"].strftime("%H:%M")
                    if s["checkout_time"]
                    else "",
                    "amount": s["amount"],
                }
            )
        data["sessions"] = sessions

    return jsonify(data)


# ============== ADMIN CHAT ==============
@app.route("/admin/chat")
@admin_required
def admin_chat():
    # chọn cư dân đang chat
    resident_id = request.args.get("resident_id", type=int)
    if resident_id is None:
        resident_id = RESIDENTS[0]["id"] if RESIDENTS else None

    active_resident = None
    if resident_id is not None:
        active_resident = next((r for r in RESIDENTS if r["id"] == resident_id), None)

    messages = get_chat_messages(resident_id) if active_resident else []

    return render_template(
        "admin/chat.html",
        residents=RESIDENTS,
        active_resident=active_resident,
        messages=messages,
    )


@app.route("/admin/chat/send", methods=["POST"])
@admin_required
def admin_chat_send():
    resident_id = int(request.form.get("resident_id"))
    content = request.form.get("content", "").strip()
    if content:
        CHATS.append(
            {
                "resident_id": resident_id,
                "sender": "admin",
                "content": content,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
    return redirect(url_for("admin_chat", resident_id=resident_id))


# =========================================================
# 5. KHU VỰC CƯ DÂN
# =========================================================
@app.route("/resident/dashboard")
@resident_required
def resident_dashboard():
    rid = session["resident_id"]
    resident = next(r for r in RESIDENTS if r["id"] == rid)
    logs = [log for log in RESIDENT_LOGS if log["resident_id"] == rid]
    messages = get_chat_messages(rid)
    return render_template(
        "resident/dashboard.html",
        resident=resident,
        logs=logs,
        messages=messages,
    )


@app.route("/resident/chat/send", methods=["POST"])
@resident_required
def resident_chat_send():
    rid = session["resident_id"]
    content = request.form.get("content", "").strip()
    if content:
        CHATS.append(
            {
                "resident_id": rid,
                "sender": "resident",
                "content": content,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
    return redirect(url_for("resident_dashboard"))


# ==========================
if __name__ == "__main__":
    app.run(debug=True)
