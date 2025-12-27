from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash
)
from datetime import datetime
import random
import unicodedata

import os
import base64
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

<<<<<<< HEAD

from backend.config import Config
from backend.db import query_one, query_all, execute
from backend.routes_admin import admin_bp  # CHỈ GIỮ admin_bp
from frontend.ai.plate_recognition import read_plate_from_image  # AI nhận diện biển số
=======
from backend.config import Config
from backend.db import query_one, query_all, execute
from backend.routes_admin import admin_bp  # CHỈ GIỮ admin_bp
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# Thư mục lưu ảnh cho trạm cổng (gate kiosk)
GATE_UPLOAD_DIR = Path(app.static_folder) / "uploads" / "gate"
(GATE_UPLOAD_DIR / "plates").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "faces").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "scenes").mkdir(parents=True, exist_ok=True)

# đăng ký backend API
app.register_blueprint(admin_bp)


def require_role(*roles):
    return session.get("role") in roles


<<<<<<< HEAD
# ====== HỖ TRỢ: MÃ VÉ & TÍNH TIỀN KHÁCH VÃNG LAI ======
def generate_ticket_code():
    """
    Tạo mã vé gồm 6 số, ví dụ 038492.
    """
    return f"{random.randint(0, 999999):06d}"


def calculate_fee(checkin_time: datetime, checkout_time: datetime) -> int:
    """
    Tính tiền gửi xe khách vãng lai: 5k/giờ, làm tròn lên.
    """
    diff = checkout_time - checkin_time
    hours = diff.total_seconds() / 3600  # float

    # Ví dụ: 1.2 giờ -> 2 giờ
    hours_rounded = int(hours) if hours.is_integer() else int(hours) + 1
    return hours_rounded * 5000  # 5k/giờ


=======
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b
# ====== HÀM TẠO USERNAME / PASSWORD TỪ HỌ TÊN + SĐT ======
def make_username(full_name, phone):
    """
    username = tên không dấu (viết liền, thường) + 4 số cuối SĐT
    VD: 'Nguyễn Quốc Vũ', '0912345678' -> 'nguyenquocvu5678'
    """
    if not full_name:
        return "user"

    normalized = unicodedata.normalize("NFD", full_name)
    no_accent = "".join(
        c for c in normalized if unicodedata.category(c) != "Mn"
    )
    base = no_accent.lower().replace(" ", "")

    suffix = phone[-4:] if phone and len(phone) >= 4 else ""
    return base + suffix


def make_initial_password(phone):
    """
    password mặc định = 6 số cuối SĐT, nếu không đủ thì dùng 123456
    """
    if phone and len(phone) >= 6:
        return phone[-6:]
    return "123456"
# =========================================================


# ---------- TRANG GỐC ----------
@app.route("/")
def index():
    role = session.get("role")
    if role in ("admin", "staff"):
        return redirect(url_for("admin_home"))
    if role == "resident":
        return redirect(url_for("resident_dashboard"))
    return redirect(url_for("login"))


# ---------- ĐĂNG NHẬP ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        flash("Vui lòng nhập đầy đủ tài khoản và mật khẩu", "warning")
        return redirect(url_for("login"))

    # 1) Thử đăng nhập ADMIN trước
    user = query_one(
        "SELECT * FROM admin_users WHERE username = %s",
        (username,),
    )
    if user and check_password_hash(user["password_hash"], password):
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user.get("role", "admin")
        return redirect(url_for("admin_home"))

    # 2) Nếu không phải admin, thử đăng nhập CƯ DÂN
    resident = query_one(
        """
        SELECT id, username, password_hash, phone, status
        FROM residents
        WHERE username = %s
        """,
        (username,),
    )

    if resident and resident["status"] == "active":
        pwd_hash = resident["password_hash"]
        phone = resident["phone"]

        ok = False

        if pwd_hash:
            if check_password_hash(pwd_hash, password):
                ok = True
        else:
            expected_plain = make_initial_password(phone)
            if password == expected_plain:
                ok = True
                new_hash = generate_password_hash(expected_plain)
                execute(
                    "UPDATE residents SET password_hash = %s WHERE id = %s",
                    (new_hash, resident["id"]),
                )

        if ok:
            session.clear()
            session["user_id"] = resident["id"]
            session["resident_id"] = resident["id"]
            session["username"] = resident["username"]
            session["role"] = "resident"
            return redirect(url_for("resident_dashboard"))

    flash("Sai tài khoản hoặc mật khẩu", "danger")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- ADMIN HOME DASHBOARD ----------
@app.route("/admin/home")
def admin_home():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    total_residents = query_one(
        "SELECT COUNT(*) AS c FROM residents"
    )["c"]

    total_guests_today = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE DATE(checkin_time) = CURDATE()
        """
    )["c"]

    active_vehicles = query_one(
        """
        SELECT COUNT(*) AS c
        FROM resident_vehicles
        WHERE is_in_parking = 1
        """
    )["c"]

    stats = {
        "total_residents": total_residents,
        "total_guests_today": total_guests_today,
        "active_vehicles": active_vehicles,
    }

    revenue_rows = query_all(
        """
        SELECT
            DATE(checkout_time) AS day,
            SUM(fee) AS total
        FROM guest_sessions
        WHERE checkout_time IS NOT NULL
        GROUP BY DATE(checkout_time)
        ORDER BY day DESC
        LIMIT 7
        """
    )

    revenue_labels = [r["day"].strftime("%d/%m") for r in reversed(revenue_rows)]
    revenue_values = [float(r["total"] or 0) for r in reversed(revenue_rows)]

    traffic_rows = query_all(
        """
        SELECT
            DATE(event_time) AS day,
            SUM(CASE WHEN event_type = 'IN'  THEN 1 ELSE 0 END) AS in_count,
            SUM(CASE WHEN event_type = 'OUT' THEN 1 ELSE 0 END) AS out_count
        FROM parking_logs
        GROUP BY DATE(event_time)
        ORDER BY day DESC
        LIMIT 7
        """
    )

    traffic_labels = [r["day"].strftime("%d/%m") for r in reversed(traffic_rows)]
    traffic_in = [int(r["in_count"] or 0) for r in reversed(traffic_rows)]
    traffic_out = [int(r["out_count"] or 0) for r in reversed(traffic_rows)]

    notifications = [
        {
            "level": "warning",
            "title": "Bãi xe tầng hầm gần đầy",
            "time": "5 phút trước",
            "message": "Số lượng xe hiện tại đã đạt 90% sức chứa."
        },
        {
            "level": "info",
            "title": "Bản vá bảo mật đã được áp dụng",
            "time": "Hôm nay 09:30",
            "message": "Hệ thống đã cập nhật bản vá bảo mật mới cho cổng đăng nhập."
        },
        {
            "level": "success",
            "title": "Doanh thu hôm nay tăng",
            "time": "Hôm nay 08:00",
            "message": "Doanh thu gửi xe khách ngoài tăng 15% so với ngày hôm qua."
        },
    ]

    return render_template(
        "admin/home.html",
        stats=stats,
        revenue_labels=revenue_labels,
        revenue_values=revenue_values,
        traffic_labels=traffic_labels,
        traffic_in=traffic_in,
        traffic_out=traffic_out,
        notifications=notifications,
    )


# ---------- TRANG QUẢN LÝ CƯ DÂN ----------
@app.route("/admin/residents", methods=["GET"])
def admin_residents():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    sql = """
        SELECT
            r.id,
            r.full_name,
            r.floor,
            r.room,
            r.status,
            r.phone,
            COALESCE(rv.plate, '') AS plate_number,
            COALESCE(rbc.backup_code, '') AS backup_code
        FROM residents r
        LEFT JOIN resident_vehicles rv
            ON rv.resident_id = r.id
        LEFT JOIN resident_backup_codes rbc
            ON rbc.resident_id = r.id AND rbc.is_active = 1
        ORDER BY 
            CAST(r.floor AS UNSIGNED) ASC,
            CAST(r.room  AS UNSIGNED) ASC,
            r.full_name ASC
    """
    rows = query_all(sql)

    residents = []
    for r in rows:
        phone = r.get("phone")
        username = make_username(r["full_name"], phone)
        password = make_initial_password(phone)

        residents.append({
            "id": r["id"],
            "full_name": r["full_name"],
            "floor": r["floor"],
            "room": r["room"],
            "status": r["status"],
            "plate_number": r.get("plate_number") or "",
            "backup_code": r.get("backup_code") or "",
            "username": username,
            "password": password,
        })

    return render_template("admin/residents.html", residents=residents)


@app.route("/admin/residents/list", methods=["GET"])
def admin_residents_list():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    sql = """
        SELECT
            r.id,
            r.full_name,
            r.floor,
            r.room,
            r.status,
            r.phone,
            COALESCE(rv.plate, '') AS plate_number,
            COALESCE(rbc.backup_code, '') AS backup_code
        FROM residents r
        LEFT JOIN resident_vehicles rv
            ON rv.resident_id = r.id
        LEFT JOIN resident_backup_codes rbc
            ON rbc.resident_id = r.id AND rbc.is_active = 1
        ORDER BY 
            CAST(r.floor AS UNSIGNED) ASC,
            CAST(r.room  AS UNSIGNED) ASC,
            r.full_name ASC
    """
    rows = query_all(sql)

    residents = []
    for r in rows:
        phone = r.get("phone")
        username = make_username(r["full_name"], phone)
        password = make_initial_password(phone)

        residents.append({
            "id": r["id"],
            "full_name": r["full_name"],
            "floor": r["floor"],
            "room": r["room"],
            "status": r["status"],
            "plate_number": r.get("plate_number") or "",
            "backup_code": r.get("backup_code") or "",
            "username": username,
            "password": password,
        })

    return render_template("admin/residents_list.html", residents=residents)


@app.route("/admin/residents/create", methods=["POST"])
def admin_create_resident():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    full_name = request.form.get("full_name", "").strip()
    floor = request.form.get("floor") or None
    room = request.form.get("room") or None
    citizen_id = request.form.get("citizen_id") or None
    email = request.form.get("email") or None
    phone = request.form.get("phone") or None
    plate_number = request.form.get("plate_number") or None
    vehicle_type = request.form.get("vehicle_type") or "motorbike"

    if not full_name:
        flash("Họ tên là bắt buộc", "danger")
        return redirect(url_for("admin_residents"))

    sql_resident = """
        INSERT INTO residents (full_name, floor, room, cccd, email, phone)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    execute(sql_resident, (full_name, floor, room, citizen_id, email, phone))

    new_resident = query_one(
        "SELECT * FROM residents ORDER BY id DESC LIMIT 1"
    )
    resident_id = new_resident["id"]

    username = make_username(full_name, phone)
    raw_password = make_initial_password(phone)
    password_hash = generate_password_hash(raw_password)

    execute(
        """
        UPDATE residents
        SET username = %s,
            password_hash = %s
        WHERE id = %s
        """,
        (username, password_hash, resident_id),
    )

    if plate_number:
        sql_vehicle = """
            INSERT INTO resident_vehicles (resident_id, plate, vehicle_type)
            VALUES (%s, %s, %s)
        """
        execute(sql_vehicle, (resident_id, plate_number, vehicle_type))

<<<<<<< HEAD
    backup_code = f"{random.randint(0, 999999):06d}"""
=======
    backup_code = f"{random.randint(0, 999999):06d}"
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b
    sql_backup = """
        INSERT INTO resident_backup_codes (resident_id, backup_code, is_active)
        VALUES (%s, %s, 1)
    """
    execute(sql_backup, (resident_id, backup_code))

    flash("Thêm cư dân mới thành công", "success")
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/<int:resident_id>/reset-backup", methods=["POST"])
def admin_reset_backup_code(resident_id):
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    execute(
        "UPDATE resident_backup_codes SET is_active = 0 WHERE resident_id = %s",
        (resident_id,),
    )

    new_code = f"{random.randint(0, 999999):06d}"
    execute(
        """
        INSERT INTO resident_backup_codes (resident_id, backup_code, is_active)
        VALUES (%s, %s, 1)
        """,
        (resident_id, new_code),
    )

    flash("Đã reset mã dự phòng cho cư dân.", "info")
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/<int:resident_id>/disable", methods=["POST"])
def admin_disable_resident(resident_id):
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    execute(
        "UPDATE residents SET status = 'inactive' WHERE id = %s",
        (resident_id,),
    )
    flash("Đã vô hiệu hóa cư dân.", "warning")
    return redirect(url_for("admin_residents"))


# ---------- Khách ngoài ----------
@app.route("/admin/guests", methods=["GET"])
def admin_guests():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    date_str = request.args.get("date")
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    sql = """
        SELECT
            plate       AS plate_number,
            ticket_code,
            checkin_time,
            checkout_time,
            TIMESTAMPDIFF(
                MINUTE,
                checkin_time,
                COALESCE(checkout_time, NOW())
            ) / 60.0 AS hours,
            fee        AS amount,
            CASE
                WHEN status = 'open'  THEN 'IN'
                WHEN status = 'closed' THEN 'OUT'
                ELSE status
            END        AS status
        FROM guest_sessions
        WHERE DATE(checkin_time) = %s
        ORDER BY checkin_time DESC
    """
    guests = query_all(sql, (date_str,))

    return render_template(
        "admin/guests.html",
        guests=guests,
        selected_date=date_str,
    )


# ---------- Báo cáo ----------
@app.route("/admin/report")
def admin_report_page():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("admin/report.html", today=today)


# ---------- Chat admin - cư dân ----------
@app.route("/admin/chat")
@app.route("/admin/chat/<int:resident_id>")
def admin_chat(resident_id=None):
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    residents = query_all(
        "SELECT id, full_name, floor, room FROM residents ORDER BY floor, room, full_name"
    )

    active_resident = None
    messages = []

    if resident_id:
        active_resident = query_one(
            "SELECT id, full_name, floor, room FROM residents WHERE id = %s",
            (resident_id,),
        )

        if active_resident:
            messages = query_all(
                """
                SELECT sender, content, created_at
                FROM messages
                WHERE resident_id = %s
                ORDER BY created_at ASC
                LIMIT 50
                """,
                (resident_id,),
            )

    return render_template(
        "admin/chat.html",
        residents=residents,
        active_resident=active_resident,
        messages=messages,
    )


@app.route("/admin/chat/send", methods=["POST"])
def admin_chat_send():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    resident_id = request.form.get("resident_id")
    content = request.form.get("content", "").strip()

    if not resident_id:
        flash("Chưa chọn cư dân để gửi tin nhắn.", "warning")
        return redirect(url_for("admin_chat"))

    if not content:
        flash("Nội dung tin nhắn không được để trống.", "warning")
        return redirect(url_for("admin_chat", resident_id=resident_id))

    execute(
        """
        INSERT INTO messages (resident_id, sender, content)
        VALUES (%s, %s, %s)
        """,
        (resident_id, "admin", content),
    )

    flash("Đã gửi tin nhắn cho cư dân.", "success")
    return redirect(url_for("admin_chat", resident_id=resident_id))


# ---------- Resident dashboard ----------
@app.route("/resident/dashboard")
def resident_dashboard():
    resident_id = session.get("resident_id")

    if resident_id:
        resident_row = query_one(
            """
            SELECT
                r.id,
                r.full_name,
                r.floor,
                r.room,
                COALESCE(rv.plate, '') AS plate_number,
                COALESCE(rv.is_in_parking, 0) AS is_in_parking
            FROM residents r
            LEFT JOIN resident_vehicles rv
                ON rv.resident_id = r.id
            WHERE r.id = %s
            LIMIT 1
            """,
            (resident_id,),
        )
    else:
        resident_row = query_one(
            """
            SELECT
                r.id,
                r.full_name,
                r.floor,
                r.room,
                COALESCE(rv.plate, '') AS plate_number,
                COALESCE(rv.is_in_parking, 0) AS is_in_parking
            FROM residents r
            LEFT JOIN resident_vehicles rv
                ON rv.resident_id = r.id
            ORDER BY r.id ASC
            LIMIT 1
            """
        )

    if not resident_row:
        resident = {
            "id": 0,
            "full_name": "Chưa có cư dân",
            "floor": "-",
            "room": "-",
            "plate_number": "-",
            "is_in_parking": False,
        }
        logs = []
        messages = []
    else:
        resident = {
            "id": resident_row["id"],
            "full_name": resident_row["full_name"],
            "floor": resident_row["floor"],
            "room": resident_row["room"],
            "plate_number": resident_row["plate_number"],
            "is_in_parking": bool(resident_row["is_in_parking"]),
        }

        logs = query_all(
            """
            SELECT
                event_time AS timestamp,
                event_type,
                plate      AS plate_number
            FROM parking_logs
            WHERE resident_id = %s
            ORDER BY event_time DESC
            LIMIT 20
            """,
            (resident["id"],),
        )

        messages = query_all(
            """
            SELECT
                sender,
                content,
                DATE_FORMAT(created_at, '%H:%i %d/%m') AS time
            FROM messages
            WHERE resident_id = %s
            ORDER BY created_at ASC
            LIMIT 50
            """,
            (resident["id"],),
        )

    hour = datetime.now().hour
    if 5 <= hour < 10:
        greeting = "Xin chào buổi sáng"
    elif 10 <= hour < 13:
        greeting = "Xin chào buổi trưa"
    elif 13 <= hour < 18:
        greeting = "Xin chào buổi chiều"
    elif 18 <= hour < 22:
        greeting = "Xin chào buổi tối"
    else:
        greeting = "Xin chào"

    avatar_letter = resident["full_name"][0].upper() if resident["full_name"] else "U"

    return render_template(
        "resident/dashboard.html",
        resident=resident,
        logs=logs,
        messages=messages,
        greeting=greeting,
        avatar_letter=avatar_letter,
    )


@app.route("/resident/chat", methods=["POST"])
def resident_chat_send():
    resident_id = session.get("resident_id")
    if not resident_id:
        flash("Bạn cần đăng nhập bằng tài khoản cư dân để gửi tin nhắn.", "danger")
        return redirect(url_for("login"))

    content = request.form.get("content", "").strip()
    if not content:
        flash("Nội dung tin nhắn không được để trống", "warning")
        return redirect(url_for("resident_dashboard", chat=1))

    execute(
        """
        INSERT INTO messages (resident_id, sender, content)
        VALUES (%s, %s, %s)
        """,
        (resident_id, "resident", content),
    )

    flash("Tin nhắn đã được gửi tới Ban quản lý.", "success")
    return redirect(url_for("resident_dashboard", chat=1))


# ================== TRẠM CỔNG RA/VÀO (KIOSK) ==================

@app.route("/gate")
def gate_kiosk():
    return render_template("gate/gate.html")

<<<<<<< HEAD

=======
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b
@app.route("/gate/plate")
def gate_plate():
    return render_template("gate/gate_plate.html")

<<<<<<< HEAD

=======
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b
@app.route("/gate/face")
def gate_face():
    return render_template("gate/gate_face.html")

<<<<<<< HEAD

=======
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b
@app.route("/gate/scene")
def gate_scene():
    return render_template("gate/gate_scene.html")


<<<<<<< HEAD
@app.route("/gate/capture", methods=["POST"])
def gate_capture():
    """
    BƯỚC 1 – XỬ LÝ BIỂN SỐ (gate_plate):

    - Nhận plate_image (bắt buộc), face_image/scene_image (tuỳ).
    - AI đọc biển số.
    - Nếu tìm được cư dân:
        → KHÔNG cập nhật vào/ra ở đây.
        → Trả về user_type='resident', next_step='face'
          để frontend chuyển qua /gate/face xử lý khuôn mặt.
    - Nếu KHÔNG phải cư dân nhưng có plate:
        → Xử lý khách vãng lai (guest):
            + Lần vào: tạo guest_sessions + ticket_code 6 số.
            + Lần ra: bắt buộc nhập ticket_code đúng mới cho ra.
    """
    try:
        data = request.get_json(silent=True) or {}

        raw_mode = (data.get("mode") or "AUTO").upper()
        if raw_mode not in ("IN", "OUT", "AUTO"):
            mode = "IN"
        else:
            mode = raw_mode

        backup_code = (data.get("backup_code") or "").strip() or None
        plate_data = data.get("plate_image")
        face_data = data.get("face_image")
        scene_data = data.get("scene_image")

        # Mã vé khách nhập khi RA (nếu có)
        ticket_code_input = (data.get("ticket_code") or "").strip() or None

        # ----- 1. Decode ảnh biển số & AI -----
        plate_bytes = None
        plate_text = None

        if plate_data and isinstance(plate_data, str) and plate_data.startswith("data:image"):
            try:
                header, encoded = plate_data.split(",", 1)
                plate_bytes = base64.b64decode(encoded)
            except Exception as e:
                print("[ERROR] Decode base64 biển số lỗi:", e)
                plate_bytes = None

        if plate_bytes:
            try:
                plate_text = read_plate_from_image(plate_bytes)
                print("[INFO] Biển số AI nhận:", plate_text)
            except Exception as e:
                print("[ERROR] AI nhận diện biển số lỗi:", e)
                plate_text = None

        # ----- 2. Hàm lưu ảnh -----
        def save_data_url_or_bytes(data_url, folder_name, prefix, raw_bytes=None):
            if raw_bytes is None:
                if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:image"):
                    return None
                try:
                    header, encoded = data_url.split(",", 1)
                    img_bytes = base64.b64decode(encoded)
                except Exception:
                    return None
            else:
                img_bytes = raw_bytes

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{ts}.png"
            folder = GATE_UPLOAD_DIR / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            filepath = folder / filename
            with open(filepath, "wb") as f:
                f.write(img_bytes)
            return f"{folder_name}/{filename}"

        plate_filename = save_data_url_or_bytes(plate_data, "plates", "plate", plate_bytes)
        face_filename = save_data_url_or_bytes(face_data, "faces", "face")
        scene_filename = save_data_url_or_bytes(scene_data, "scenes", "scene")

        # ----- 3. Tìm cư dân qua backup_code (nếu có) -----
        resident_id = None
        if backup_code:
            row = query_one(
                """
                SELECT r.id
                FROM resident_backup_codes b
                JOIN residents r ON r.id = b.resident_id
                WHERE b.backup_code = %s
                  AND b.is_active = 1
                  AND r.status = 'active'
                """,
                (backup_code,),
            )
            if row:
                resident_id = row["id"]

        user_type = None
        event_type = None
        guest_session_id = None
        ticket_code_created = None
        fee = None
        now = datetime.now()

        need_ticket_code = False
        ticket_code_mismatch = False

        # 3a. Nếu chưa có resident_id mà có plate -> dò theo bảng resident_vehicles
        if resident_id is None and plate_text:
            veh = query_one(
                """
                SELECT rv.resident_id
                FROM resident_vehicles rv
                JOIN residents r ON r.id = rv.resident_id
                WHERE rv.plate = %s
                  AND r.status = 'active'
                LIMIT 1
                """,
                (plate_text,),
            )
            if veh:
                resident_id = veh["resident_id"]

        # ----- 4. Nếu là CƯ DÂN -> chỉ báo next_step='face' -----
        if resident_id is not None:
            user_type = "resident"

            # LƯU GATE_CAPTURE (mode đặt tạm IN cho hợp cột, sau này log face riêng)
            db_mode = "IN"
            try:
                execute(
                    """
                    INSERT INTO gate_captures (
                        mode, backup_code, resident_id,
                        plate_image, face_image, scene_image
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (db_mode, backup_code, resident_id,
                     plate_filename, face_filename, scene_filename),
                )
            except Exception as e:
                print("[WARN] Không ghi được gate_captures:", e)

            # Trả về next_step='face' để FE chuyển trang
            return {
                "ok": True,
                "message": "Biển số thuộc cư dân, chuyển sang nhận diện khuôn mặt.",
                "user_type": "resident",
                "next_step": "face",
                "matched_resident_id": resident_id,
                "plate_text": plate_text,
                "plate_image": plate_filename,
            }, 200

        # ----- 5. KHÔNG phải cư dân nhưng có biển số -> KHÁCH VÃNG LAI -----
        elif plate_text:
            user_type = "guest"

            session_row = query_one(
                """
                SELECT *
                FROM guest_sessions
                WHERE plate = %s
                  AND status = 'open'
                ORDER BY checkin_time DESC
                LIMIT 1
                """,
                (plate_text,),
            )

            if session_row:
                # Đang gửi xe -> giờ RA, bắt buộc nhập ticket_code đúng
                mode = "OUT"
                if not ticket_code_input:
                    need_ticket_code = True
                elif ticket_code_input != session_row["ticket_code"]:
                    need_ticket_code = True
                    ticket_code_mismatch = True
                else:
                    event_type = "guest_out"
                    guest_session_id = session_row["id"]
                    fee = calculate_fee(session_row["checkin_time"], now)

                    execute(
                        """
                        UPDATE guest_sessions
                        SET checkout_time = %s,
                            fee = %s,
                            exit_image_path = %s,
                            status = 'closed'
                        WHERE id = %s
                        """,
                        (now, fee, plate_filename, guest_session_id),
                    )
            else:
                # Lần VÀO đầu tiên
                mode = "IN"
                event_type = "guest_in"
                ticket_code_created = generate_ticket_code()

                execute(
                    """
                    INSERT INTO guest_sessions (
                        plate, ticket_code, checkin_time, entry_image_path, status
                    )
                    VALUES (%s, %s, %s, %s, 'open')
                    """,
                    (plate_text, ticket_code_created, now, plate_filename),
                )
                row = query_one(
                    """
                    SELECT id
                    FROM guest_sessions
                    WHERE plate = %s
                      AND ticket_code = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (plate_text, ticket_code_created),
                )
                if row:
                    guest_session_id = row["id"]

            # Ghi parking_logs cho khách nếu có event
            if event_type:
                execute(
                    """
                    INSERT INTO parking_logs (
                        event_time, event_type, user_type,
                        resident_id, guest_session_id, plate
                    )
                    VALUES (%s, %s, 'guest', NULL, %s, %s)
                    """,
                    (now, event_type, guest_session_id, plate_text),
                )

            # Lưu gate_captures
            db_mode = mode if mode in ("IN", "OUT") else "IN"
            try:
                execute(
                    """
                    INSERT INTO gate_captures (
                        mode, backup_code, resident_id,
                        plate_image, face_image, scene_image
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (db_mode, None, None,
                     plate_filename, face_filename, scene_filename),
                )
            except Exception as e:
                print("[WARN] Không ghi được gate_captures:", e)

            # Chuẩn bị message & ok
            ok = True
            msg = ""
            if need_ticket_code:
                ok = False
                if ticket_code_mismatch:
                    msg = "Mã vé 6 số không đúng, vui lòng nhập lại."
                else:
                    msg = "Vui lòng nhập mã vé 6 số của khách."

            return {
                "ok": ok,
                "message": msg,
                "user_type": "guest",
                "mode": mode,
                "plate_text": plate_text,
                "ticket_code_created": ticket_code_created,  # khi vào
                "ticket_code_input": ticket_code_input,      # khi ra
                "fee": fee,
                "need_ticket_code": need_ticket_code,
                "ticket_code_mismatch": ticket_code_mismatch,
                "plate_image": plate_filename,
            }, 200

        # ----- 6. Không đọc được biển số -----
        db_mode = "IN"
        try:
            execute(
                """
                INSERT INTO gate_captures (
                    mode, backup_code, resident_id,
                    plate_image, face_image, scene_image
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (db_mode, backup_code, None,
                 plate_filename, face_filename, scene_filename),
            )
        except Exception as e:
            print("[WARN] Không ghi được gate_captures:", e)

        return {
            "ok": False,
            "message": "AI chưa nhận diện được biển số rõ ràng, vui lòng giữ nguyên thêm vài giây hoặc chụp lại.",
            "user_type": None,
            "plate_text": None,
            "plate_image": plate_filename,
        }, 200

    except Exception as e:
        print("[ERROR] gate_capture bị lỗi:", e)
        return {"ok": False, "error": str(e)}, 500

@app.route("/gate/face/capture", methods=["POST"])
def gate_face_capture():
    """
    BƯỚC 2 – XỬ LÝ KHUÔN MẶT CƯ DÂN (gate_face):

    Input JSON:
      - resident_id: id cư dân (bắt buộc).
      - plate_text: biển số (để log).
      - face_image: dataURL ảnh khuôn mặt.
      - scene_image: (optional) toàn cảnh.
      - mode: 'AUTO' / 'IN' / 'OUT'
      - face_ok / face_verified: True nếu AI/nhân viên xác nhận mặt đúng.
      - backup_code: mã 6 số cư dân (nếu mặt không xác thực được).

    Logic:
      1. Suy ra mode:
           - Nếu mode=IN/OUT -> dùng luôn.
           - Nếu AUTO:
               + Nếu tìm thấy xe -> dựa vào is_in_parking.
               + Nếu không -> tra log gần nhất của cư dân:
                     * resident_in  -> lần này OUT
                     * resident_out / không có -> lần này IN
      2. Nếu KHÔNG face_ok:
           - Kiểm tra backup_code:
               + Thiếu/không đúng -> yêu cầu nhập lại, KHÔNG cho qua.
      3. Nếu xác thực OK:
           - Cập nhật resident_vehicles.is_in_parking.
           - Ghi parking_logs (resident_in/resident_out).
           - Lưu gate_captures.
    """
    try:
        data = request.get_json(silent=True) or {}

        resident_id = data.get("resident_id")
        plate_text = (data.get("plate_text") or "").strip() or None
        raw_mode = (data.get("mode") or "AUTO").upper()
        backup_code = (data.get("backup_code") or "").strip() or None
        face_data = data.get("face_image")
        scene_data = data.get("scene_image")

        if not resident_id:
            return {
                "ok": False,
                "message": "Thiếu resident_id, không thể xử lý nhận diện khuôn mặt.",
            }, 400

        try:
            resident_id = int(resident_id)
        except ValueError:
            return {
                "ok": False,
                "message": "resident_id không hợp lệ.",
            }, 400

        # --- cờ face_ok: FE báo đã nhận diện đúng khuôn mặt ---
        face_raw = data.get("face_ok") or data.get("face_verified")
        face_ok = False
        if face_raw is not None:
            face_ok = str(face_raw).strip().lower() in ("1", "true", "yes", "y")

        # ----- 1. Lưu ảnh khuôn mặt / toàn cảnh -----
        def save_face_or_scene(data_url, folder_name, prefix):
            if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:image"):
                return None
            try:
                header, encoded = data_url.split(",", 1)
                img_bytes = base64.b64decode(encoded)
            except Exception:
                return None

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{ts}.png"
            folder = GATE_UPLOAD_DIR / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            filepath = folder / filename
            with open(filepath, "wb") as f:
                f.write(img_bytes)
            return f"{folder_name}/{filename}"

        face_filename = save_face_or_scene(face_data, "faces", "face")
        scene_filename = save_face_or_scene(scene_data, "scenes", "scene")

        # ----- 2. Lấy xe cư dân (nếu có) + trạng thái hiện tại -----
        veh = None
        if plate_text:
            veh = query_one(
                """
                SELECT id, is_in_parking
                FROM resident_vehicles
                WHERE resident_id = %s
                  AND plate = %s
                LIMIT 1
                """,
                (resident_id, plate_text),
            )

        current_state = None  # None: không rõ, False: ngoài bãi, True: trong bãi

        if veh is not None:
            current_state = bool(veh["is_in_parking"])
        else:
            # Không tìm thấy dòng xe: fallback -> xem log gần nhất
            last_log = query_one(
                """
                SELECT event_type
                FROM parking_logs
                WHERE user_type = 'resident'
                  AND resident_id = %s
                ORDER BY event_time DESC
                LIMIT 1
                """,
                (resident_id,),
            )
            if last_log:
                if last_log["event_type"] == "resident_in":
                    current_state = True   # lần trước là vào -> giờ đang TRONG bãi
                elif last_log["event_type"] == "resident_out":
                    current_state = False  # lần trước là ra -> giờ đang NGOÀI bãi

        # ----- 3. Suy ra mode (IN/OUT) -----
        if raw_mode in ("IN", "OUT"):
            mode = raw_mode
        else:
            # AUTO
            if current_state is None:
                # chưa có thông tin gì -> mặc định cho là VÀO lần đầu
                mode = "IN"
            elif current_state is False:
                # đang ngoài bãi -> lần này là VÀO
                mode = "IN"
            else:
                # đang trong bãi -> lần này là RA
                mode = "OUT"

        # ----- 4. Kiểm tra xác thực: mặt hoặc mã 6 số -----
        need_backup_code = False
        backup_code_mismatch = False

        if not face_ok:
            if not backup_code:
                need_backup_code = True
            else:
                code_row = query_one(
                    """
                    SELECT b.id
                    FROM resident_backup_codes b
                    WHERE b.resident_id = %s
                      AND b.backup_code = %s
                      AND b.is_active = 1
                    LIMIT 1
                    """,
                    (resident_id, backup_code),
                )
                if not code_row:
                    need_backup_code = True
                    backup_code_mismatch = True

        if need_backup_code:
            msg = "Vui lòng nhập mã 6 số được cấp cho cư dân."
            if backup_code_mismatch:
                msg = "Mã 6 số không đúng hoặc đã hết hiệu lực. Vui lòng nhập lại."
            return {
                "ok": False,
                "message": msg,
                "need_backup_code": True,
                "backup_code_mismatch": backup_code_mismatch,
                "mode": mode,
                "resident_id": resident_id,
                "plate_text": plate_text,
            }, 200

        # ----- 5. Xác thực OK -> cập nhật trạng thái & log -----
        now = datetime.now()
        event_type = "resident_in" if mode == "IN" else "resident_out"

        # Cập nhật is_in_parking
        if veh:
            new_state = 1 if mode == "IN" else 0
            execute(
                "UPDATE resident_vehicles SET is_in_parking = %s WHERE id = %s",
                (new_state, veh["id"]),
            )
        else:
            # Không tìm thấy xe cụ thể: có thể UPDATE tất cả xe của cư dân (nếu muốn)
            execute(
                """
                UPDATE resident_vehicles
                SET is_in_parking = %s
                WHERE resident_id = %s
                """,
                (1 if mode == "IN" else 0, resident_id),
            )

        # Ghi log
        execute(
            """
            INSERT INTO parking_logs (
                event_time, event_type, user_type,
                resident_id, guest_session_id, plate
            )
            VALUES (%s, %s, 'resident', %s, NULL, %s)
            """,
            (now, event_type, resident_id, plate_text),
        )

        # Lưu gate_captures cho bước face
        db_mode = mode if mode in ("IN", "OUT") else "IN"
        try:
            execute(
                """
                INSERT INTO gate_captures (
                    mode, backup_code, resident_id,
                    plate_image, face_image, scene_image
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (db_mode, backup_code, resident_id,
                 None, face_filename, scene_filename),
            )
        except Exception as e:
            print("[WARN] Không ghi gate_captures (face):", e)

        return {
            "ok": True,
            "message": "Cư dân đã được xác thực, xe đã được {} bãi.".format(
                "VÀO" if mode == "IN" else "RA khỏi"
            ),
            "mode": mode,
            "resident_id": resident_id,
            "plate_text": plate_text,
        }, 200

    except Exception as e:
        print("[ERROR] gate_face_capture bị lỗi:", e)
        return {"ok": False, "error": str(e)}, 500
=======

@app.route("/gate/capture", methods=["POST"])
def gate_capture():
    data = request.get_json(silent=True) or {}

    mode = (data.get("mode") or "IN").upper()
    if mode not in ("IN", "OUT"):
        mode = "IN"

    backup_code = (data.get("backup_code") or "").strip() or None
    plate_data = data.get("plate_image")
    face_data = data.get("face_image")
    scene_data = data.get("scene_image")

    def save_data_url(data_url, folder_name, prefix):
        if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:image"):
            return None
        try:
            header, encoded = data_url.split(",", 1)
            img_bytes = base64.b64decode(encoded)
        except Exception:
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{ts}.png"
        folder = GATE_UPLOAD_DIR / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        filepath = folder / filename
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        return f"{folder_name}/{filename}"

    plate_filename = save_data_url(plate_data, "plates", "plate")
    face_filename = save_data_url(face_data, "faces", "face")
    scene_filename = save_data_url(scene_data, "scenes", "scene")

    resident_id = None
    if backup_code:
        row = query_one(
            """
            SELECT resident_id
            FROM resident_backup_codes
            WHERE backup_code = %s AND is_active = 1
            LIMIT 1
            """,
            (backup_code,),
        )
        if row:
            resident_id = row["resident_id"]

    execute(
        """
        INSERT INTO gate_captures
            (mode, backup_code, resident_id, plate_image, face_image, scene_image)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (mode, backup_code, resident_id, plate_filename, face_filename, scene_filename),
    )

    return {
        "ok": True,
        "mode": mode,
        "matched_resident_id": resident_id,
        "plate_image": plate_filename,
        "face_image": face_filename,
        "scene_image": scene_filename,
    }
>>>>>>> 6605a310cd2290368913826b011a34b3351b204b


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
