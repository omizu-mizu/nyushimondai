import os
import shutil
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pdf_processor import process_pdf
from storage import Storage
from units import UNIT_KEYWORDS, expand_keywords

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

storage = Storage(INDEX_PATH)

app = FastAPI(title="入試問題リファレンスアプリ")

app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/meta")
def meta():
    data = storage.all()
    universities = sorted({p["university"] for p in data["pdfs"] if p.get("university")})
    subjects = sorted({p["subject"] for p in data["pdfs"] if p.get("subject")})
    return {
        "units": list(UNIT_KEYWORDS.keys()),
        "universities": universities,
        "subjects": subjects,
        "pdf_count": len(data["pdfs"]),
        "block_count": len(data["blocks"]),
    }


@app.get("/api/pdfs")
def list_pdfs():
    data = storage.all()
    counts = {}
    for b in data["blocks"]:
        counts[b["pdf_id"]] = counts.get(b["pdf_id"], 0) + 1
    result = []
    for p in data["pdfs"]:
        item = dict(p)
        item["block_count"] = counts.get(p["id"], 0)
        result.append(item)
    result.sort(key=lambda x: x["uploaded_at"], reverse=True)
    return result


@app.delete("/api/pdfs/{pdf_id}")
def delete_pdf(pdf_id: str):
    data = storage.all()
    pdf = next((p for p in data["pdfs"] if p["id"] == pdf_id), None)
    if not pdf:
        raise HTTPException(status_code=404, detail="指定されたPDFが見つかりません")

    removed_blocks = storage.delete_pdf(pdf_id)
    for b in removed_blocks:
        image_path = os.path.join(IMAGES_DIR, b["image_file"])
        if os.path.exists(image_path):
            os.remove(image_path)

    pdf_path = os.path.join(UPLOADS_DIR, pdf["stored_filename"])
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    return {"status": "deleted"}


@app.post("/api/upload")
async def upload(
    files: List[UploadFile] = File(...),
    universities: List[str] = Form(...),
    years: List[str] = Form(...),
    subjects: List[str] = Form(...),
):
    if not (len(files) == len(universities) == len(years) == len(subjects)):
        raise HTTPException(status_code=400, detail="メタデータの件数がファイル数と一致しません")

    results = []
    for f, university, year, subject in zip(files, universities, years, subjects):
        if not f.filename.lower().endswith(".pdf"):
            results.append({"filename": f.filename, "status": "error", "detail": "PDFファイルのみ対応しています"})
            continue

        pdf_id = uuid.uuid4().hex
        stored_filename = f"{pdf_id}.pdf"
        stored_path = os.path.join(UPLOADS_DIR, stored_filename)
        with open(stored_path, "wb") as out:
            shutil.copyfileobj(f.file, out)

        try:
            blocks = process_pdf(stored_path, IMAGES_DIR)
        except Exception as e:
            os.remove(stored_path)
            results.append({"filename": f.filename, "status": "error", "detail": f"処理に失敗しました: {e}"})
            continue

        pdf_meta = {
            "id": pdf_id,
            "filename": f.filename,
            "stored_filename": stored_filename,
            "university": university.strip(),
            "year": year.strip(),
            "subject": subject.strip(),
            "uploaded_at": datetime.utcnow().isoformat(),
        }
        storage.add_pdf(pdf_meta, blocks)
        results.append({
            "filename": f.filename,
            "status": "ok",
            "block_count": len(blocks),
        })

    return {"results": results}


class SearchRequest(BaseModel):
    unit: str
    count: int = 5
    university: Optional[str] = None
    subject: Optional[str] = None


@app.post("/api/search")
def search(req: SearchRequest):
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="問題数は1以上を指定してください")

    keywords = expand_keywords(req.unit)
    if not keywords:
        raise HTTPException(status_code=400, detail="単元を入力してください")

    data = storage.all()
    pdf_by_id = {p["id"]: p for p in data["pdfs"]}

    scored = []
    for block in data["blocks"]:
        pdf = pdf_by_id.get(block["pdf_id"])
        if not pdf:
            continue
        if req.university and req.university.strip():
            if req.university.strip() not in (pdf.get("university") or ""):
                continue
        if req.subject and req.subject.strip():
            if req.subject.strip() not in (pdf.get("subject") or ""):
                continue

        text = block["text"]
        score = sum(text.count(kw) for kw in keywords)
        if score > 0:
            scored.append((score, block, pdf))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[: req.count]

    return {
        "matched_total": len(scored),
        "returned": len(selected),
        "keywords_used": keywords,
        "problems": [
            {
                "id": block["id"],
                "label": block["label"],
                "university": pdf.get("university") or "不明",
                "year": pdf.get("year") or "",
                "subject": pdf.get("subject") or "",
                "source_filename": pdf["filename"],
                "page_start": block["page_start"],
                "page_end": block["page_end"],
                "image_url": f"/images/{block['image_file']}",
                "score": score,
                "excerpt": block["text"][:80],
            }
            for score, block, pdf in selected
        ],
    }
