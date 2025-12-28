from flask import Blueprint, request, jsonify
from datetime import datetime
import random

from .db import query_one, execute
from backend.config import Config

gate_bp = Blueprint("gate", __name__, url_prefix="/api/gate")


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


# =============== GUEST ===============

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


# =============== RESIDENT – BACKUP CODE ===============

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


# =============== RESIDENT – CHECKIN/CHECKOUT THƯỜNG ===============

@gate_bp.route("/resident/checkin", methods=["POST"])
def resident_checkin():
    """
    Cư dân vào bãi (nhận diện thành công).
    Body JSON:
    {
      "resident_id": 1,
      "plate": "59A12345"
    }
    """
    data = request.get_json() or {}
    resident_id = data.get("resident_id")
    plate = data.get("plate")

    if not resident_id:
        return jsonify({"error": "resident_id is required"}), 400

    # Kiểm tra cư dân có tồn tại & đang active
    sql = "SELECT * FROM residents WHERE id = %s AND status = 'active'"
    resident = query_one(sql, (resident_id,))
    if not resident:
        return jsonify({"error": "Resident not found or inactive"}), 404

    now = datetime.now()

    # Ghi log vào bảng parking_logs
    log_sql = """
        INSERT INTO parking_logs (event_time, event_type, user_type, resident_id, plate)
        VALUES (%s, 'resident_in', 'resident', %s, %s)
    """
    execute(log_sql, (now, resident_id, plate))

    return jsonify({
        "message": "Resident checkin logged",
        "resident_id": resident_id,
        "full_name": resident["full_name"],
        "plate": plate,
        "event_time": now.isoformat()
    })


@gate_bp.route("/resident/checkout", methods=["POST"])
def resident_checkout():
    """
    Cư dân ra khỏi bãi (không tính phí).
    Body JSON:
    {
      "resident_id": 1,
      "plate": "59A12345"
    }
    """
    data = request.get_json() or {}
    resident_id = data.get("resident_id")
    plate = data.get("plate")

    if not resident_id:
        return jsonify({"error": "resident_id is required"}), 400

    sql = "SELECT * FROM residents WHERE id = %s AND status = 'active'"
    resident = query_one(sql, (resident_id,))
    if not resident:
        return jsonify({"error": "Resident not found or inactive"}), 404

    now = datetime.now()

    log_sql = """
        INSERT INTO parking_logs (event_time, event_type, user_type, resident_id, plate)
        VALUES (%s, 'resident_out', 'resident', %s, %s)
    """
    execute(log_sql, (now, resident_id, plate))

    return jsonify({
        "message": "Resident checkout logged",
        "resident_id": resident_id,
        "full_name": resident["full_name"],
        "plate": plate,
        "event_time": now.isoformat()
    })
