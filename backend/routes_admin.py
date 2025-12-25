from flask import Blueprint, request, jsonify
from datetime import datetime

from db import query_one, query_all, execute

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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


@admin_bp.route("/residents/<int:resident_id>/backup-code", methods=["POST"])
def set_backup_code(resident_id):
    """
    Cấp / reset mã backup cho cư dân.
    JSON body:
    {
      "backup_code": "ABCD1234"
    }
    """
    data = request.get_json() or {}
    backup_code = data.get("backup_code")

    if not backup_code:
        return jsonify({"error": "backup_code is required"}), 400

    # Vô hiệu hoá mã cũ
    execute(
        "UPDATE resident_backup_codes SET is_active = 0 WHERE resident_id = %s",
        (resident_id,),
    )

    # Tạo mã mới
    execute(
        "INSERT INTO resident_backup_codes (resident_id, backup_code, is_active) VALUES (%s, %s, 1)",
        (resident_id, backup_code),
    )

    return jsonify({"message": "Backup code updated"})


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
