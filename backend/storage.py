"""問題のメタデータを保持する単純なJSONファイルストア。"""
import json
import os
import threading

_LOCK = threading.Lock()


class Storage:
    def __init__(self, index_path):
        self.index_path = index_path
        if not os.path.exists(index_path):
            self._write({"pdfs": [], "blocks": []})

    def _read(self):
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_pdf_pending(self, pdf_meta):
        with _LOCK:
            data = self._read()
            data["pdfs"].append(pdf_meta)
            self._write(data)

    def update_pdf(self, pdf_id, **fields):
        with _LOCK:
            data = self._read()
            for p in data["pdfs"]:
                if p["id"] == pdf_id:
                    p.update(fields)
                    break
            self._write(data)

    def append_blocks(self, pdf_id, blocks):
        """処理中のPDFについて、完了したエントリ分のブロックを逐次保存する。
        処理が途中で中断されても、ここで保存済みのブロックは失われない。
        """
        if not blocks:
            return
        with _LOCK:
            data = self._read()
            for b in blocks:
                b["pdf_id"] = pdf_id
            data["blocks"].extend(blocks)
            self._write(data)

    def mark_pdf_done(self, pdf_id):
        with _LOCK:
            data = self._read()
            for p in data["pdfs"]:
                if p["id"] == pdf_id:
                    p["status"] = "done"
                    p["error"] = None
                    break
            self._write(data)

    def all(self):
        with _LOCK:
            return self._read()

    def update_block(self, block_id, **fields):
        with _LOCK:
            data = self._read()
            updated = None
            for b in data["blocks"]:
                if b["id"] == block_id:
                    b.update(fields)
                    updated = b
                    break
            self._write(data)
            return updated

    def delete_pdf(self, pdf_id):
        with _LOCK:
            data = self._read()
            data["pdfs"] = [p for p in data["pdfs"] if p["id"] != pdf_id]
            removed_blocks = [b for b in data["blocks"] if b["pdf_id"] == pdf_id]
            data["blocks"] = [b for b in data["blocks"] if b["pdf_id"] != pdf_id]
            self._write(data)
            return removed_blocks

    def delete_block(self, block_id):
        with _LOCK:
            data = self._read()
            removed = next((b for b in data["blocks"] if b["id"] == block_id), None)
            data["blocks"] = [b for b in data["blocks"] if b["id"] != block_id]
            self._write(data)
            return removed
