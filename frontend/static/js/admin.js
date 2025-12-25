// static/js/admin.js

const ADMIN_API_BASE = "/admin";

document.addEventListener("DOMContentLoaded", () => {
  const btnLoad = document.getElementById("btn-load-report");
  const btnDetail = document.getElementById("btn-load-detail");
  const dateInput = document.getElementById("report-date");
  const summary = document.getElementById("report-summary");
  const detail = document.getElementById("report-detail");

  async function loadReport(detailMode = false) {
    const date = dateInput.value;
    if (!date) {
      alert("Vui lòng chọn ngày");
      return;
    }
    summary.innerHTML = `<div class="text-muted">Đang tải báo cáo...</div>`;
    if (detailMode) {
      detail.innerHTML = "";
    }

    try {
      const res = await fetch(
        `${ADMIN_API_BASE}/report/daily?date=${encodeURIComponent(date)}&detail=${detailMode ? "1" : "0"}`
      );
      const data = await res.json();
      if (!data.success) {
        summary.innerHTML = `<div class="alert alert-danger">Không lấy được báo cáo.</div>`;
        return;
      }

      summary.innerHTML = `
        <div class="alert alert-info">
          Ngày: <b>${data.date}</b><br/>
          Lượt xe cư dân: <b>${data.resident_count}</b><br/>
          Lượt xe khách ngoài: <b>${data.guest_count}</b><br/>
          Doanh thu khách ngoài: <b>${data.revenue.toLocaleString()} VNĐ</b>
        </div>`;

      if (detailMode && data.sessions) {
        let html = `
          <table class="table table-sm">
            <thead>
              <tr>
                <th>Biển số</th>
                <th>Mã vé</th>
                <th>Vào</th>
                <th>Ra</th>
                <th>Tiền</th>
              </tr>
            </thead>
            <tbody>
        `;
        for (const s of data.sessions) {
          html += `
            <tr>
              <td>${s.plate_number}</td>
              <td>${s.ticket_code}</td>
              <td>${s.checkin_time}</td>
              <td>${s.checkout_time}</td>
              <td>${s.amount.toLocaleString()} VNĐ</td>
            </tr>
          `;
        }
        html += `</tbody></table>`;
        detail.innerHTML = html;
      }
    } catch (err) {
      console.error(err);
      summary.innerHTML = `<div class="alert alert-danger">Lỗi gọi API.</div>`;
    }
  }

  if (btnLoad) {
    btnLoad.addEventListener("click", () => loadReport(false));
  }
  if (btnDetail) {
    btnDetail.addEventListener("click", () => loadReport(true));
  }
});
