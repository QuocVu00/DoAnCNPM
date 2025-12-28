// static/js/gate.js

const API_BASE = "/gate"; // chỉnh cho khớp với blueprint Người 1

function showResult(html) {
  const el = document.getElementById("gate-result");
  el.innerHTML = html;
}

document.addEventListener("DOMContentLoaded", () => {
  const inputImage = document.getElementById("gate-image");
  const preview = document.getElementById("preview-image");

  if (inputImage) {
    inputImage.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      preview.src = url;
      preview.classList.remove("d-none");
    });
  }

  // Xe cư dân – Nhận diện khuôn mặt (demo: chỉ gọi API, backend tự dùng webcam)
  const btnResidentFace = document.getElementById("btn-resident-face");
  if (btnResidentFace) {
    btnResidentFace.addEventListener("click", async () => {
      showResult("<div class='alert alert-info'>Đang nhận diện cư dân...</div>");
      try {
        const res = await fetch(`${API_BASE}/resident/face`, {
          method: "POST",
        });
        const data = await res.json();
        if (data.success) {
          showResult(
            `<div class="alert alert-success">
              Cư dân: <b>${data.resident_name}</b> (ID: ${data.resident_id}) – Cổng đã mở!
            </div>`
          );
        } else {
          showResult(
            `<div class="alert alert-danger">
              Không nhận diện được khuôn mặt. ${data.message || ""}
            </div>`
          );
        }
      } catch (err) {
        console.error(err);
        showResult(`<div class="alert alert-danger">Lỗi gọi API.</div>`);
      }
    });
  }

  // Xe cư dân – Mã dự phòng
  const btnBackup = document.getElementById("btn-backup-login");
  if (btnBackup) {
    btnBackup.addEventListener("click", async () => {
      const code = document.getElementById("backup-code").value.trim();
      if (!code) {
        alert("Vui lòng nhập mã dự phòng");
        return;
      }
      showResult("<div class='alert alert-info'>Đang kiểm tra mã dự phòng...</div>");
      try {
        const res = await fetch(`${API_BASE}/resident/backup-login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ backup_code: code }),
        });
        const data = await res.json();
        if (data.success) {
          showResult(
            `<div class="alert alert-success">
              Mã dự phòng hợp lệ. Cư dân: <b>${data.resident_name}</b> – Cổng đã mở!
            </div>`
          );
        } else {
          showResult(
            `<div class="alert alert-danger">
              Mã dự phòng không đúng hoặc đã vô hiệu.
            </div>`
          );
        }
      } catch (err) {
        console.error(err);
        showResult(`<div class="alert alert-danger">Lỗi gọi API.</div>`);
      }
    });
  }

  // Xe khách ngoài – Check-in (tạo vé 6 số)
  const btnGuestIn = document.getElementById("btn-guest-checkin");
  if (btnGuestIn) {
    btnGuestIn.addEventListener("click", async () => {
      // demo: không gửi ảnh, Người 1 có thể gọi OCR nội bộ
      showResult("<div class='alert alert-info'>Đang tạo mã vé 6 số...</div>");
      try {
        const res = await fetch(`${API_BASE}/guest/checkin`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}), // sau có thể gửi plate/image_path
        });
        const data = await res.json();
        if (data.success) {
          showResult(
            `<div class="alert alert-success">
              Mã vé của bạn: <b>${data.ticket_code}</b><br/>
              Vui lòng giữ kỹ mã này để xuất trình khi lấy xe.
            </div>`
          );
        } else {
          showResult(`<div class="alert alert-danger">Không tạo được vé.</div>`);
        }
      } catch (err) {
        console.error(err);
        showResult(`<div class="alert alert-danger">Lỗi gọi API.</div>`);
      }
    });
  }

  // Xe khách ngoài – Checkout
  const btnGuestOut = document.getElementById("btn-guest-checkout");
  if (btnGuestOut) {
    btnGuestOut.addEventListener("click", async () => {
      const ticket = document.getElementById("ticket-code").value.trim();
      if (!ticket) {
        alert("Vui lòng nhập mã vé");
        return;
      }
      showResult("<div class='alert alert-info'>Đang tính tiền...</div>");
      try {
        const res = await fetch(`${API_BASE}/guest/checkout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket_code: ticket }),
        });
        const data = await res.json();
        if (data.success) {
          showResult(
            `<div class="alert alert-success">
              Thời gian gửi: <b>${data.hours} giờ</b><br/>
              Số tiền phải trả: <b>${data.amount.toLocaleString()} VNĐ</b><br/>
              Cảm ơn bạn đã sử dụng dịch vụ!
            </div>`
          );
        } else {
          showResult(
            `<div class="alert alert-danger">
              Mã vé không hợp lệ hoặc đã dùng rồi.
            </div>`
          );
        }
      } catch (err) {
        console.error(err);
        showResult(`<div class="alert alert-danger">Lỗi gọi API.</div>`);
      }
    });
  }
});
