// static/js/resident.js

const RESIDENT_API_BASE = "/resident";

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form-support");
  const supportResult = document.getElementById("support-result");

  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = document.getElementById("support-content").value.trim();
    if (!content) {
      alert("Vui lòng nhập nội dung yêu cầu");
      return;
    }
    supportResult.innerHTML = `<div class="text-muted">Đang gửi yêu cầu...</div>`;
    try {
      const res = await fetch(`${RESIDENT_API_BASE}/support`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await res.json();
      if (data.success) {
        supportResult.innerHTML = `<div class="alert alert-success">Đã gửi yêu cầu tới admin.</div>`;
        document.getElementById("support-content").value = "";
      } else {
        supportResult.innerHTML = `<div class="alert alert-danger">Gửi thất bại.</div>`;
      }
    } catch (err) {
      console.error(err);
      supportResult.innerHTML = `<div class="alert alert-danger">Lỗi gọi API.</div>`;
    }
  });
});
