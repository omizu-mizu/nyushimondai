const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabButtons.forEach((b) => b.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "list") {
      loadPdfList();
    }
  });
});

async function loadMeta() {
  const res = await fetch("/api/meta");
  const meta = await res.json();

  const unitList = document.getElementById("unit-list");
  unitList.innerHTML = meta.units.map((u) => `<option value="${escapeHtml(u)}">`).join("");

  const universityList = document.getElementById("university-list");
  universityList.innerHTML = meta.universities.map((u) => `<option value="${escapeHtml(u)}">`).join("");

  const subjectList = document.getElementById("subject-list");
  subjectList.innerHTML = meta.subjects.map((s) => `<option value="${escapeHtml(s)}">`).join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
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
      .map(
        (p) => `
      <div class="result-card">
        <img src="${p.image_url}" alt="${escapeHtml(p.label)}" loading="lazy" />
        <div class="result-meta">
          <span><strong>${escapeHtml(p.label)}</strong></span>
          <span>${escapeHtml(p.university)}</span>
          <span>${escapeHtml(p.year)}</span>
          <span>${escapeHtml(p.subject)}</span>
          <span>出典: ${escapeHtml(p.source_filename)} (p.${p.page_start}${p.page_end !== p.page_start ? "-" + p.page_end : ""})</span>
        </div>
      </div>`
      )
      .join("");
  } catch (err) {
    searchStatus.textContent = `エラー: ${err.message}`;
  }
});

// ---- アップロード ----
const fileInput = document.getElementById("file-input");
const fileMetaList = document.getElementById("file-meta-list");
const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");

fileInput.addEventListener("change", () => {
  fileMetaList.innerHTML = "";
  Array.from(fileInput.files).forEach((file, idx) => {
    const row = document.createElement("div");
    row.className = "file-meta-row";
    row.innerHTML = `
      <div class="filename">${escapeHtml(file.name)}</div>
      <div class="fields">
        <input type="text" placeholder="大学名 (任意)" data-field="university" data-idx="${idx}" />
        <input type="text" placeholder="年度 (任意)" data-field="year" data-idx="${idx}" />
        <input type="text" placeholder="科目 (任意)" data-field="subject" data-idx="${idx}" />
      </div>
    `;
    fileMetaList.appendChild(row);
  });
});

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (fileInput.files.length === 0) {
    uploadStatus.textContent = "PDFファイルを選択してください。";
    return;
  }

  uploadStatus.textContent = "アップロード・解析中... (ページ数が多いPDFは時間がかかります)";

  const formData = new FormData();
  Array.from(fileInput.files).forEach((file, idx) => {
    formData.append("files", file);
    formData.append("universities", fileMetaList.querySelector(`[data-field="university"][data-idx="${idx}"]`).value);
    formData.append("years", fileMetaList.querySelector(`[data-field="year"][data-idx="${idx}"]`).value);
    formData.append("subjects", fileMetaList.querySelector(`[data-field="subject"][data-idx="${idx}"]`).value);
  });

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      uploadStatus.textContent = `エラー: ${data.detail || "アップロードに失敗しました"}`;
      return;
    }
    uploadStatus.textContent = data.results
      .map((r) =>
        r.status === "ok"
          ? `✔ ${r.filename}: ${r.block_count}問検出`
          : `✘ ${r.filename}: ${r.detail}`
      )
      .join("\n");
    uploadForm.reset();
    fileMetaList.innerHTML = "";
    loadMeta();
  } catch (err) {
    uploadStatus.textContent = `エラー: ${err.message}`;
  }
});

// ---- アップロード済み一覧 ----
async function loadPdfList() {
  const container = document.getElementById("pdf-list");
  container.innerHTML = "読み込み中...";
  const res = await fetch("/api/pdfs");
  const pdfs = await res.json();

  if (pdfs.length === 0) {
    container.innerHTML = "<p>まだPDFがアップロードされていません。</p>";
    return;
  }

  const rows = pdfs
    .map(
      (p) => `
    <tr>
      <td>${escapeHtml(p.filename)}</td>
      <td>${escapeHtml(p.university || "-")}</td>
      <td>${escapeHtml(p.year || "-")}</td>
      <td>${escapeHtml(p.subject || "-")}</td>
      <td>${p.block_count}問</td>
      <td><button class="delete-btn" data-id="${p.id}">削除</button></td>
    </tr>`
    )
    .join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr><th>ファイル名</th><th>大学名</th><th>年度</th><th>科目</th><th>検出問題数</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  container.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("このPDFと関連する問題データを削除しますか?")) return;
      await fetch(`/api/pdfs/${btn.dataset.id}`, { method: "DELETE" });
      loadPdfList();
      loadMeta();
    });
  });
}

loadMeta();
