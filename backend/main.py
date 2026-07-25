import os
import shutil
import threading
import traceback
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pdf_processor import process_pdf
from storage import Storage
from units import UNIT_KEYWORDS, expand_keywords, resolve_unit_key

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

storage = Storage(INDEX_PATH)

app = FastAPI(title="入試問題集リファレンスアプリ")

app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/meta")
def meta():
    data = storage.all()
    universities = sorted({b["university"] for b in data["blocks"] if b.get("university")})
    subjects = sorted({b["subject"] for b in data["blocks"] if b.get("subject")})
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


@app.get("/api/blocks")
def list_blocks(pdf_id: Optional[str] = None):
    data = storage.all()
    blocks = data["blocks"]
    if pdf_id:
        blocks = [b for b in blocks if b["pdf_id"] == pdf_id]
    return blocks


class BlockUpdate(BaseModel):
    university: Optional[str] = None
    year: Optional[str] = None
    subject: Optional[str] = None
    unit_tags: Optional[List[str]] = None


@app.patch("/api/blocks/{block_id}")
def update_block(block_id: str, body: BlockUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="更新する項目がありません")
    updated = storage.update_block(block_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="指定された問題が見つかりません")
    return updated


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


def _process_in_background(pdf_id, stored_path):
    def progress_cb(done, total):
        storage.update_pdf(pdf_id, pages_done=done, pages_total=total)

    try:
        blocks = process_pdf(stored_path, IMAGES_DIR, progress_cb=progress_cb)
        storage.finish_pdf(pdf_id, blocks)
    except Exception as e:
        traceback.print_exc()
        storage.update_pdf(pdf_id, status="error", error=str(e))


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    results = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            results.append({"filename": f.filename, "status": "error", "detail": "PDFファイルのみ対応しています"})
            continue

        pdf_id = uuid.uuid4().hex
        stored_filename = f"{pdf_id}.pdf"
        stored_path = os.path.join(UPLOADS_DIR, stored_filename)
        with open(stored_path, "wb") as out:
            shutil.copyfileobj(f.file, out)

        pdf_meta = {
            "id": pdf_id,
            "filename": f.filename,
            "stored_filename": stored_filename,
            "uploaded_at": datetime.utcnow().isoformat(),
            "status": "processing",
            "error": None,
            "pages_done": 0,
            "pages_total": None,
        }
        storage.add_pdf_pending(pdf_meta)

        thread = threading.Thread(target=_process_in_background, args=(pdf_id, stored_path), daemon=True)
        thread.start()

        results.append({"filename": f.filename, "status": "processing", "pdf_id": pdf_id})

    return {"results": results}


class SearchRequest(BaseModel):
    unit: str
    count: int = 5
    university: Optional[str] = None
    subject: Optional[str] = None


UNIT_TAG_SCORE_BONUS = 5


@app.post("/api/search")
def search(req: SearchRequest):
    if req.count <= 0:
        raise HTTPException(status_code=400, detail="問題数は1以上を指定してください")

    keywords = expand_keywords(req.unit)
    if not keywords:
        raise HTTPException(status_code=400, detail="単元を入力してください")
    resolved_key = resolve_unit_key(req.unit)

    data = storage.all()
    pdfs_done = {p["id"] for p in data["pdfs"] if p.get("status") == "done"}

    scored = []
    for block in data["blocks"]:
        if block["pdf_id"] not in pdfs_done:
            continue
        if req.university and req.university.strip():
            if req.university.strip() not in (block.get("university") or ""):
                continue
        if req.subject and req.subject.strip():
            if req.subject.strip() not in (block.get("subject") or ""):
                continue

        text = block["text"]
        score = sum(text.count(kw) for kw in keywords)
        if resolved_key and resolved_key in (block.get("unit_tags") or []):
            score += UNIT_TAG_SCORE_BONUS
        if score > 0:
            scored.append((score, block))

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
                "university": block.get("university") or "不明",
                "year": block.get("year") or "",
                "subject": block.get("subject") or "",
                "unit_tags": block.get("unit_tags") or [],
                "image_url": f"/images/{block['image_file']}",
                "score": score,
                "excerpt": block["text"][:80],
            }
            for score, block in selected
        ],
    }
