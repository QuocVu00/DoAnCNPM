from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash
)
from datetime import datetime
import random
import unicodedata

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

# Thư mục lưu ảnh cho trạm cổng (gate kiosk)
GATE_UPLOAD_DIR = Path(app.static_folder) / "uploads" / "gate"
(GATE_UPLOAD_DIR / "plates").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "faces").mkdir(parents=True, exist_ok=True)
(GATE_UPLOAD_DIR / "scenes").mkdir(parents=True, exist_ok=True)

# đăng ký backend API
app.register_blueprint(admin_bp)


# =========================================================
#  BIẾN DÙNG CHUNG CHO TEMPLATE (navbar: role, brand_url)
# =========================================================
@app.context_processor
def inject_layout_vars():
    role = session.get("role")

    if role in ("admin", "staff"):
        brand_url = url_for("admin_home")
    elif role == "resident":
        brand_url = url_for("resident_dashboard")
    else:
        brand_url = url_for("login")

    return dict(role=role, brand_url=brand_url)


def require_role(*roles):
    return session.get("role") in roles


# =========================================================
#  DB: TABLE PHỤ CHO NOTIFICATION + ĐẾM NHẬP SAI MÃ VÉ
# =========================================================
def ensure_support_tables():
    """
    Tạo bảng phụ nếu chưa có (không phá DB hiện tại).
    - admin_notifications: lưu thông báo cho admin (bao gồm: nhập sai mã vé 3 lần, v.v.)
    - guest_ticket_attempts: đếm số lần nhập sai mã vé theo guest_session_id
    """
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS admin_notifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                level VARCHAR(20) DEFAULT 'info',
                title VARCHAR(255) NOT NULL,
                message TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    except Exception as e:
        print("[WARN] ensure admin_notifications failed:", e)

    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS guest_ticket_attempts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guest_session_id INT NOT NULL,
                wrong_count INT NOT NULL DEFAULT 0,
                last_attempt_at DATETIME NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_guest_session (guest_session_id)
            )
            """
        )
    except Exception as e:
        print("[WARN] ensure guest_ticket_attempts failed:", e)


ensure_support_tables()


def add_admin_notification(level: str, title: str, message: str):
    try:
        execute(
            """
            INSERT INTO admin_notifications(level, title, message, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (level, title, message, datetime.now()),
        )
    except Exception as e:
        print("[WARN] add_admin_notification failed:", e)


def fmt_time_ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    diff = datetime.now() - dt
    sec = int(diff.total_seconds())
    if sec < 60:
        return f"{sec}s trước"
    if sec < 3600:
        return f"{sec // 60} phút trước"
    if sec < 86400:
        return f"{sec // 3600} giờ trước"
    return dt.strftime("%d/%m %H:%M")


# ====== HỖ TRỢ: MÃ VÉ & TÍNH TIỀN KHÁCH VÃNG LAI ======
def generate_ticket_code():
    return f"{random.randint(0, 999999):06d}"


def calculate_fee(checkin_time: datetime, checkout_time: datetime) -> int:
    diff = checkout_time - checkin_time
    hours = diff.total_seconds() / 3600
    hours_rounded = int(hours) if hours.is_integer() else int(hours) + 1
    return hours_rounded * 5000


# ====== HÀM TẠO USERNAME / PASSWORD TỪ HỌ TÊN + SĐT ======
def make_username(full_name, phone):
    if not full_name:
        return "user"

    normalized = unicodedata.normalize("NFD", full_name)
    no_accent = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    base = no_accent.lower().replace(" ", "")
    suffix = phone[-4:] if phone and len(phone) >= 4 else ""
    return base + suffix


def make_initial_password(phone):
    if phone and len(phone) >= 6:
        return phone[-6:]
    return "123456"


# =========================================================
#                    ROUTES CHUNG
# =========================================================
@app.route("/")
def index():
    role = session.get("role")
    if role in ("admin", "staff"):
        return redirect(url_for("admin_home"))
    if role == "resident":
        return redirect(url_for("resident_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        flash("Vui lòng nhập đầy đủ tài khoản và mật khẩu", "warning")
        return redirect(url_for("login"))

    # 1) ADMIN/STAFF
    user = query_one("SELECT * FROM admin_users WHERE username = %s", (username,))
    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["id"]
        session["role"] = user.get("role", "admin")
        return redirect(url_for("admin_home"))

    # 2) RESIDENT
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
            ok = check_password_hash(pwd_hash, password)
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
        SELECT id, plate, is_in_parking
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


@app.route("/resident/chat/send", methods=["POST"])
def resident_chat_send():
    """
    Nếu bạn muốn thật sự lưu tin nhắn cư dân để hiển thị notification cho admin,
    bạn có thể thêm bảng resident_messages. Hiện tại mình sẽ:
    - đẩy thành admin_notifications để admin thấy giống “tin nhắn”.
    """
    if not require_role("resident"):
        return redirect(url_for("login"))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("Vui lòng nhập nội dung tin nhắn trước khi gửi.", "warning")
        return redirect(url_for("resident_dashboard"))

    resident_id = session.get("resident_id")
    resident = query_one(
        "SELECT full_name FROM residents WHERE id=%s",
        (resident_id,),
    )
    sender_name = resident["full_name"] if resident else "Cư dân"

    add_admin_notification(
        "info",
        f"Tin nhắn cư dân: {sender_name}",
        message,
    )

    flash("Đã gửi tin nhắn tới ban quản trị.", "success")
    return redirect(url_for("resident_dashboard"))


# =========================================================
#                    ADMIN HOME + CHART
# =========================================================
@app.route("/admin/home")
def admin_home():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    today = datetime.now().date()

    # Tổng số cư dân active
    total_residents_row = query_one(
        "SELECT COUNT(*) AS c FROM residents WHERE status = 'active'"
    )
    total_residents = total_residents_row["c"] if total_residents_row else 0

    # Số khách vãng lai hôm nay (đếm checkin hôm nay)
    total_guests_today_row = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE DATE(checkin_time) = %s
        """,
        (today,),
    )
    total_guests_today = total_guests_today_row["c"] if total_guests_today_row else 0

    # Xe đang ở bãi (resident_vehicles.is_in_parking=1) + guest_sessions open (hôm nay hoặc vẫn open)
    resident_in_row = query_one(
        "SELECT COUNT(*) AS c FROM resident_vehicles WHERE is_in_parking = 1"
    )
    resident_in = resident_in_row["c"] if resident_in_row else 0

    guest_in_row = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE status = 'open'
        """
    )
    guest_in = guest_in_row["c"] if guest_in_row else 0

    stats = {
        "total_residents": int(total_residents),
        "total_guests_today": int(total_guests_today),
        "active_vehicles": int(resident_in) + int(guest_in),
    }

    # Doanh thu 7 ngày gần nhất (theo checkout_time)
    revenue_rows = query_all(
        """
        SELECT
            DATE(checkout_time) AS day,
            SUM(fee) AS total
        FROM guest_sessions
        WHERE status = 'closed'
          AND checkout_time IS NOT NULL
        GROUP BY DATE(checkout_time)
        ORDER BY day DESC
        LIMIT 7
        """
    )
    revenue_labels = [r["day"].strftime("%d/%m") for r in reversed(revenue_rows)]
    revenue_values = [float(r["total"] or 0) for r in reversed(revenue_rows)]

    # Lượt xe ra/vào 7 ngày (đúng event_type trong DB)
    traffic_rows = query_all(
        """
        SELECT
            DATE(event_time) AS day,
            SUM(CASE WHEN event_type IN ('resident_in','guest_in')  THEN 1 ELSE 0 END) AS in_count,
            SUM(CASE WHEN event_type IN ('resident_out','guest_out') THEN 1 ELSE 0 END) AS out_count
        FROM parking_logs
        GROUP BY DATE(event_time)
        ORDER BY day DESC
        LIMIT 7
        """
    )
    traffic_labels = [r["day"].strftime("%d/%m") for r in reversed(traffic_rows)]
    traffic_in = [int(r["in_count"] or 0) for r in reversed(traffic_rows)]
    traffic_out = [int(r["out_count"] or 0) for r in reversed(traffic_rows)]

    # Doanh thu hôm nay (đưa vào notifications)
    rev_today = query_one(
        """
        SELECT COALESCE(SUM(fee),0) AS total
        FROM guest_sessions
        WHERE status='closed'
          AND checkout_time IS NOT NULL
          AND DATE(checkout_time) = %s
        """,
        (today,),
    )
    rev_today_val = int(rev_today["total"] or 0) if rev_today else 0

    # Notifications: lấy từ DB (tin nhắn cư dân, nhập sai mã vé 3 lần, ...)
    notif_rows = []
    try:
        notif_rows = query_all(
            """
            SELECT level, title, message, created_at
            FROM admin_notifications
            ORDER BY id DESC
            LIMIT 10
            """
        )
    except Exception as e:
        print("[WARN] read admin_notifications failed:", e)

    notifications = []

    # 1) luôn có “doanh thu hôm nay”
    notifications.append(
        {
            "level": "success",
            "title": "Doanh thu hôm nay",
            "time": "Hôm nay",
            "message": f"Tổng doanh thu khách ngoài hôm nay: {rev_today_val:,}đ".replace(",", "."),
        }
    )

    # 2) đẩy các thông báo từ DB
    for r in notif_rows:
        notifications.append(
            {
                "level": r.get("level") or "info",
                "title": r.get("title") or "Thông báo",
                "time": fmt_time_ago(r.get("created_at")),
                "message": r.get("message") or "",
            }
        )

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
        residents.append(
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "floor": r["floor"],
                "room": r["room"],
                "status": r["status"],
                "plate_number": r.get("plate_number") or "",
                "backup_code": r.get("backup_code") or "",
                "username": make_username(r["full_name"], phone),
                "password": make_initial_password(phone),
            }
        )

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

    if not full_name:
        flash("Họ tên là bắt buộc", "danger")
        return redirect(url_for("admin_residents"))

    execute(
        """
        INSERT INTO residents (full_name, floor, room, cccd, email, phone)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (full_name, floor, room, citizen_id, email, phone),
    )

    new_resident = query_one("SELECT * FROM residents ORDER BY id DESC LIMIT 1")
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
        execute(
            """
            INSERT INTO resident_vehicles (resident_id, plate, is_in_parking)
            VALUES (%s, %s, 0)
            """,
            (resident_id, plate_number.strip().upper()),
        )

    backup_code = f"{random.randint(0, 999999):06d}"
    execute(
        """
        INSERT INTO resident_backup_codes (resident_id, backup_code, is_active)
        VALUES (%s, %s, 1)
        """,
        (resident_id, backup_code),
    )

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


@app.route("/admin/residents/<int:resident_id>/disable", methods=["POST"], endpoint="admin_disable_resident")
def admin_delete_resident_real(resident_id):
    """
    ✅ XÓA THẬT cư dân khỏi danh sách (DELETE thật):
    - xóa các bảng con trước để tránh lỗi FK
    - sau đó xóa residents
    Gắn endpoint name 'admin_disable_resident' để KHÔNG làm lỗi template cũ.
    """
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    # (1) xóa logs liên quan
    try:
        execute("DELETE FROM parking_logs WHERE resident_id = %s", (resident_id,))
    except Exception as e:
        print("[WARN] delete parking_logs failed:", e)

    # (2) xóa backup codes
    try:
        execute("DELETE FROM resident_backup_codes WHERE resident_id = %s", (resident_id,))
    except Exception as e:
        print("[WARN] delete resident_backup_codes failed:", e)

    # (3) xóa vehicles
    try:
        execute("DELETE FROM resident_vehicles WHERE resident_id = %s", (resident_id,))
    except Exception as e:
        print("[WARN] delete resident_vehicles failed:", e)

    # (4) xóa gate captures
    try:
        execute("DELETE FROM gate_captures WHERE resident_id = %s", (resident_id,))
    except Exception as e:
        print("[WARN] delete gate_captures failed:", e)

    # (5) xóa resident
    execute("DELETE FROM residents WHERE id = %s", (resident_id,))

    flash("Đã xóa cư dân khỏi danh sách (xóa thật).", "warning")
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/list")
def admin_residents_list():
    """
    Trang DS cư dân (chỉ bảng) - base.html đang gọi endpoint này.
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
        residents.append(
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "floor": r["floor"],
                "room": r["room"],
                "status": r["status"],
                "plate_number": r.get("plate_number") or "",
                "backup_code": r.get("backup_code") or "",
                "username": make_username(r["full_name"], phone),
                "password": make_initial_password(phone),
            }
        )

    return render_template("admin/residents_list.html", residents=residents)


# =========================================================
#                 ADMIN: KHÁCH NGOÀI + BÁO CÁO + XE ĐANG Ở BÃI
# =========================================================
@app.route("/admin/guests")
def admin_guests():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    today = datetime.now().date()
    date_str = (request.args.get("date") or "").strip()
    plate = (request.args.get("plate") or "").strip()
    ticket_code = (request.args.get("ticket_code") or "").strip()
    status_ui = (request.args.get("status") or "").strip().upper()  # IN/OUT

    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            filter_date = today
    else:
        filter_date = today

    where = []
    params = []

    if filter_date:
        where.append("DATE(gs.checkin_time) = %s")
        params.append(filter_date)

    if plate:
        where.append("UPPER(gs.plate) LIKE %s")
        params.append(f"%{plate.upper()}%")

    if ticket_code:
        where.append("gs.ticket_code LIKE %s")
        params.append(f"%{ticket_code}%")

    if status_ui == "IN":
        where.append("gs.status = 'open'")
    elif status_ui == "OUT":
        where.append("gs.status = 'closed'")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    guests = query_all(
        f"""
        SELECT
            gs.plate         AS plate_number,
            gs.ticket_code   AS ticket_code,
            gs.checkin_time  AS checkin_time,
            gs.checkout_time AS checkout_time,
            gs.fee           AS amount,
            CASE
              WHEN gs.status='open' THEN 'IN'
              WHEN gs.status='closed' THEN 'OUT'
              ELSE gs.status
            END AS status
        FROM guest_sessions gs
        {where_sql}
        ORDER BY gs.checkin_time DESC
        LIMIT 500
        """,
        tuple(params) if params else None,
    )

    return render_template("admin/guests.html", guests=guests)


@app.route("/admin/report")
def admin_report_page():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    today = datetime.now().date()
    date_str = (request.args.get("date") or "").strip()

    if date_str:
        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            report_date = today
    else:
        report_date = today

    guests = query_all(
        """
        SELECT
            gs.plate         AS plate_number,
            gs.ticket_code   AS ticket_code,
            gs.checkin_time  AS checkin_time,
            gs.checkout_time AS checkout_time,
            gs.fee           AS amount,
            CASE
              WHEN gs.status='open' THEN 'IN'
              WHEN gs.status='closed' THEN 'OUT'
              ELSE gs.status
            END AS status
        FROM guest_sessions gs
        WHERE DATE(gs.checkin_time) = %s
        ORDER BY gs.checkin_time DESC
        """,
        (report_date,),
    )

    revenue_row = query_one(
        """
        SELECT COALESCE(SUM(fee), 0) AS total
        FROM guest_sessions
        WHERE status='closed'
          AND checkout_time IS NOT NULL
          AND DATE(checkout_time) = %s
        """,
        (report_date,),
    )
    total_revenue = int(revenue_row["total"] or 0) if revenue_row else 0

    return render_template(
        "admin/report.html",
        today=report_date.strftime("%Y-%m-%d"),
        guests=guests,
        total_revenue=total_revenue,
    )


@app.route("/admin/active-vehicles")
def admin_active_vehicles():
    """
    ✅ FIX: Trang 'Xe đang ở bãi' phải có dữ liệu giống số 'Xe đang ở bãi' trên dashboard.
    ✅ Thêm time vào cho cư dân (lấy thời gian IN gần nhất từ parking_logs).
    ✅ Bỏ phần loại xe: không trả vehicle_type (template nếu có thì để trống/ẩn).
    """
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    # Resident đang ở bãi
    resident_rows = query_all(
        """
        SELECT
            rv.plate AS plate,
            r.full_name AS owner_name,
            r.floor AS floor,
            r.room AS room
        FROM resident_vehicles rv
        JOIN residents r ON r.id = rv.resident_id
        WHERE rv.is_in_parking = 1
        ORDER BY r.floor, r.room
        """
    )

    resident_active = []
    for r in resident_rows:
        plate = (r.get("plate") or "").upper()
        last_in = query_one(
            """
            SELECT event_time
            FROM parking_logs
            WHERE event_type = 'resident_in'
              AND plate = %s
            ORDER BY event_time DESC
            LIMIT 1
            """,
            (plate,),
        )
        resident_active.append(
            {
                "kind": "resident",
                "plate": plate,
                "owner_name": r.get("owner_name") or "",
                "location": f"{r.get('floor')}/{r.get('room')}",
                "in_time": last_in["event_time"] if last_in else None,
                "ticket_code": None,
            }
        )

    # Guest đang ở bãi (open)
    guest_rows = query_all(
        """
        SELECT
            plate, ticket_code, checkin_time
        FROM guest_sessions
        WHERE status = 'open'
        ORDER BY checkin_time DESC
        """
    )

    guest_active = [
        {
            "kind": "guest",
            "plate": (g.get("plate") or "").upper(),
            "owner_name": "",
            "location": "",
            "in_time": g.get("checkin_time"),
            "ticket_code": g.get("ticket_code"),
        }
        for g in guest_rows
    ]

    active_list = resident_active + guest_active

    return render_template("admin/active_vehicles.html", active_list=active_list)


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
    return render_template("gate/index.html")


@app.route("/gate/plate")
def gate_plate():
    return render_template("gate/gate_plate.html")


@app.route("/gate/face")
def gate_face():
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
    if not data:
        return None
    try:
        if isinstance(data, str) and data.startswith("data:image"):
            _, b64_data = data.split(",", 1)
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
    - Cư dân:
        + nếu is_in_parking = 0/NULL -> IN (ghi log resident_in)
        + nếu is_in_parking = 1 -> OUT -> chuyển face step
    - Khách:
        + nếu chưa có session open -> IN (ticket_code)
        + nếu đã open -> OUT yêu cầu mã, sai 3 lần -> notify admin
    """
    try:
        data = request.get_json(silent=True) or {}

        plate_data = data.get("plate_image")
        face_data = data.get("face_image")
        scene_data = data.get("scene_image")

        guest_ticket_code = (data.get("guest_ticket_code") or "").strip() or None
        plate_text_manual = (data.get("plate_text_manual") or "").strip() or None

        if not plate_data:
            return {"ok": False, "message": "Thiếu ảnh biển số (plate_image)."}, 400

        # 1) Lưu ảnh
        plate_filename = save_data_url_or_bytes(plate_data, "plates", "plate")
        face_filename = save_data_url_or_bytes(face_data, "faces", "face")
        scene_filename = save_data_url_or_bytes(scene_data, "scenes", "scene")

        if not plate_filename:
            return {"ok": False, "message": "Không lưu được ảnh biển số."}, 500

        # 2) OCR
        full_path = GATE_UPLOAD_DIR / plate_filename
        if plate_text_manual:
            plate_text = plate_text_manual
        else:
            plate_text = ""
            try:
                with open(full_path, "rb") as f:
                    img_bytes = f.read()
                plate_text = read_plate_from_image(img_bytes) or ""
            except Exception as e:
                print("[WARN] OCR error:", e)
                plate_text = ""

        plate_text = plate_text.strip().upper() if plate_text else ""
        if not plate_text:
            return {"ok": False, "message": "Không đọc được biển số, vui lòng thử lại hoặc nhập tay."}, 200

        now = datetime.now()

        # 3) check resident vehicle
        veh_row = query_one(
            """
            SELECT rv.id, rv.resident_id, rv.is_in_parking, rv.plate
            FROM resident_vehicles rv
            WHERE UPPER(rv.plate) = %s
            LIMIT 1
            """,
            (plate_text,),
        )

        if veh_row and veh_row.get("resident_id"):
            resident_id = veh_row["resident_id"]
            current_state = None if veh_row["is_in_parking"] is None else bool(veh_row["is_in_parking"])

            if current_state is False or current_state is None:
                # IN
                execute("UPDATE resident_vehicles SET is_in_parking = 1 WHERE id = %s", (veh_row["id"],))
                execute(
                    """
                    INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
                    VALUES (%s, %s, 'resident', %s, NULL, %s)
                    """,
                    (now, "resident_in", resident_id, plate_text),
                )
                try:
                    execute(
                        """
                        INSERT INTO gate_captures(mode, backup_code, resident_id, plate_image, face_image, scene_image)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        ("IN", None, resident_id, plate_filename, face_filename, scene_filename),
                    )
                except Exception as e:
                    print("[WARN] gate_captures resident IN:", e)

                return {
                    "ok": True,
                    "user_type": "resident",
                    "event_type": "resident_in",
                    "mode": "IN",
                    "plate_text": plate_text,
                    "message": "Đã nhận diện cư dân, xe vào bãi thành công.",
                }, 200

            # OUT -> face step
            try:
                execute(
                    """
                    INSERT INTO gate_captures(mode, backup_code, resident_id, plate_image, face_image, scene_image)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    ("OUT", None, resident_id, plate_filename, face_filename, scene_filename),
                )
            except Exception as e:
                print("[WARN] gate_captures resident OUT-plate:", e)

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

        # 4) guest flow
        session_row = None
        if guest_ticket_code:
            session_row = query_one(
                """
                SELECT id, plate, checkin_time, ticket_code
                FROM guest_sessions
                WHERE ticket_code = %s AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (guest_ticket_code,),
            )

        if session_row is None:
            session_row = query_one(
                """
                SELECT id, plate, checkin_time, ticket_code
                FROM guest_sessions
                WHERE plate = %s AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (plate_text,),
            )

        if session_row is None:
            # guest IN
            ticket_code_created = generate_ticket_code()
            execute(
                """
                INSERT INTO guest_sessions(plate, ticket_code, checkin_time, status, plate_image)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (plate_text, ticket_code_created, now, "open", plate_filename),
            )

            row = query_one(
                """
                SELECT id
                FROM guest_sessions
                WHERE plate = %s AND ticket_code = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (plate_text, ticket_code_created),
            )
            guest_session_id = row["id"] if row else None

            execute(
                """
                INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
                VALUES (%s, %s, 'guest', NULL, %s, %s)
                """,
                (now, "guest_in", guest_session_id, plate_text),
            )

            try:
                execute(
                    """
                    INSERT INTO gate_captures(mode, backup_code, resident_id, plate_image, face_image, scene_image)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    ("IN", None, None, plate_filename, face_filename, scene_filename),
                )
            except Exception as e:
                print("[WARN] gate_captures guest IN:", e)

            return {
                "ok": True,
                "user_type": "guest",
                "event_type": "guest_in",
                "mode": "IN",
                "plate_text": plate_text,
                "ticket_code": ticket_code_created,
                "message": f"Mã vé của bạn là {ticket_code_created}. Vui lòng giữ mã để xuất trình khi lấy xe ra.",
            }, 200

        # guest OUT
        guest_session_id = session_row["id"]
        expected_ticket = (session_row["ticket_code"] or "").strip()

        if not guest_ticket_code:
            return {
                "ok": False,
                "message": "Vui lòng nhập mã vé 6 số để lấy xe ra.",
                "need_ticket_code": True,
                "plate_text": plate_text,
            }, 200

        if guest_ticket_code != expected_ticket:
            # ✅ đếm sai + notify admin nếu >=3
            try:
                row = query_one(
                    "SELECT wrong_count FROM guest_ticket_attempts WHERE guest_session_id=%s",
                    (guest_session_id,),
                )
                if row:
                    wrong_count = int(row["wrong_count"] or 0) + 1
                    execute(
                        """
                        UPDATE guest_ticket_attempts
                        SET wrong_count=%s, last_attempt_at=%s
                        WHERE guest_session_id=%s
                        """,
                        (wrong_count, now, guest_session_id),
                    )
                else:
                    wrong_count = 1
                    execute(
                        """
                        INSERT INTO guest_ticket_attempts(guest_session_id, wrong_count, last_attempt_at)
                        VALUES (%s, %s, %s)
                        """,
                        (guest_session_id, wrong_count, now),
                    )

                if wrong_count >= 3:
                    add_admin_notification(
                        "danger",
                        "Nhập mã vé sai quá 3 lần",
                        f"Biển số: {plate_text} | Session ID: {guest_session_id} | Lần sai: {wrong_count} | Thời gian: {now.strftime('%d/%m/%Y %H:%M:%S')}",
                    )
            except Exception as e:
                print("[WARN] track wrong attempts failed:", e)

            return {
                "ok": False,
                "message": "Mã vé không đúng. Vui lòng kiểm tra lại.",
                "need_ticket_code": True,
                "plate_text": plate_text,
            }, 200

        # đúng mã -> clear attempts + checkout + fee
        try:
            execute("DELETE FROM guest_ticket_attempts WHERE guest_session_id=%s", (guest_session_id,))
        except Exception:
            pass

        fee = calculate_fee(session_row["checkin_time"], now)
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

        execute(
            """
            INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
            VALUES (%s, %s, 'guest', NULL, %s, %s)
            """,
            (now, "guest_out", guest_session_id, plate_text),
        )

        try:
            execute(
                """
                INSERT INTO gate_captures(mode, backup_code, resident_id, plate_image, face_image, scene_image)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("OUT", None, None, plate_filename, face_filename, scene_filename),
            )
        except Exception as e:
            print("[WARN] gate_captures guest OUT:", e)

        return {
            "ok": True,
            "user_type": "guest",
            "event_type": "guest_out",
            "mode": "OUT",
            "plate_text": plate_text,
            "message": f"Thanh toán {fee}đ, xe đã được cho ra.",
            "fee": fee,
        }, 200

    except BadRequest as e:
        print("[WARN] BadRequest:", e)
        return {"ok": False, "message": "Dữ liệu gửi từ trình duyệt không hợp lệ, vui lòng thử lại."}, 200
    except Exception as e:
        print("[ERROR] gate_capture:", e)
        return {"ok": False, "error": str(e)}, 500


# =========================================================
#  API GATE: BƯỚC 2 – XỬ LÝ KHUÔN MẶT CƯ DÂN (gate_face)
# =========================================================
@app.route("/gate/face/capture", methods=["POST"])
def gate_face_capture():
    try:
        data = request.get_json(silent=True) or {}

        resident_id = data.get("resident_id")
        plate_text = (data.get("plate_text") or "").strip().upper() or None
        raw_mode = (data.get("mode") or "AUTO").upper()
        backup_code = (data.get("backup_code") or "").strip() or None
        face_data = data.get("face_image")
        scene_data = data.get("scene_image")

        if not resident_id:
            return {"ok": False, "message": "Thiếu resident_id."}, 400

        def save_face_or_scene(data_bytes, folder_name: str, prefix: str):
            if not data_bytes:
                return None
            try:
                if isinstance(data_bytes, str) and data_bytes.startswith("data:image"):
                    _, b64_data = data_bytes.split(",", 1)
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

        veh = None
        if plate_text:
            veh = query_one(
                """
                SELECT id, is_in_parking
                FROM resident_vehicles
                WHERE resident_id = %s AND plate = %s
                LIMIT 1
                """,
                (resident_id, plate_text),
            )

        current_state = None
        if veh is not None:
            current_state = bool(veh["is_in_parking"])

        if raw_mode in ("IN", "OUT"):
            mode = raw_mode
        else:
            mode = "IN" if (current_state is None or current_state is False) else "OUT"

        face_ok = bool(data.get("face_ok") or data.get("face_verified"))
        need_backup_code = False
        backup_code_mismatch = False

        if not face_ok:
            if not backup_code:
                need_backup_code = True
            else:
                code_row = query_one(
                    """
                    SELECT id
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

        now = datetime.now()
        event_type = "resident_in" if mode == "IN" else "resident_out"

        if veh:
            execute(
                "UPDATE resident_vehicles SET is_in_parking = %s WHERE id = %s",
                (1 if mode == "IN" else 0, veh["id"]),
            )
        else:
            execute(
                "UPDATE resident_vehicles SET is_in_parking = %s WHERE resident_id = %s",
                (1 if mode == "IN" else 0, resident_id),
            )

        execute(
            """
            INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
            VALUES (%s, %s, 'resident', %s, NULL, %s)
            """,
            (now, event_type, resident_id, plate_text),
        )

        try:
            execute(
                """
                INSERT INTO gate_captures(mode, backup_code, resident_id, plate_image, face_image, scene_image)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (mode, backup_code, resident_id, None, face_filename, scene_filename),
            )
        except Exception as e:
            print("[WARN] gate_captures face step:", e)

        return {
            "ok": True,
            "message": f"Cư dân đã được xác thực, xe đã được {'VÀO' if mode=='IN' else 'RA khỏi'} bãi.",
            "mode": mode,
            "resident_id": resident_id,
            "plate_text": plate_text,
        }, 200

    except Exception as e:
        print("[ERROR] gate_face_capture:", e)
        return {"ok": False, "error": str(e)}, 500


# =========================================================
#                       MAIN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
