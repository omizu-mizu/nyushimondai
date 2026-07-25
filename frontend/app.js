const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabButtons.forEach((b) => b.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "review") {
      loadReview();
    }
  });
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : str;
  return div.innerHTML;
}

async function loadMeta() {
  const res = await fetch("/api/meta");
  const meta = await res.json();

  document.getElementById("unit-list").innerHTML = meta.units.map((u) => `<option value="${escapeHtml(u)}">`).join("");
  document.getElementById("university-list").innerHTML = meta.universities.map((u) => `<option value="${escapeHtml(u)}">`).join("");
  document.getElementById("subject-list").innerHTML = meta.subjects.map((s) => `<option value="${escapeHtml(s)}">`).join("");
}

// ---- 検索 ----
const searchForm = document.getElementById("search-form");
const searchStatus = document.getElementById("search-status");
const searchResults = document.getElementById("search-results");

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  searchStatus.textContent = "検索中...";
  searchResults.innerHTML = "";

  const payload = {
    unit: document.getElementById("unit-input").value,
    count: parseInt(document.getElementById("count-input").value, 10),
    university: document.getElementById("university-input").value,
    subject: document.getElementById("subject-input").value,
  };

  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      searchStatus.textContent = `エラー: ${data.detail || "検索に失敗しました"}`;
      return;
    }

    searchStatus.textContent = `該当 ${data.matched_total} 件中 ${data.returned} 件を表示 (検索キーワード: ${data.keywords_used.join(", ")})`;

    if (data.problems.length === 0) {
      searchResults.innerHTML = `<p>条件に一致する問題が見つかりませんでした。PDFをアップロード済みか、単元名を確認してください。</p>`;
      return;
    }

    searchResults.innerHTML = data.problems
      .map((p) => {
        const tags = (p.unit_tags || []).map((t) => `<span class="tag-badge">${escapeHtml(t)}</span>`).join("");
        return `
      <div class="result-card">
        <img src="${p.image_url}" alt="${escapeHtml(p.label)}" loading="lazy" />
        <div class="result-meta">
          <span><strong>${escapeHtml(p.label)}</strong></span>
          <span>${escapeHtml(p.university)}</span>
          <span>${escapeHtml(p.year)}</span>
          <span>${escapeHtml(p.subject)}</span>
          ${tags}
        </div>
      </div>`;
      })
      .join("");
  } catch (err) {
    searchStatus.textContent = `エラー: ${err.message}`;
  }
});

// ---- アップロード ----
const fileInput = document.getElementById("file-input");
const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");
const uploadProgress = document.getElementById("upload-progress");

let pollTimer = null;

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (fileInput.files.length === 0) {
    uploadStatus.textContent = "PDFファイルを選択してください。";
    return;
  }

  uploadStatus.textContent = "アップロード中...";

  const formData = new FormData();
  Array.from(fileInput.files).forEach((file) => formData.append("files", file));

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      uploadStatus.textContent = `エラー: ${data.detail || "アップロードに失敗しました"}`;
      return;
    }

    const errors = data.results.filter((r) => r.status === "error");
    uploadStatus.textContent = errors.length
      ? errors.map((r) => `✘ ${r.filename}: ${r.detail}`).join("\n")
      : "アップロードしました。解析状況:";

    uploadForm.reset();
    startProgressPolling();
  } catch (err) {
    uploadStatus.textContent = `エラー: ${err.message}`;
  }
});

function startProgressPolling() {
  uploadProgress.style.display = "block";
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refreshProgress, 1500);
  refreshProgress();
}

async function refreshProgress() {
  const res = await fetch("/api/pdfs");
  const pdfs = await res.json();

  uploadProgress.innerHTML = pdfs
    .map((p) => {
      let barPct = 0;
      let statusText = "";
      if (p.status === "processing") {
        barPct = p.pages_total ? Math.round((p.pages_done / p.pages_total) * 100) : 0;
        statusText = p.pages_total ? `解析中... (${p.pages_done}/${p.pages_total}ページ)` : "解析準備中...";
      } else if (p.status === "done") {
        barPct = 100;
        statusText = `完了 (${p.block_count}問検出)`;
      } else if (p.status === "error") {
        barPct = 100;
        statusText = `エラー: ${escapeHtml(p.error || "")}`;
      }
      return `
        <div class="pdf-progress-row">
          <div class="filename">${escapeHtml(p.filename)}</div>
          <div>${statusText}</div>
          <div class="progress-bar"><div style="width:${barPct}%; background:${p.status === "error" ? "#dc2626" : ""}"></div></div>
        </div>`;
    })
    .join("");

  const stillProcessing = pdfs.some((p) => p.status === "processing");
  if (!stillProcessing && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    loadMeta();
  }
}

// ---- 検出結果の確認・修正 ----
async function loadReview() {
  const container = document.getElementById("review-list");
  container.innerHTML = "読み込み中...";

  const [pdfsRes, blocksRes] = await Promise.all([fetch("/api/pdfs"), fetch("/api/blocks")]);
  const pdfs = await pdfsRes.json();
  const blocks = await blocksRes.json();

  if (pdfs.length === 0) {
    container.innerHTML = "<p>まだPDFがアップロードされていません。</p>";
    return;
  }

  const blocksByPdf = {};
  blocks.forEach((b) => {
    (blocksByPdf[b.pdf_id] = blocksByPdf[b.pdf_id] || []).push(b);
  });

  container.innerHTML = pdfs
    .map((p) => {
      const pdfBlocks = blocksByPdf[p.id] || [];
      const statusLabel = { processing: "解析中", done: "完了", error: "エラー" }[p.status] || p.status;
      const blocksHtml = pdfBlocks
        .map(
          (b) => `
        <div class="review-block" data-block-id="${b.id}">
          <img src="/images/${b.image_file}" alt="${escapeHtml(b.label)}" loading="lazy" />
          <div class="fields">
            <div>
              <label>大問</label>
              <input type="text" value="${escapeHtml(b.label)}" disabled />
            </div>
            <div>
              <label>大学名</label>
              <input type="text" data-field="university" value="${escapeHtml(b.university)}" />
            </div>
            <div>
              <label>年度</label>
              <input type="text" data-field="year" value="${escapeHtml(b.year)}" />
            </div>
            <div>
              <label>科目</label>
              <input type="text" data-field="subject" value="${escapeHtml(b.subject)}" />
            </div>
            <div style="grid-column: span 2;">
              <label>単元タグ (カンマ区切り)</label>
              <input type="text" data-field="unit_tags" value="${escapeHtml((b.unit_tags || []).join(", "))}" />
            </div>
            <button type="button" class="save-btn">保存</button>
            <span class="save-result"></span>
          </div>
        </div>`
        )
        .join("");

      return `
        <div class="review-pdf-group">
          <h3>${escapeHtml(p.filename)}</h3>
          <div class="pdf-status">状態: ${statusLabel} / 検出問題数: ${p.block_count}</div>
          ${blocksHtml || "<p>問題が検出されませんでした。</p>"}
          <button type="button" class="delete-btn" data-pdf-id="${p.id}">このPDFを削除</button>
        </div>`;
    })
    .join("");

  container.querySelectorAll(".review-block").forEach((row) => {
    const blockId = row.dataset.blockId;
    row.querySelector(".save-btn").addEventListener("click", async () => {
      const payload = {
        university: row.querySelector('[data-field="university"]').value,
        year: row.querySelector('[data-field="year"]').value,
        subject: row.querySelector('[data-field="subject"]').value,
        unit_tags: row
          .querySelector('[data-field="unit_tags"]')
          .value.split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      };
      const resultEl = row.querySelector(".save-result");
      resultEl.textContent = "保存中...";
      try {
        const res = await fetch(`/api/blocks/${blockId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const data = await res.json();
          resultEl.textContent = `エラー: ${data.detail || ""}`;
          return;
        }
        resultEl.textContent = "保存しました";
        loadMeta();
      } catch (err) {
        resultEl.textContent = `エラー: ${err.message}`;
      }
    });
  });

  container.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("このPDFと関連する問題データを削除しますか?")) return;
      await fetch(`/api/pdfs/${btn.dataset.pdfId}`, { method: "DELETE" });
      loadReview();
      loadMeta();
    });
  });
}

loadMeta();
