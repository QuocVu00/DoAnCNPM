from flask import Blueprint, request, jsonify
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

from .db import query_one, query_all, execute
from backend.db import query_one, query_all, execute


admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")



# =============== CƯ DÂN ===============

@admin_bp.route("/residents/create", methods=["POST"])
def create_resident():
    """
    Tạo cư dân mới.
    JSON body ví dụ:
    {
      "full_name": "Nguyen Van A",
      "floor": 3,
      "room": "301",
      "cccd": "0123456789",
      "email": "a@example.com",
      "phone": "0909123456"
    }
    """
    data = request.get_json() or {}

    full_name = data.get("full_name")
    floor = data.get("floor")
    room = data.get("room")
    cccd = data.get("cccd")
    email = data.get("email")
    phone = data.get("phone")

    if not full_name:
        return jsonify({"error": "full_name is required"}), 400

    sql = """
        INSERT INTO residents (full_name, floor, room, cccd, email, phone)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    execute(sql, (full_name, floor, room, cccd, email, phone))

    return jsonify({"message": "Resident created successfully"}), 201


@admin_bp.route("/residents", methods=["GET"])
def list_residents():
    """
    Trả danh sách tất cả cư dân.
    """
    residents = query_all("SELECT * FROM residents ORDER BY id DESC")
    return jsonify(residents)


@admin_bp.route("/residents/update", methods=["POST"])
def update_resident():
    """
    Cập nhật thông tin cư dân.
    JSON body ví dụ:
    {
      "id": 1,
      "full_name": "Nguyen Van B",
      "floor": 4,
      "room": "402",
      "cccd": "9876543210",
      "email": "b@example.com",
      "phone": "0912345678",
      "status": "active"
    }
    Có thể gửi thiếu vài trường, hàm sẽ giữ nguyên giá trị cũ.
    """
    data = request.get_json() or {}
    resident_id = data.get("id")

    if not resident_id:
        return jsonify({"error": "id is required"}), 400

    # Lấy dữ liệu hiện tại
    existing = query_one("SELECT * FROM residents WHERE id = %s", (resident_id,))
    if not existing:
        return jsonify({"error": "Resident not found"}), 404

    full_name = data.get("full_name", existing["full_name"])
    floor = data.get("floor", existing["floor"])
    room = data.get("room", existing["room"])
    cccd = data.get("cccd", existing["cccd"])
    email = data.get("email", existing["email"])
    phone = data.get("phone", existing["phone"])
    status = data.get("status", existing["status"])

    sql = """
        UPDATE residents
        SET full_name=%s, floor=%s, room=%s,
            cccd=%s, email=%s, phone=%s, status=%s
        WHERE id=%s
    """
    execute(sql, (full_name, floor, room, cccd, email, phone, status, resident_id))

    return jsonify({"message": "Resident updated successfully"})


@admin_bp.route("/residents/delete", methods=["POST"])
def delete_resident():
    """
    Vô hiệu hoá cư dân (không xoá cứng).
    JSON body:
    {
      "id": 1
    }
    Thực tế: set status = 'inactive'
    """
    data = request.get_json() or {}
    resident_id = data.get("id")

    if not resident_id:
        return jsonify({"error": "id is required"}), 400

    existing = query_one("SELECT id FROM residents WHERE id = %s", (resident_id,))
    if not existing:
        return jsonify({"error": "Resident not found"}), 404

    sql = "UPDATE residents SET status = 'inactive' WHERE id = %s"
    execute(sql, (resident_id,))

    return jsonify({"message": "Resident deactivated"})


@admin_bp.route("/residents/<int:resident_id>/backup-code", methods=["POST"])
def set_backup_code(resident_id):
    """
    Cấp mã backup cho cư dân.
    JSON body:
    {
      "backup_code": "ABCD1234"
    }
    """
    data = request.get_json() or {}
    backup_code = data.get("backup_code")

    if not backup_code:
        return jsonify({"error": "backup_code is required"}), 400

    # Chỉ chèn mã mới, tránh lỗi lock khi UPDATE
    sql = """
        INSERT INTO resident_backup_codes (resident_id, backup_code, is_active)
        VALUES (%s, %s, 1)
    """
    execute(sql, (resident_id, backup_code))

    return jsonify({"message": "Backup code created"})


# =============== BÁO CÁO ===============

@admin_bp.route("/report/daily", methods=["GET"])
def report_daily():
    """
    Báo cáo cuối ngày.
    GET /admin/report/daily?date=YYYY-MM-DD
    Nếu không truyền date -> dùng ngày hôm nay.
    """
    date_str = request.args.get("date")
    if not date_str:
        # lấy ngày hiện tại
        date_str = datetime.now().strftime("%Y-%m-%d")

    # Thống kê khách ngoài từ guest_sessions
    sql_guest = """
        SELECT COUNT(*) AS guest_count, COALESCE(SUM(fee), 0) AS total_fee
        FROM guest_sessions
        WHERE DATE(checkin_time) = %s
    """
    guest_stats = query_one(sql_guest, (date_str,))

    # Thống kê số sự kiện cư dân ra/vào (log)
    sql_resident = """
        SELECT COUNT(*) AS resident_events
        FROM parking_logs
        WHERE DATE(event_time) = %s AND user_type = 'resident'
    """
    resident_stats = query_one(sql_resident, (date_str,))

    return jsonify({
        "date": date_str,
        "guest_count": guest_stats["guest_count"],
        "guest_revenue": guest_stats["total_fee"],
        "resident_events": resident_stats["resident_events"],
    })
# =============== ADMIN AUTH ===============

@admin_bp.route("/auth/register", methods=["POST"])
def admin_register():
    """
    Tạo tài khoản admin mới (chỉ dùng khi setup ban đầu).
    JSON body:
    {
      "username": "admin",
      "password": "123456",
      "full_name": "Quan Tri Vien"
    }
    """
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    full_name = data.get("full_name")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    # Kiểm tra trùng username
    existing = query_one(
        "SELECT id FROM admin_users WHERE username = %s",
        (username,)
    )
    if existing:
        return jsonify({"error": "Username already exists"}), 400

    password_hash = generate_password_hash(password)

    sql = """
        INSERT INTO admin_users (username, password_hash, full_name)
        VALUES (%s, %s, %s)
    """
    execute(sql, (username, password_hash, full_name))

    return jsonify({"message": "Admin user created successfully"}), 201


@admin_bp.route("/auth/login", methods=["POST"])
def admin_login():
    """
    Đăng nhập admin.
    JSON body:
    {
      "username": "admin",
      "password": "123456"
    }
    """
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    user = query_one(
        "SELECT * FROM admin_users WHERE username = %s",
        (username,)
    )

    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401

    # Ở mức đơn giản, chỉ trả về thông tin cơ bản, chưa làm token
    return jsonify({
        "message": "Login successful",
        "admin_id": user["id"],
        "username": user["username"],
        "full_name": user["full_name"]
    })
