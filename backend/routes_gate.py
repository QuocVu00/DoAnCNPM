from flask import Blueprint, request, jsonify
from datetime import datetime
import random

from config import Config
from db import query_one, execute

# Tạo blueprint cho nhóm API ở cổng bãi xe
gate_bp = Blueprint("gate", __name__, url_prefix="/gate")


def generate_ticket_code():
    """
    Tạo mã vé gồm 6 số, ví dụ: 038492
    """
    return f"{random.randint(0, 999999):06d}"


def calculate_fee(checkin_time: datetime, checkout_time: datetime) -> int:
    """
    Tính tiền gửi xe (5k/giờ, làm tròn lên giờ tiếp theo)
    """
    diff = checkout_time - checkin_time
    hours = diff.total_seconds() / 3600  # số giờ (float)

    # làm tròn lên (1.2 giờ -> 2 giờ)
    hours_rounded = int(hours) if hours.is_integer() else int(hours) + 1
    return hours_rounded * 5000  # 5k/giờ


@gate_bp.route("/guest/checkin", methods=["POST"])
def guest_checkin():
    """
    API cho khách vãng lai khi GỬI XE (IN)
    Body JSON cần có:
    {
      "plate": "59A12345",
      "entry_image_path": "optional/path.jpg"
    }
    """
    data = request.get_json() or {}
    plate = data.get("plate")
    entry_image_path = data.get("entry_image_path")

    if not plate:
        return jsonify({"error": "plate is required"}), 400

    ticket_code = generate_ticket_code()
    now = datetime.now()

    sql = """
        INSERT INTO guest_sessions (plate, ticket_code, checkin_time, entry_image_path)
        VALUES (%s, %s, %s, %s)
    """
    execute(sql, (plate, ticket_code, now, entry_image_path))

    return jsonify({
        "message": "Guest checkin success",
        "plate": plate,
        "ticket_code": ticket_code,
        "checkin_time": now.isoformat()
    }), 201


@gate_bp.route("/guest/checkout", methods=["POST"])
def guest_checkout():
    """
    API cho khách vãng lai khi LẤY XE (OUT)
    Body JSON cần có:
    {
      "ticket_code": "123456",
      "exit_image_path": "optional/path.jpg"
    }
    """
    data = request.get_json() or {}
    ticket_code = data.get("ticket_code")
    exit_image_path = data.get("exit_image_path")

    if not ticket_code:
        return jsonify({"error": "ticket_code is required"}), 400

    # tìm phiên gửi xe còn đang open theo ticket_code
    session = query_one(
        "SELECT * FROM guest_sessions WHERE ticket_code = %s AND status = 'open'",
        (ticket_code,)
    )

    if not session:
        return jsonify({"error": "Ticket not found or already closed"}), 404

    checkin_time = session["checkin_time"]
    now = datetime.now()
    fee = calculate_fee(checkin_time, now)

    update_sql = """
        UPDATE guest_sessions
        SET checkout_time = %s,
            fee = %s,
            exit_image_path = %s,
            status = 'closed'
        WHERE id = %s
    """
    execute(update_sql, (now, fee, exit_image_path, session["id"]))

    return jsonify({
        "message": "Guest checkout success",
        "ticket_code": ticket_code,
        "plate": session["plate"],
        "checkin_time": checkin_time.isoformat(),
        "checkout_time": now.isoformat(),
        "fee": fee
    })

@gate_bp.route("/resident/backup-login", methods=["POST"])
def resident_backup_login():
    """
    Cư dân nhập mã backup khi không nhận diện được khuôn mặt.
    JSON body:
    {
      "backup_code": "ABCD1234"
    }
    """
    data = request.get_json() or {}
    backup_code = data.get("backup_code")

    if not backup_code:
        return jsonify({"error": "backup_code is required"}), 400

    # Tìm cư dân theo mã backup còn active
    sql = """
        SELECT r.*
        FROM resident_backup_codes b
        JOIN residents r ON r.id = b.resident_id
        WHERE b.backup_code = %s AND b.is_active = 1 AND r.status = 'active'
    """
    resident = query_one(sql, (backup_code,))

    if not resident:
        return jsonify({"error": "Invalid or inactive backup code"}), 404

    # Ghi log vào bảng parking_logs (cư dân vào bãi)
    now = datetime.now()
    log_sql = """
        INSERT INTO parking_logs (event_time, event_type, user_type, resident_id, plate)
        VALUES (%s, 'resident_in', 'resident', %s, NULL)
    """
    execute(log_sql, (now, resident["id"]))

    return jsonify({
        "message": "Backup code accepted, gate can be opened",
        "resident_id": resident["id"],
        "full_name": resident["full_name"],
        "floor": resident["floor"],
        "room": resident["room"]
    })
