from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash
)
from datetime import datetime
import random
import unicodedata

from werkzeug.security import check_password_hash, generate_password_hash

from backend.config import Config
from backend.db import query_one, query_all, execute
from backend.routes_admin import admin_bp  # CHỈ GIỮ admin_bp


app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY


# đăng ký backend API
app.register_blueprint(admin_bp)


def require_role(*roles):
    return session.get("role") in roles


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
            # đã có hash -> kiểm tra như bình thường
            if check_password_hash(pwd_hash, password):
                ok = True
        else:
            # cư dân cũ chưa có password_hash: dùng mật khẩu mặc định từ SĐT
            expected_plain = make_initial_password(phone)
            if password == expected_plain:
                ok = True
                # lưu hash luôn để lần sau check cho chuẩn
                new_hash = generate_password_hash(expected_plain)
                execute(
                    "UPDATE residents SET password_hash = %s WHERE id = %s",
                    (new_hash, resident["id"]),
                )

        if ok:
            session.clear()
            session["user_id"] = resident["id"]
            session["resident_id"] = resident["id"]  # <-- lưu riêng id cư dân
            session["username"] = resident["username"]
            session["role"] = "resident"
            return redirect(url_for("resident_dashboard"))

    # 3) Nếu cả hai đều fail
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

    # --- Thống kê cơ bản ---
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

    # --- Dữ liệu biểu đồ doanh thu 7 ngày gần nhất ---
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

    # --- Dữ liệu biểu đồ xe ra/vào 7 ngày gần nhất ---
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

    # --- Thông báo hệ thống (demo) ---
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


# ---------- TRANG QUẢN LÝ CƯ DÂN (CÓ FORM + BẢNG) ----------
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


# ---------- TRANG “CƯ DÂN” – CHỈ HIỂN THỊ DANH SÁCH ----------
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

    # 1) thêm cư dân cơ bản
    sql_resident = """
        INSERT INTO residents (full_name, floor, room, cccd, email, phone)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    execute(sql_resident, (full_name, floor, room, citizen_id, email, phone))

    new_resident = query_one(
        "SELECT * FROM residents ORDER BY id DESC LIMIT 1"
    )
    resident_id = new_resident["id"]

    # 2) tạo username + password_hash và lưu vào residents
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

    # 3) thêm xe nếu có
    if plate_number:
        sql_vehicle = """
            INSERT INTO resident_vehicles (resident_id, plate, vehicle_type)
            VALUES (%s, %s, %s)
        """
        execute(sql_vehicle, (resident_id, plate_number, vehicle_type))

    # 4) mã dự phòng
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


# ---------- Chat admin - cư dân (demo) ----------
@app.route("/admin/chat")
@app.route("/admin/chat/<int:resident_id>")
def admin_chat(resident_id=None):
    if not require_role("admin", "staff"):
        return redirect(url_for("login"))

    residents = query_all(
        "SELECT id, full_name, floor, room FROM residents ORDER BY floor, room, full_name"
    )

    active_resident = None
    if resident_id:
        active_resident = query_one(
            "SELECT id, full_name, floor, room FROM residents WHERE id = %s",
            (resident_id,),
        )

    messages = []  # demo

    return render_template(
        "admin/chat.html",
        residents=residents,
        active_resident=active_resident,
        messages=messages,
    )


# ---------- Resident dashboard ----------
@app.route("/resident/dashboard")
def resident_dashboard():
    """
    Ưu tiên lấy cư dân theo resident_id trong session (đúng người đang đăng nhập).
    Nếu không có (admin mở để xem thử) thì fallback về cư dân đầu tiên.
    """
    resident_id = session.get("resident_id")

    if resident_id:
        # lấy đúng cư dân đang đăng nhập
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
        # demo: lấy cư dân đầu tiên
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

    messages = []
    return render_template(
        "resident/dashboard.html",
        resident=resident,
        logs=logs,
        messages=messages,
    )


@app.route("/resident/chat", methods=["POST"])
def resident_chat_send():
    content = request.form.get("content", "").strip()
    if not content:
        flash("Nội dung tin nhắn không được để trống", "warning")
    else:
        flash("Tin nhắn đã được ghi nhận (demo).", "success")
    return redirect(url_for("resident_dashboard"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
