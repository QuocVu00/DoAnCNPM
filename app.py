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
from werkzeug.exceptions import BadRequest

from backend.config import Config
from backend.db import query_one, query_all, execute
from backend.routes_admin import admin_bp  # CHỈ GIỮ admin_bp
from frontend.ai.plate_recognition import read_plate_from_image  # AI nhận diện biển số


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY


# =========================================================
#  BIẾN DÙNG CHUNG CHO TEMPLATE (navbar: role, brand_url)
# =========================================================
@app.context_processor
def inject_layout_vars():
    """
    Biến dùng chung cho mọi template:
      - role: 'admin' / 'staff' / 'resident' / None
      - brand_url: link logo trên navbar
    """
    role = session.get("role")

    if role in ("admin", "staff"):
        brand_url = url_for("admin_home")
    elif role == "resident":
        brand_url = url_for("resident_dashboard")
    else:
        brand_url = url_for("login")

    return dict(
        role=role,
        brand_url=brand_url,
    )


# Thư mục lưu ảnh cho trạm cổng (gate kiosk)
GATE_UPLOAD_DIR = Path(app.static_folder) / "uploads" / "gate"
(GATE_UPLOAD_DIR / "plates").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "faces").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "scenes").mkdir(parents=True, exist_ok=True)

# đăng ký backend API
app.register_blueprint(admin_bp)


def require_role(*roles):
    return session.get("role") in roles


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

    hours_rounded = int(hours) if hours.is_integer() else int(hours) + 1

    return hours_rounded * 5000  # 5k/giờ


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
#                    ROUTES CHUNG
# =========================================================

# ---------- TRANG GỐC ----------
@app.route("/")
def index():
    role = session.get("role")
    if role == "admin" or role == "staff":
        return redirect(url_for("admin_home"))
    elif role == "resident":
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
        session["user_id"] = user["id"]
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
            session["resident_id"] = resident["id"]
            session["role"] = "resident"
            return redirect(url_for("resident_dashboard"))

    flash("Sai tài khoản / mật khẩu. Vui lòng thử lại.", "danger")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
#                    CƯ DÂN
# =========================================================

# ---------- DASHBOARD CƯ DÂN ----------
@app.route("/resident/dashboard")
def resident_dashboard():
    if not require_role("resident"):
        return redirect(url_for("login"))

    resident_id = session.get("resident_id")
    if not resident_id:
        return redirect(url_for("login"))

    resident = query_one(
        """
        SELECT id, full_name, floor, room, phone, status
        FROM residents
        WHERE id = %s
        """,
        (resident_id,),
    )

    if not resident:
        flash("Không tìm thấy thông tin cư dân.", "danger")
        return redirect(url_for("login"))

    vehicles = query_all(
        """
        SELECT id, plate, vehicle_type, is_in_parking
        FROM resident_vehicles
        WHERE resident_id = %s
        """,
        (resident_id,),
    )

    logs = query_all(
        """
        SELECT event_time, event_type, plate
        FROM parking_logs
        WHERE resident_id = %s
        ORDER BY event_time DESC
        LIMIT 20
        """,
        (resident_id,),
    )

    return render_template(
        "resident/dashboard.html",
        resident=resident,
        vehicles=vehicles,
        logs=logs,
    )


# --- ROUTE GIẢ CHAT CƯ DÂN (tránh BuildError) ---
@app.route("/resident/chat/send", methods=["POST"])
def resident_chat_send():
    if not require_role("resident"):
        return redirect(url_for("login"))

    message = request.form.get("message", "").strip()
    if message:
        flash("Tin nhắn của bạn đã được ghi nhận (demo).", "success")
    else:
        flash("Vui lòng nhập nội dung tin nhắn trước khi gửi.", "warning")

    return redirect(url_for("resident_dashboard"))


# =========================================================
#                    ADMIN DASHBOARD
# =========================================================

@app.route("/admin/home")
def admin_home():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    # Tổng số cư dân
    total_residents_row = query_one(
        "SELECT COUNT(*) AS c FROM residents WHERE status = 'active'"
    )
    total_residents = total_residents_row["c"] if total_residents_row else 0

    # Số khách vãng lai hôm nay
    today = datetime.now().date()
    total_guests_today_row = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE DATE(checkin_time) = %s
        """,
        (today,),
    )
    total_guests_today = total_guests_today_row["c"] if total_guests_today_row else 0

    # 🚗 Số xe đang ở trong bãi
    resident_in_row = query_one(
        "SELECT COUNT(*) AS c FROM resident_vehicles WHERE is_in_parking = 1"
    )
    resident_in = resident_in_row["c"] if resident_in_row else 0

    guest_in_row = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE status = 'open'
          AND DATE(checkin_time) = %s
        """,
        (today,),
    )
    guest_in = guest_in_row["c"] if guest_in_row else 0

    active_vehicles = int(resident_in) + int(guest_in)

    stats = {
        "total_residents": total_residents,
        "total_guests_today": total_guests_today,
        "active_vehicles": active_vehicles,
    }

    # Doanh thu 7 ngày gần nhất
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

    # Lượt xe ra/vào 7 ngày
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


# =========================================================
#                 QUẢN LÝ CƯ DÂN (ADMIN)
# =========================================================

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

    backup_code = f"{random.randint(0, 999999):06d}"
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
    flash("Đã vô hiệu cư dân.", "warning")
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/list")
def admin_residents_list():
    """
    Trang DANH SÁCH CƯ DÂN (chỉ bảng), dùng template admin/residents_list.html
    """
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


# ---------- CÁC TRANG ADMIN KHÁC ----------
@app.route("/admin/guests")
def admin_guests():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))
    return render_template("admin/guests.html")


@app.route("/admin/active-vehicles")
def admin_active_vehicles():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))
    return render_template("admin/active_vehicles.html")


@app.route("/admin/report")
def admin_report_page():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))
    return render_template("admin/report.html")


@app.route("/admin/chat")
def admin_chat():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))
    return render_template("admin/chat.html")


# =========================================================
#                 TRẠM CỔNG / GATE – VIEW
# =========================================================

@app.route("/gate")
def gate_index():
    """
    Trang chọn bước gate:
      - Bước 1: /gate/plate (nhận diện biển số)
      - Bước 2: /gate/face (xử lý khuôn mặt cư dân)
    """
    return render_template("gate/index.html")


@app.route("/gate/plate")
def gate_plate():
    """
    Màn hình bước 1: Camera biển số + chụp ảnh + gửi lên /gate/capture.
    """
    return render_template("gate/gate_plate.html")


@app.route("/gate/face")
def gate_face():
    """
    Màn hình bước 2: Camera khuôn mặt cư dân.
    Nhận:
      - resident_id
      - plate_text
      - mode (IN/OUT/AUTO)
    Gửi thêm:
      - ref_face_image: ảnh mặt đã lưu gần nhất (nếu có)
    """
    resident_id = request.args.get("resident_id")
    plate_text = request.args.get("plate_text", "")
    mode = request.args.get("mode", "AUTO").upper()

    ref_face_image = None
    if resident_id:
        row = query_one(
            """
            SELECT face_image
            FROM gate_captures
            WHERE resident_id = %s
              AND face_image IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (resident_id,),
        )
        if row:
            ref_face_image = row["face_image"]

    return render_template(
        "gate/gate_face.html",
        resident_id=resident_id,
        plate_text=plate_text,
        mode=mode,
        ref_face_image=ref_face_image,
    )


# =========================================================
#      API GATE: BƯỚC 1 – XỬ LÝ BIỂN SỐ (gate_plate)
# =========================================================
def save_data_url_or_bytes(data, folder_name: str, prefix: str) -> str | None:
    """
    Lưu ảnh từ dataURL hoặc bytes sang thư mục uploads/gate/<folder_name>/...
    Trả về relative_path để lưu DB, hoặc None nếu không có dữ liệu.
    """
    if not data:
        return None

    try:
        if isinstance(data, str) and data.startswith("data:image"):
            header, b64_data = data.split(",", 1)
            img_bytes = base64.b64decode(b64_data)
        elif isinstance(data, bytes):
            img_bytes = data
        else:
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{ts}.png"
        folder = GATE_UPLOAD_DIR / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        filepath = folder / filename
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        return f"{folder_name}/{filename}"

    except Exception as e:
        print("[ERROR] save_data_url_or_bytes:", e)
        return None


@app.route("/gate/capture", methods=["POST"])
def gate_capture():
    """
    BƯỚC 1 – XỬ LÝ BIỂN SỐ (gate_plate):

    - Cư dân:
        + Nếu xe đang ngoài bãi (is_in_parking = 0 hoặc NULL) => cho VÀO luôn.
        + Nếu xe đang trong bãi (is_in_parking = 1)           => lần này là RA => chuyển sang bước xác thực khuôn mặt.

    - Khách ngoài:
        + Nếu chưa có guest_session open => tạo vé mới (IN), trả ticket_code.
        + Nếu đã có guest_session open   => yêu cầu nhập mã, đúng thì OUT + tính tiền.
    """
    try:
        print("[DEBUG] content_type:", request.content_type)
        data = request.get_json(silent=True) or {}

        raw_mode = (data.get("mode") or "AUTO").upper()  # hiện chưa dùng, để mở rộng
        plate_data = data.get("plate_image")
        face_data = data.get("face_image")
        scene_data = data.get("scene_image")
        guest_ticket_code = (data.get("guest_ticket_code") or "").strip() or None
        plate_text_manual = (data.get("plate_text_manual") or "").strip() or None

        if not plate_data:
            return {
                "ok": False,
                "message": "Thiếu ảnh biển số (plate_image).",
            }, 400

        # 1. Lưu ảnh
        plate_filename = save_data_url_or_bytes(plate_data, "plates", "plate")
        face_filename = save_data_url_or_bytes(face_data, "faces", "face")
        scene_filename = save_data_url_or_bytes(scene_data, "scenes", "scene")

        if not plate_filename:
            return {
                "ok": False,
                "message": "Không lưu được ảnh biển số.",
            }, 500

        # 2. Đọc biển số
        plate_text = ""
        full_path = GATE_UPLOAD_DIR / plate_filename
        print("[DEBUG] Đường dẫn ảnh biển số:", full_path)

        if plate_text_manual:
            # Nếu người dùng nhập tay thì ưu tiên
            plate_text = plate_text_manual
        else:
            try:
                # Đọc file ảnh -> bytes rồi truyền cho OCR
                with open(full_path, "rb") as f:
                    img_bytes = f.read()

                plate_text = read_plate_from_image(img_bytes) or ""
                print("[DEBUG] Kết quả AI trả về:", repr(plate_text))
            except Exception as e:
                print("[WARN] Lỗi AI đọc biển số:", e)
                plate_text = ""

        plate_text = plate_text.strip().upper() if plate_text else ""

        if not plate_text:
            return {
                "ok": False,
                "message": "Không đọc được biển số, vui lòng thử lại hoặc nhập tay.",
            }, 200

        now = datetime.now()

        # 3. Thử tìm xe CƯ DÂN theo biển số
        veh_row = query_one(
            """
            SELECT rv.id, rv.resident_id, rv.is_in_parking, rv.plate
            FROM resident_vehicles rv
            WHERE UPPER(rv.plate) = %s
            LIMIT 1
            """,
            (plate_text,),
        )
        print("[DEBUG] resident lookup => plate_text =", plate_text, "veh_row =", veh_row)

        if veh_row is not None and veh_row.get("resident_id") is not None:
            resident_id = veh_row["resident_id"]
            db_plate = (veh_row.get("plate") or "").upper()

            if db_plate != plate_text:
                # Biển trong DB khác hẳn OCR => coi như khách ngoài cho an toàn
                print("[DEBUG] plate mismatch between DB and OCR, treat as GUEST. db_plate =", db_plate)
            else:
                # ====== XE CƯ DÂN ======
                is_in_parking = veh_row["is_in_parking"]
                current_state = None if is_in_parking is None else bool(is_in_parking)

                if current_state is False or current_state is None:
                    # Đang ngoài bãi / chưa biết -> lần này là VÀO
                    execute(
                        "UPDATE resident_vehicles SET is_in_parking = 1 WHERE id = %s",
                        (veh_row["id"],),
                    )

                    execute(
                        """
                        INSERT INTO parking_logs (
                            event_time, event_type, user_type,
                            resident_id, guest_session_id, plate
                        )
                        VALUES (%s, %s, 'resident', %s, NULL, %s)
                        """,
                        (now, "resident_in", resident_id, plate_text),
                    )

                    try:
                        execute(
                            """
                            INSERT INTO gate_captures (
                                mode, backup_code, resident_id,
                                plate_image, face_image, scene_image
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            ("IN", None, resident_id,
                             plate_filename, face_filename, scene_filename),
                        )
                    except Exception as e:
                        print("[WARN] Không ghi được gate_captures (resident IN):", e)

                    return {
                        "ok": True,
                        "user_type": "resident",
                        "event_type": "resident_in",
                        "mode": "IN",
                        "plate_text": plate_text,
                        "message": "Đã nhận diện cư dân, xe vào bãi thành công.",
                    }, 200

                else:
                    # Đang trong bãi -> lần này là RA -> chuyển sang bước xác thực khuôn mặt
                    try:
                        execute(
                            """
                            INSERT INTO gate_captures (
                                mode, backup_code, resident_id,
                                plate_image, face_image, scene_image
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            ("OUT", None, resident_id,
                             plate_filename, face_filename, scene_filename),
                        )
                    except Exception as e:
                        print("[WARN] Không ghi được gate_captures (resident OUT-plate):", e)

                    return {
                        "ok": True,
                        "user_type": "resident",
                        "next_step": "face",
                        "resident_id": resident_id,
                        "plate_text": plate_text,
                        "mode": "OUT",
                        "plate_image": plate_filename,
                        "message": "Xe cư dân đang trong bãi, chuyển sang bước xác thực khuôn mặt.",
                    }, 200

        # 4. KHÔNG PHẢI CƯ DÂN -> KHÁCH VÃNG LAI
        print("[DEBUG] GUEST FLOW - plate:", plate_text, "ticket input:", guest_ticket_code)

        # Ưu tiên tìm session đang mở theo MÃ VÉ, sau đó mới theo BIỂN SỐ
        session_row = None

        # 4.1. Nếu người dùng đã nhập mã vé thì tìm theo ticket_code trước
        if guest_ticket_code:
            session_row = query_one(
                """
                SELECT id, plate, checkin_time, ticket_code
                FROM guest_sessions
                WHERE ticket_code = %s
                  AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (guest_ticket_code,),
            )

        # 4.2. Nếu chưa thấy thì fallback sang tìm theo biển số
        if session_row is None:
            session_row = query_one(
                """
                SELECT id, plate, checkin_time, ticket_code
                FROM guest_sessions
                WHERE plate = %s
                  AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (plate_text,),
            )

        event_type = None
        ticket_code_created = None
        guest_session_id = None
        fee = 0  # mặc định 0, dùng cho lượt RA

        if session_row is None:
            # ---------- LẦN VÀO (KHÁCH VÃNG LAI VÀO BÃI) ----------
            mode = "IN"
            event_type = "guest_in"

            ticket_code_created = generate_ticket_code()
            execute(
                """
                INSERT INTO guest_sessions (
                    plate, ticket_code, checkin_time, status, plate_image
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (plate_text, ticket_code_created, now, "open", plate_filename),
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

        else:
            # ---------- LẦN RA (KHÁCH LẤY XE RA) ----------
            mode = "OUT"
            event_type = "guest_out"
            guest_session_id = session_row["id"]
            expected_ticket = (session_row["ticket_code"] or "").strip()

            # Chưa nhập mã vé -> yêu cầu nhập
            if not guest_ticket_code:
                return {
                    "ok": False,
                    "message": "Vui lòng nhập mã vé 6 số để lấy xe ra.",
                    "need_ticket_code": True,
                    "plate_text": plate_text,
                }, 200

            # Nhập sai mã vé
            if guest_ticket_code != expected_ticket:
                return {
                    "ok": False,
                    "message": "Mã vé không đúng. Vui lòng kiểm tra lại.",
                    "need_ticket_code": True,
                    "plate_text": plate_text,
                }, 200

            # Mã vé đúng -> tính tiền & đóng phiên
            checkin_time = session_row["checkin_time"]
            fee = calculate_fee(checkin_time, now)

            execute(
                """
                UPDATE guest_sessions
                SET checkout_time = %s,
                    fee = %s,
                    status = 'closed'
                WHERE id = %s
                """,
                (now, fee, guest_session_id),
            )

        # ---------- GHI LOG CHO KHÁCH ----------
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

        # ---------- LƯU gate_captures CHO KHÁCH ----------
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
            print("[WARN] Không ghi được gate_captures (guest):", e)

        # ---------- JSON TRẢ VỀ CHO FRONTEND ----------
        if event_type == "guest_in":
            return {
                "ok": True,
                "user_type": "guest",
                "event_type": event_type,
                "mode": mode,
                "plate_text": plate_text,
                "ticket_code": ticket_code_created,
                "message": "Mã vé của bạn là {}. Vui lòng giữ mã để xuất trình khi lấy xe ra.".format(
                    ticket_code_created
                ),
            }, 200

        if event_type == "guest_out":
            return {
                "ok": True,
                "user_type": "guest",
                "event_type": event_type,
                "mode": mode,
                "plate_text": plate_text,
                "message": "Thanh toán {}đ, xe đã được cho ra.".format(fee),
                "fee": fee,
            }, 200

        # Trường hợp fallback an toàn (hiếm gặp)
        return {
            "ok": True,
            "user_type": "guest",
            "mode": mode,
            "plate_text": plate_text,
            "message": "Đã ghi nhận biển số {}, vui lòng làm theo hướng dẫn ở cổng.".format(plate_text),
        }, 200

    except BadRequest as e:
        print("[WARN] BadRequest trong gate_capture:", e)
        return {
            "ok": False,
            "message": "Dữ liệu gửi từ trình duyệt không hợp lệ, vui lòng thử lại.",
        }, 200

    except Exception as e:
        print("[ERROR] gate_capture bị lỗi:", e)
        return {"ok": False, "error": str(e)}, 500


# =========================================================
#  API GATE: BƯỚC 2 – XỬ LÝ KHUÔN MẶT CƯ DÂN (gate_face)
# =========================================================
@app.route("/gate/face/capture", methods=["POST"])
def gate_face_capture():
    """
    BƯỚC 2 – XỬ LÝ KHUÔN MẶT CƯ DÂN (gate_face):
    - Nếu face_ok == True: cho qua.
    - Nếu face_ok == False: yêu cầu nhập backup_code 6 số của cư dân.
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
                "message": "Thiếu resident_id.",
            }, 400

        # 1) Lưu ảnh face/scene nếu có
        def save_face_or_scene(data_bytes, folder_name: str, prefix: str):
            if not data_bytes:
                return None
            try:
                if isinstance(data_bytes, str) and data_bytes.startswith("data:image"):
                    header, b64_data = data_bytes.split(",", 1)
                    img_bytes = base64.b64decode(b64_data)
                elif isinstance(data_bytes, bytes):
                    img_bytes = data_bytes
                else:
                    return None

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{prefix}_{ts}.png"
                folder = GATE_UPLOAD_DIR / folder_name
                folder.mkdir(parents=True, exist_ok=True)
                filepath = folder / filename
                with open(filepath, "wb") as f:
                    f.write(img_bytes)
                return f"{folder_name}/{filename}"

            except Exception as e:
                print("[ERROR] save_face_or_scene:", e)
                return None

        face_filename = save_face_or_scene(face_data, "faces", "face")
        scene_filename = save_face_or_scene(scene_data, "scenes", "scene")

        # 2. Lấy xe cư dân (nếu có) + trạng thái hiện tại
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

        if raw_mode in ("IN", "OUT"):
            mode = raw_mode
        else:
            if current_state is None:
                mode = "IN"
            elif current_state is False:
                mode = "IN"
            else:
                mode = "OUT"

        # 3. Kiểm tra face_ok / backup_code
        face_ok = bool(data.get("face_ok") or data.get("face_verified"))
        need_backup_code = False
        backup_code_mismatch = False

        if not face_ok:
            if not backup_code:
                need_backup_code = True
            else:
                # Kiểm tra đúng mã 6 số & còn hiệu lực
                code_row = query_one(
                    """
                    SELECT id, backup_code
                    FROM resident_backup_codes
                    WHERE resident_id = %s
                      AND backup_code = %s
                      AND is_active = 1
                    ORDER BY id DESC
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

        # 5. Xác thực OK -> cập nhật trạng thái & log
        now = datetime.now()
        event_type = "resident_in" if mode == "IN" else "resident_out"

        if veh:
            new_state = 1 if mode == "IN" else 0
            execute(
                "UPDATE resident_vehicles SET is_in_parking = %s WHERE id = %s",
                (new_state, veh["id"]),
            )
        else:
            execute(
                """
                UPDATE resident_vehicles
                SET is_in_parking = %s
                WHERE resident_id = %s
                """,
                (1 if mode == "IN" else 0, resident_id),
            )

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
            print("[WARN] Không ghi được gate_captures:", e)

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


# =========================================================
#                       MAIN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
