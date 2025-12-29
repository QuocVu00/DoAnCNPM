from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash, jsonify
)
from datetime import datetime, timedelta
import random
import unicodedata
import io
import base64
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from backend.config import Config
from backend.db import query_one, query_all, execute
from backend.routes_admin import admin_bp
from frontend.ai.plate_recognition import read_plate_from_image


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

# ==== Face Recognition ====
try:
    import face_recognition
except ImportError as e:
    face_recognition = None
    print("[WARN] face_recognition not available:", e)


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
#  DB: TABLE PHỤ CHO NOTIFICATION + ĐẾM NHẬP SAI MÃ VÉ + LOCK TRẠM + CHAT
# =========================================================
def ensure_support_tables():
    """
    - admin_notifications: lưu thông báo hệ thống cho admin
    - guest_ticket_attempts: đếm số lần nhập sai mã vé theo guest_session_id
    - gate_locks: trạng thái khóa trạm (1 dòng duy nhất)
    - resident_messages: lịch sử chat cư dân <-> admin
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
                attempt_count INT DEFAULT 0,
                last_attempt_at DATETIME NULL,
                locked_until DATETIME NULL,
                updated_at DATETIME NULL,
                UNIQUE KEY uniq_guest_session (guest_session_id)
            )
            """
        )
    except Exception as e:
        print("[WARN] ensure guest_ticket_attempts failed:", e)

    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS gate_locks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                is_locked TINYINT(1) DEFAULT 0,
                locked_reason VARCHAR(255),
                locked_at DATETIME,
                unlocked_at DATETIME
            )
            """
        )
        # đảm bảo chỉ có 1 dòng gate_locks
        row = query_one("SELECT id FROM gate_locks ORDER BY id ASC LIMIT 1")
        if not row:
            execute("INSERT INTO gate_locks(is_locked, locked_reason, locked_at) VALUES (0, NULL, NULL)")
    except Exception as e:
        print("[WARN] ensure gate_locks failed:", e)

    # Bảng lưu lịch sử chat cư dân
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS resident_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                resident_id INT NOT NULL,
                sender ENUM('resident','admin') NOT NULL DEFAULT 'resident',
                content TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_resident_created (resident_id, created_at)
            )
            """
        )
    except Exception as e:
        print("[WARN] ensure resident_messages failed:", e)


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


def _get_gate_lock_id():
    try:
        row = query_one("SELECT id FROM gate_locks ORDER BY id ASC LIMIT 1")
        return row["id"] if row else None
    except Exception as e:
        print("[WARN] _get_gate_lock_id:", e)
        return None


def get_gate_lock_row():
    try:
        return query_one(
            "SELECT id, is_locked, locked_reason, locked_at, unlocked_at FROM gate_locks ORDER BY id ASC LIMIT 1"
        )
    except Exception as e:
        print("[WARN] get_gate_lock_row:", e)
        return None


def gate_is_locked() -> bool:
    row = get_gate_lock_row()
    return bool(row and int(row.get("is_locked") or 0) == 1)


def gate_lock(reason: str):
    try:
        lock_id = _get_gate_lock_id()
        if not lock_id:
            return
        execute(
            """
            UPDATE gate_locks
            SET is_locked=1, locked_reason=%s, locked_at=%s, unlocked_at=NULL
            WHERE id=%s
            """,
            (reason, datetime.now(), lock_id),
        )
    except Exception as e:
        print("[WARN] gate_lock failed:", e)


def gate_unlock():
    try:
        lock_id = _get_gate_lock_id()
        if not lock_id:
            return
        execute(
            """
            UPDATE gate_locks
            SET is_locked=0, locked_reason=NULL, unlocked_at=%s
            WHERE id=%s
            """,
            (datetime.now(), lock_id),
        )
    except Exception as e:
        print("[WARN] gate_unlock failed:", e)


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

    if resident and resident.get("status") == "active":
        pwd_hash = resident.get("password_hash")
        phone = resident.get("phone")
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

    # Thông tin cơ bản cư dân
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

    # Xe của cư dân
    vehicles = query_all(
        """
        SELECT id, plate, is_in_parking
        FROM resident_vehicles
        WHERE resident_id = %s
        """,
        (resident_id,),
    )

    # Lịch sử ra/vào
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

    # ====== 1) Câu chào theo thời gian (buổi sáng/chiều/tối) ======
    now = datetime.now()
    h = now.hour
    if 5 <= h < 12:
        greeting_time = "buổi sáng"
    elif 12 <= h < 18:
        greeting_time = "buổi chiều"
    else:
        greeting_time = "buổi tối"

    # ====== 2) Biển số chính ======
    primary_plate = ""
    if vehicles:
        first_plate = vehicles[0].get("plate") or ""
        primary_plate = first_plate.strip().upper()

    # ====== 3) Mã dự phòng đang active ======
    backup_code = ""
    try:
        bc_row = query_one(
            """
            SELECT backup_code
            FROM resident_backup_codes
            WHERE resident_id=%s AND is_active=1
            ORDER BY id DESC
            LIMIT 1
            """,
            (resident_id,),
        )
        if bc_row and bc_row.get("backup_code"):
            backup_code = bc_row["backup_code"]
    except Exception as e:
        print("[WARN] resident_dashboard backup_code:", e)

    # ====== 4) Ảnh hồ sơ (face_image nếu có) ======
    avatar_url = None
    try:
        face_row = query_one(
            "SELECT face_image FROM residents WHERE id=%s LIMIT 1",
            (resident_id,),
        )
        face_path = face_row.get("face_image") if face_row else None
        if face_path:
            rel = str(face_path).replace("\\", "/").lstrip("/")
            avatar_url = url_for("static", filename=rel)
    except Exception as e:
        print("[WARN] resident_dashboard avatar face_image:", e)

    # ====== 5) Lịch sử chat cư dân <-> admin ======
    messages = []
    try:
        rows = query_all(
            """
            SELECT sender, content, created_at
            FROM resident_messages
            WHERE resident_id=%s
            ORDER BY id ASC
            LIMIT 100
            """,
            (resident_id,),
        ) or []
        for r in rows:
            created_at = r.get("created_at")
            if isinstance(created_at, datetime):
                time_str = created_at.strftime("%H:%M %d/%m")
            else:
                time_str = str(created_at) if created_at else ""
            messages.append(
                {
                    "sender": r.get("sender") or "resident",
                    "content": r.get("content") or "",
                    "time": time_str,
                }
            )
    except Exception as e:
        print("[WARN] resident_dashboard messages:", e)

    return render_template(
        "resident/dashboard.html",
        resident=resident,
        vehicles=vehicles,
        logs=logs,
        greeting_time=greeting_time,
        primary_plate=primary_plate,
        backup_code=backup_code,
        avatar_url=avatar_url,
        messages=messages,
    )


@app.route("/resident/chat/send", methods=["POST"])
def resident_chat_send():
    if not require_role("resident"):
        return redirect(url_for("login"))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("Vui lòng nhập nội dung tin nhắn trước khi gửi.", "warning")
        return redirect(url_for("resident_dashboard"))

    resident_id = session.get("resident_id")
    resident = query_one("SELECT full_name FROM residents WHERE id=%s", (resident_id,))
    sender_name = resident["full_name"] if resident else "Cư dân"

    # Lưu lịch sử chat
    try:
        execute(
            """
            INSERT INTO resident_messages(resident_id, sender, content, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (resident_id, "resident", message, datetime.now()),
        )
    except Exception as e:
        print("[WARN] resident_chat_send insert message failed:", e)

    # Thông báo cho admin trên dashboard
    add_admin_notification(
        "info",
        f"Tin nhắn cư dân: {sender_name}",
        message,
    )

    flash("Đã gửi tin nhắn tới ban quản trị.", "success")
    return redirect(url_for("resident_dashboard", chat=1))


# =========================================================
#                    ADMIN HOME + CHART
# =========================================================
@app.route("/admin/home")
def admin_home():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    today = datetime.now().date()

    total_residents_row = query_one("SELECT COUNT(*) AS c FROM residents WHERE status = 'active'")
    total_residents = int(total_residents_row["c"] if total_residents_row else 0)

    total_guests_today_row = query_one(
        """
        SELECT COUNT(*) AS c
        FROM guest_sessions
        WHERE DATE(checkin_time) = %s
        """,
        (today,),
    )
    total_guests_today = int(total_guests_today_row["c"] if total_guests_today_row else 0)

    resident_in_row = query_one("SELECT COUNT(*) AS c FROM resident_vehicles WHERE is_in_parking = 1")
    resident_in = int(resident_in_row["c"] if resident_in_row else 0)

    guest_in_row = query_one("SELECT COUNT(*) AS c FROM guest_sessions WHERE status = 'open'")
    guest_in = int(guest_in_row["c"] if guest_in_row else 0)

    stats = {
        "total_residents": total_residents,
        "total_guests_today": total_guests_today,
        "active_vehicles": resident_in + guest_in,
    }

    revenue_rows = query_all(
        """
        SELECT DATE(checkout_time) AS day, SUM(fee) AS total
        FROM guest_sessions
        WHERE status='closed' AND checkout_time IS NOT NULL
        GROUP BY DATE(checkout_time)
        ORDER BY day DESC
        LIMIT 7
        """
    ) or []
    revenue_labels = [r["day"].strftime("%d/%m") for r in reversed(revenue_rows)]
    revenue_values = [float(r["total"] or 0) for r in reversed(revenue_rows)]

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
    ) or []
    traffic_labels = [r["day"].strftime("%d/%m") for r in reversed(traffic_rows)]
    traffic_in = [int(r["in_count"] or 0) for r in reversed(traffic_rows)]
    traffic_out = [int(r["out_count"] or 0) for r in reversed(traffic_rows)]

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

    notif_rows = []
    try:
        notif_rows = query_all(
            """
            SELECT level, title, message, created_at
            FROM admin_notifications
            ORDER BY id DESC
            LIMIT 10
            """
        ) or []
    except Exception as e:
        print("[WARN] read admin_notifications failed:", e)

    notifications = [{
        "level": "success",
        "title": "Doanh thu hôm nay",
        "time": "Hôm nay",
        "message": f"Tổng doanh thu khách ngoài hôm nay: {rev_today_val:,}đ".replace(",", "."),
    }]

    for r in notif_rows:
        notifications.append(
            {
                "level": r.get("level") or "info",
                "title": r.get("title") or "Thông báo",
                "time": "",
                "message": r.get("message") or "",
            }
        )

    lock_row = get_gate_lock_row()
    gate_locked = bool(lock_row and int(lock_row.get("is_locked") or 0) == 1)
    gate_lock_info = {
        "locked_reason": (lock_row or {}).get("locked_reason"),
        "locked_at": (lock_row or {}).get("locked_at"),
    }

    return render_template(
        "admin/home.html",
        stats=stats,
        revenue_labels=revenue_labels,
        revenue_values=revenue_values,
        traffic_labels=traffic_labels,
        traffic_in=traffic_in,
        traffic_out=traffic_out,
        notifications=notifications,
        gate_locked=gate_locked,
        gate_lock_info=gate_lock_info,
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
    rows = query_all(sql) or []

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
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    # Xóa dữ liệu liên quan (không crash nếu thiếu bảng)
    for sql, params in [
        ("DELETE FROM parking_logs WHERE resident_id=%s", (resident_id,)),
        ("DELETE FROM resident_backup_codes WHERE resident_id=%s", (resident_id,)),
        ("DELETE FROM resident_vehicles WHERE resident_id=%s", (resident_id,)),
        ("DELETE FROM gate_captures WHERE resident_id=%s", (resident_id,)),
        ("DELETE FROM resident_messages WHERE resident_id=%s", (resident_id,)),
    ]:
        try:
            execute(sql, params)
        except Exception as e:
            print("[WARN] delete related table failed:", e)

    try:
        execute("DELETE FROM residents WHERE id=%s", (resident_id,))
    except Exception as e:
        print("[WARN] delete residents failed:", e)

    flash("Đã xóa cư dân khỏi danh sách.", "warning")
    return redirect(url_for("admin_residents"))


@app.route("/admin/residents/list")
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
    rows = query_all(sql) or []

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
#                 ADMIN: KHÁCH NGOÀI + BÁO CÁO
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
    ) or []

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
    ) or []

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


# =========================================================
#                 ADMIN: XE ĐANG Ở BÃI
# =========================================================
@app.route("/admin/active-vehicles")
def admin_active_vehicles():
    """Trang 'Xe đang ở bãi'."""
    if not require_role("admin"):
        return redirect(url_for("login"))

    def _fmt_dt(dt):
        try:
            return dt.strftime("%d/%m/%Y %H:%M:%S") if dt else ""
        except Exception:
            return ""

    vehicles = []

    # 1) Resident đang ở bãi
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
        ORDER BY CAST(r.floor AS UNSIGNED), CAST(r.room AS UNSIGNED)
        """
    ) or []

    for r in resident_rows:
        plate = (r.get("plate") or "").strip().upper()
        if not plate:
            continue

        last_in = query_one(
            """
            SELECT event_time
            FROM parking_logs
            WHERE event_type = 'resident_in'
              AND UPPER(plate) = %s
            ORDER BY event_time DESC
            LIMIT 1
            """,
            (plate,),
        )
        in_time = last_in["event_time"] if last_in else None

        vehicles.append(
            {
                "source": "resident",
                "plate_number": plate,
                "vehicle_type": None,
                "owner_name": r.get("owner_name") or "",
                "location": f"{r.get('floor')}/{r.get('room')}",
                "checkin_display": _fmt_dt(in_time) if in_time else "-",
                "ticket_code": "-",
            }
        )

    # 2) Guest đang ở bãi (open)
    guest_rows = query_all(
        """
        SELECT plate, ticket_code, checkin_time
        FROM guest_sessions
        WHERE status = 'open'
        ORDER BY checkin_time DESC
        """
    ) or []

    for g in guest_rows:
        vehicles.append(
            {
                "source": "guest",
                "plate_number": (g.get("plate") or "").strip().upper(),
                "vehicle_type": None,
                "owner_name": "",
                "location": "",
                "checkin_display": _fmt_dt(g.get("checkin_time")) or "-",
                "ticket_code": g.get("ticket_code") or "-",
            }
        )

    return render_template("admin/active_vehicles.html", vehicles=vehicles)


# =========================================================
#                 ADMIN CHAT
# =========================================================
@app.route("/admin/chat")
def admin_chat():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    # Danh sách cư dân bên cột trái
    residents = query_all(
        """
        SELECT id, full_name, floor, room
        FROM residents
        ORDER BY CAST(floor AS UNSIGNED), CAST(room AS UNSIGNED), full_name
        """
    ) or []

    selected_resident = None
    messages = []
    resident_id = request.args.get("resident_id", type=int)

    if resident_id:
        selected_resident = query_one(
            """
            SELECT id, full_name, floor, room
            FROM residents
            WHERE id=%s
            """,
            (resident_id,),
        )

        if selected_resident:
            try:
                rows = query_all(
                    """
                    SELECT sender, content, created_at
                    FROM resident_messages
                    WHERE resident_id=%s
                    ORDER BY id ASC
                    LIMIT 200
                    """,
                    (resident_id,),
                ) or []
                for r in rows:
                    created_at = r.get("created_at")
                    if isinstance(created_at, datetime):
                        time_str = created_at.strftime("%H:%M %d/%m")
                    else:
                        time_str = str(created_at) if created_at else ""
                    messages.append(
                        {
                            "sender": r.get("sender") or "resident",
                            "content": r.get("content") or "",
                            "time": time_str,
                        }
                    )
            except Exception as e:
                print("[WARN] admin_chat messages:", e)

    return render_template(
        "admin/chat.html",
        residents=residents,
        selected_resident=selected_resident,
        messages=messages,
    )


@app.route("/admin/chat/send", methods=["POST"])
def admin_chat_send():
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    resident_id = request.form.get("resident_id", type=int)
    content = (request.form.get("message") or "").strip()

    if not resident_id or not content:
        flash("Thiếu cư dân hoặc nội dung tin nhắn.", "warning")
        return redirect(url_for("admin_chat"))

    try:
        execute(
            """
            INSERT INTO resident_messages(resident_id, sender, content, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (resident_id, "admin", content, datetime.now()),
        )
    except Exception as e:
        print("[WARN] admin_chat_send failed:", e)

    return redirect(url_for("admin_chat", resident_id=resident_id))


# =========================================================
#                 TRẠM CỔNG / GATE – VIEW
# =========================================================
@app.route("/gate")
def gate_index():
    return render_template("gate/index.html")


@app.route("/gate/plate")
def gate_plate():
    return render_template("gate/gate_plate.html", gate_locked=gate_is_locked())


@app.route("/gate/face")
def gate_face():
    resident_id = request.args.get("resident_id")
    plate_text = request.args.get("plate_text", "")
    mode = request.args.get("mode", "AUTO").upper()

    ref_face_image = None
    if resident_id:
        try:
            row = query_one("SELECT face_image FROM residents WHERE id=%s LIMIT 1", (resident_id,))
            if row:
                ref_face_image = row.get("face_image")
        except Exception as e:
            print("[WARN] residents.face_image not available:", e)

    return render_template(
        "gate/gate_face.html",
        resident_id=resident_id,
        plate_text=plate_text,
        mode=mode,
        ref_face_image=ref_face_image,
    )


# =========================================================
#      HỖ TRỢ LƯU ẢNH TỪ DATA URL
# =========================================================
def save_data_url(data_url: str, folder_name: str, prefix: str) -> str | None:
    if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:image"):
        return None
    try:
        _, b64_data = data_url.split(",", 1)
        img_bytes = base64.b64decode(b64_data)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{prefix}_{ts}.png"
        folder = GATE_UPLOAD_DIR / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        filepath = folder / filename
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        return f"{folder_name}/{filename}"
    except Exception as e:
        print("[ERROR] save_data_url:", e)
        return None


def normalize_plate(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("_", "")
        .upper()
        .strip()
    )


# =========================================================
#      API GATE: BƯỚC 1 – XỬ LÝ BIỂN SỐ (gate_plate)
# =========================================================
@app.route("/gate/capture", methods=["POST"])
def gate_capture():
    try:
        data = request.get_json(silent=True) or {}

        plate_image = data.get("plate_image")          # có thể là dataURL/base64/path
        manual_plate = (data.get("plate_text_manual") or "").strip().upper()

        def _to_image_bytes(val):
            """
            Chuyển mọi kiểu input về bytes ảnh:
            - data:image/...;base64,xxxx
            - base64 trần
            - đường dẫn file (C:\\... hoặc uploads/gate/plates/..)
            - bytes
            """
            if not val:
                return None

            if isinstance(val, (bytes, bytearray)):
                return bytes(val)

            if isinstance(val, str):
                s = val.strip()

                if s.startswith("data:image"):
                    try:
                        _, b64 = s.split(",", 1)
                        return base64.b64decode(b64)
                    except Exception as e:
                        print("[WARN] decode dataURL failed:", e)
                        return None

                if len(s) > 100 and ("\\\\" not in s) and ("://" not in s) and ("/" not in s[:10]):
                    try:
                        return base64.b64decode(s)
                    except Exception:
                        pass

                try:
                    p = Path(s)
                    if p.exists():
                        return p.read_bytes()
                except Exception:
                    pass

                try:
                    p2 = (GATE_UPLOAD_DIR / s).resolve()
                    if p2.exists():
                        return p2.read_bytes()
                except Exception:
                    pass

            return None

        # ====== 1) LẤY BYTES ẢNH BIỂN SỐ ĐỂ OCR ======
        img_bytes = _to_image_bytes(plate_image)

        # ====== 2) OCR / MANUAL ======
        plate_text = manual_plate

        if not plate_text:
            if not img_bytes:
                print("[DEBUG] plate_image type:", type(plate_image), "len:",
                      (len(plate_image) if isinstance(plate_image, str) else "N/A"))
                return jsonify({"ok": False, "message": "Không nhận được ảnh biển số từ camera."}), 200

            try:
                plate_text = (read_plate_from_image(img_bytes) or "").strip().upper()
            except Exception as e:
                print("[WARN] OCR read_plate_from_image failed:", e)
                plate_text = ""

        plate_text = (
            plate_text.replace(" ", "")
            .replace("-", "")
            .replace(".", "")
            .replace("_", "")
            .upper()
        )

        print("[DEBUG] gate_capture plate_text =", plate_text)

        if not plate_text:
            return jsonify({"ok": False, "message": "Không đọc được biển số. Vui lòng thử lại."}), 200

        now = datetime.now()

        # =====================================================
        # A) RESIDENT
        # =====================================================
        veh_row = query_one(
            """
            SELECT rv.id, rv.resident_id, rv.is_in_parking
            FROM resident_vehicles rv
            WHERE UPPER(REPLACE(REPLACE(REPLACE(rv.plate,' ',''),'-',''),'.','')) = %s
            LIMIT 1
            """,
            (plate_text,)
        )

        if veh_row:
            resident_id = veh_row["resident_id"]
            is_in = int(veh_row.get("is_in_parking") or 0)

            if is_in == 0:
                execute("UPDATE resident_vehicles SET is_in_parking=1 WHERE id=%s", (veh_row["id"],))
                execute(
                    """
                    INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
                    VALUES (%s,'resident_in','resident',%s,NULL,%s)
                    """,
                    (now, resident_id, plate_text),
                )
                return jsonify({
                    "ok": True,
                    "action": "redirect",
                    "redirect_url": url_for("gate_message", kind="welcome")
                }), 200

            return jsonify({
                "ok": True,
                "action": "redirect",
                "redirect_url": url_for("gate_face", resident_id=resident_id, plate_text=plate_text, mode="OUT")
            }), 200

        # =====================================================
        # B) GUEST
        # =====================================================
        session_row = query_one(
            """
            SELECT id
            FROM guest_sessions
            WHERE UPPER(REPLACE(REPLACE(REPLACE(plate,' ',''),'-',''),'.','')) = %s
              AND status='open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (plate_text,),
        )

        if session_row:
            if gate_is_locked():
                return jsonify({"ok": False, "gate_locked": True, "message": "Trạm đang bị khóa. Liên hệ Admin."}), 200

            return jsonify({
                "ok": True,
                "action": "redirect",
                "redirect_url": url_for("gate_ticket", plate=plate_text, session_id=session_row["id"])
            }), 200

        ticket_code = f"{random.randint(0, 999999):06d}"
        execute(
            "INSERT INTO guest_sessions(plate, ticket_code, checkin_time, status) VALUES (%s,%s,%s,'open')",
            (plate_text, ticket_code, now),
        )

        created = query_one(
            "SELECT id FROM guest_sessions WHERE plate=%s AND ticket_code=%s ORDER BY id DESC LIMIT 1",
            (plate_text, ticket_code),
        )
        guest_session_id = created["id"] if created else None

        execute(
            """
            INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
            VALUES (%s,'guest_in','guest',NULL,%s,%s)
            """,
            (now, guest_session_id, plate_text),
        )

        return jsonify({
            "ok": True,
            "action": "redirect",
            "redirect_url": url_for("gate_ticket_info", plate=plate_text, code=ticket_code)
        }), 200

    except Exception as e:
        print("[ERROR] gate_capture:", e)
        return jsonify({"ok": False, "message": "Lỗi hệ thống."}), 500


# =========================================================
#  API GATE: BƯỚC 2 – XỬ LÝ KHUÔN MẶT CƯ DÂN (gate_face)
# =========================================================
@app.route("/gate/face/capture", methods=["POST"])
def gate_face_capture():
    try:
        data = request.get_json(silent=True) or {}

        resident_id = data.get("resident_id")
        plate_text = normalize_plate(data.get("plate_text") or "")
        raw_mode = (data.get("mode") or "OUT").upper()
        backup_code = (data.get("backup_code") or "").strip() or None
        face_data = data.get("face_image")  # dataURL

        if not resident_id:
            return jsonify({"ok": False, "message": "Thiếu resident_id."}), 400

        ref_face_path = None
        try:
            row = query_one("SELECT face_image FROM residents WHERE id=%s LIMIT 1", (resident_id,))
            ref_face_path = row["face_image"] if row else None
        except Exception as e:
            print("[WARN] residents.face_image not available:", e)

        def decode_data_url(d):
            if not d or not isinstance(d, str) or not d.startswith("data:image"):
                return None
            try:
                _, b64_data = d.split(",", 1)
                return base64.b64decode(b64_data)
            except Exception:
                return None

        face_bytes = decode_data_url(face_data)

        face_ok = False
        if face_recognition and ref_face_path and face_bytes:
            try:
                rel = (ref_face_path or "").replace("\\", "/").lstrip("/")
                ref_full = Path(app.static_folder) / rel
                if ref_full.exists():
                    ref_img = face_recognition.load_image_file(str(ref_full))
                    ref_encs = face_recognition.face_encodings(ref_img)

                    live_img = face_recognition.load_image_file(io.BytesIO(face_bytes))
                    live_encs = face_recognition.face_encodings(live_img)

                    if ref_encs and live_encs:
                        dist = float(face_recognition.face_distance([ref_encs[0]], live_encs[0])[0])
                        threshold = 0.60
                        face_ok = dist <= threshold
                        print(f"[DEBUG] face_distance={dist:.4f} threshold={threshold}")
            except Exception as e:
                print("[WARN] face verify error:", e)

        if face_ok:
            now = datetime.now()
            execute(
                "UPDATE resident_vehicles SET is_in_parking=0 WHERE resident_id=%s AND UPPER(plate)=%s",
                (resident_id, plate_text),
            )
            execute(
                """
                INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
                VALUES (%s, %s, 'resident', %s, NULL, %s)
                """,
                (now, "resident_out", resident_id, plate_text),
            )
            return jsonify({"ok": True, "redirect_url": url_for("gate_message", kind="goodbye")}), 200

        if not backup_code:
            return jsonify({
                "ok": False,
                "need_backup_code": True,
                "message": "Không xác thực được khuôn mặt. Vui lòng nhập mã 6 số của cư dân.",
            }), 200

        code_row = query_one(
            """
            SELECT id
            FROM resident_backup_codes
            WHERE resident_id=%s AND backup_code=%s AND is_active=1
            ORDER BY id DESC
            LIMIT 1
            """,
            (resident_id, backup_code),
        )
        if not code_row:
            return jsonify({
                "ok": False,
                "need_backup_code": True,
                "message": "Mã 6 số không đúng hoặc đã hết hiệu lực. Vui lòng nhập lại.",
            }), 200

        now = datetime.now()
        execute(
            "UPDATE resident_vehicles SET is_in_parking=0 WHERE resident_id=%s AND UPPER(plate)=%s",
            (resident_id, plate_text),
        )
        execute(
            """
            INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
            VALUES (%s, %s, 'resident', %s, NULL, %s)
            """,
            (now, "resident_out", resident_id, plate_text),
        )

        return jsonify({"ok": True, "redirect_url": url_for("gate_message", kind="goodbye")}), 200

    except Exception as e:
        print("[ERROR] gate_face_capture:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================================================
#  Admin mở khóa trạm
# =========================================================
@app.route("/admin/gate/status", methods=["GET"])
def admin_gate_status():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    row = get_gate_lock_row()
    if not row:
        return jsonify({"ok": True, "is_locked": False}), 200

    return jsonify({
        "ok": True,
        "is_locked": bool(int(row.get("is_locked") or 0) == 1),
        "locked_reason": row.get("locked_reason"),
        "locked_at": row.get("locked_at").strftime("%Y-%m-%d %H:%M:%S") if row.get("locked_at") else None
    }), 200


@app.route("/admin/gate/unlock", methods=["POST"])
def admin_gate_unlock():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    gate_unlock()

    try:
        execute("""
            UPDATE guest_ticket_attempts
            SET attempt_count = 0,
                locked_until = NULL,
                updated_at = %s
            WHERE locked_until IS NOT NULL
        """, (datetime.now(),))
    except Exception as e:
        print("[WARN] reset guest_ticket_attempts failed:", e)

    add_admin_notification("success", "Mở khóa trạm cổng", "Admin đã mở khóa trạm và reset khóa nhập mã vé.")
    return jsonify({"ok": True, "message": "Đã mở khóa trạm và reset khóa nhập mã vé."}), 200


# =========================================================
#  Trang thông báo / hiện mã vé / nhập mã vé
# =========================================================
@app.route("/gate/message")
def gate_message():
    kind = (request.args.get("kind") or "").strip().lower()
    if kind not in ("welcome", "goodbye"):
        return redirect(url_for("gate_plate"))
    return render_template("gate/gate_message.html", kind=kind)


@app.route("/gate/ticket-info")
def gate_ticket_info():
    plate = normalize_plate(request.args.get("plate") or "")
    code = (request.args.get("code") or "").strip()
    return render_template("gate/gate_ticket_info.html", plate=plate, code=code)


MAX_TICKET_FAILS = 3
LOCK_MINUTES = 10  # khóa 10 phút


@app.route("/gate/ticket", methods=["GET"])
def gate_ticket():
    plate = (request.args.get("plate") or "").strip().upper()
    session_id = (request.args.get("session_id") or "").strip()

    if not session_id:
        flash("Thiếu session_id.", "danger")
        return redirect(url_for("gate_plate"))

    gate_locked = gate_is_locked()

    attempt_count = 0
    locked_until = None
    try:
        att = query_one(
            "SELECT attempt_count, locked_until FROM guest_ticket_attempts WHERE guest_session_id=%s",
            (session_id,)
        )
        if att:
            attempt_count = int(att.get("attempt_count") or 0)
            locked_until = att.get("locked_until")
    except Exception as e:
        print("[WARN] read guest_ticket_attempts:", e)

    return render_template(
        "gate/gate_ticket.html",
        plate=plate,
        session_id=session_id,
        gate_locked=gate_locked,
        attempt_count=attempt_count,
        locked_until=locked_until,
        max_fails=MAX_TICKET_FAILS
    )


# =========================================================
#  API xác nhận mã vé khi ra (3 lần sai -> khóa + notify admin)
# =========================================================
@app.route("/gate/guest/verify", methods=["POST"])
def gate_guest_verify():
    data = request.get_json(silent=True) or {}

    plate = (data.get("plate") or "").strip().upper()
    session_id = str(data.get("session_id") or "").strip()
    ticket_code = str(data.get("ticket_code") or "").strip()

    if not session_id:
        return jsonify({"ok": False, "message": "Thiếu session_id."}), 400
    if not ticket_code:
        return jsonify({"ok": False, "message": "Vui lòng nhập mã vé."}), 400

    now = datetime.now()

    try:
        if gate_is_locked():
            return jsonify({
                "ok": False,
                "locked": True,
                "message": "Trạm đang bị khóa. Vui lòng liên hệ Admin để mở khóa."
            }), 200

        att = query_one(
            "SELECT attempt_count, locked_until FROM guest_ticket_attempts WHERE guest_session_id=%s",
            (session_id,)
        )

        if not att:
            execute(
                "INSERT INTO guest_ticket_attempts (guest_session_id, attempt_count, locked_until, updated_at) "
                "VALUES (%s,0,NULL,%s)",
                (session_id, now)
            )
            att = {"attempt_count": 0, "locked_until": None}

        attempt_count = int(att.get("attempt_count") or 0)

        gs = query_one(
            "SELECT id, plate, ticket_code, status, checkin_time FROM guest_sessions WHERE id=%s",
            (session_id,)
        )
        if not gs:
            return jsonify({"ok": False, "message": "Không tìm thấy phiên gửi xe khách."}), 404

        real_code = str(gs.get("ticket_code") or "").strip()
        real_plate = (gs.get("plate") or plate or "").strip().upper()

        if real_code and ticket_code == real_code:
            execute(
                "UPDATE guest_ticket_attempts SET attempt_count=0, locked_until=NULL, updated_at=%s "
                "WHERE guest_session_id=%s",
                (now, session_id)
            )

            checkin_time = gs.get("checkin_time")
            fee = 0
            if checkin_time:
                diff = now - checkin_time
                hours = diff.total_seconds() / 3600
                hours_rounded = int(hours) if hours.is_integer() else int(hours) + 1
                fee = hours_rounded * 5000

            execute(
                "UPDATE guest_sessions SET status='closed', checkout_time=%s, fee=%s WHERE id=%s",
                (now, fee, session_id)
            )

            execute(
                """
                INSERT INTO parking_logs(event_time, event_type, user_type, resident_id, guest_session_id, plate)
                VALUES (%s,'guest_out','guest',NULL,%s,%s)
                """,
                (now, session_id, real_plate),
            )

            return jsonify({
                "ok": True,
                "message": "Xác thực thành công. Cho xe ra!",
                "redirect_url": url_for("gate_message", kind="goodbye")
            }), 200

        attempt_count += 1

        if attempt_count >= MAX_TICKET_FAILS:
            gate_lock(
                f"Khóa trạm do nhập sai mã vé {MAX_TICKET_FAILS} lần. Plate: {real_plate} Session: {session_id}"
            )

            execute(
                "UPDATE guest_ticket_attempts SET attempt_count=%s, locked_until=%s, updated_at=%s "
                "WHERE guest_session_id=%s",
                (attempt_count, now + timedelta(minutes=LOCK_MINUTES), now, session_id)
            )

            add_admin_notification(
                "danger",
                "Khóa trạm do nhập sai mã vé",
                f"Khách nhập sai mã vé {MAX_TICKET_FAILS} lần. Plate: {real_plate} Session: {session_id}."
            )

            return jsonify({
                "ok": False,
                "locked": True,
                "message": f"Bạn đã nhập sai {MAX_TICKET_FAILS} lần. Trạm đã khóa và đã báo Admin."
            }), 200

        execute(
            "UPDATE guest_ticket_attempts SET attempt_count=%s, updated_at=%s WHERE guest_session_id=%s",
            (attempt_count, now, session_id)
        )

        remaining = MAX_TICKET_FAILS - attempt_count
        return jsonify({
            "ok": False,
            "message": f"Mã vé sai. Bạn còn {remaining} lần thử.",
            "remaining": remaining
        }), 200

    except Exception as e:
        print("[ERROR] gate_guest_verify:", e)
        return jsonify({"ok": False, "message": "Có lỗi xảy ra."}), 500


# =========================================================
#                       MAIN
# =========================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
