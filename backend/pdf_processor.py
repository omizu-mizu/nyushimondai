"""PDFを「大問」単位に分割し、該当ページ範囲を画像として切り出すモジュール。

入試問題PDFは大問見出し(「第1問」「大問2」「[3]」など)を手がかりに区切る。
見出しが見つからない場合は1ページ=1問題として扱うフォールバックを行う。
"""
import io
import os
import re
import uuid
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image

MARKER_PATTERNS = [
    re.compile(r'^第[0-90-9一二三四五六七八九十百]+問'),
    re.compile(r'^大問[0-90-9一二三四五六七八九十]+'),
    re.compile(r'^\[[0-90-9]+\]\s*$'),
]

# 大問見出しが検出できなかった場合にのみ使うフォールバックパターン
FALLBACK_MARKER_PATTERNS = [
    re.compile(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\s*$'),
]

ZOOM = 2.0  # 画像出力の解像度倍率
MIN_RECT_HEIGHT = 5.0
MIN_MEANINGFUL_HEIGHT = 15.0  # これ未満の高さのページ断片は内容なしとみなし除外する


@dataclass
class Marker:
    page_index: int
    y0: float
    label: str


def _line_text(line):
    return "".join(span.get("text", "") for span in line.get("spans", [])).strip()


def _find_markers(doc, patterns):
    markers = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        raw = page.get_text("dict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                text = _line_text(line)
                if not text:
                    continue
                for pattern in patterns:
                    if pattern.match(text):
                        y0 = line["bbox"][1]
                        markers.append(Marker(page_index, y0, text[:20]))
                        break
    return markers


def _clamp_rect(page, rect):
    x0 = max(rect.x0, page.rect.x0)
    y0 = max(rect.y0, page.rect.y0)
    x1 = min(rect.x1, page.rect.x1)
    y1 = min(rect.y1, page.rect.y1)
    if y1 - y0 < MIN_RECT_HEIGHT:
        y1 = min(y0 + MIN_RECT_HEIGHT, page.rect.y1)
    return fitz.Rect(x0, y0, x1, y1)


def _render_clip(page, rect):
    rect = _clamp_rect(page, rect)
    mat = fitz.Matrix(ZOOM, ZOOM)
    pix = page.get_pixmap(matrix=mat, clip=rect)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"), rect


def _stack_images(images):
    if len(images) == 1:
        return images[0]
    width = max(im.width for im in images)
    height = sum(im.height for im in images)
    combined = Image.new("RGB", (width, height), "white")
    y = 0
    for im in images:
        combined.paste(im, (0, y))
        y += im.height
    return combined


def _block_end(doc, markers, i):
    if i + 1 < len(markers):
        nxt = markers[i + 1]
        return nxt.page_index, nxt.y0
    last_page_index = len(doc) - 1
    return last_page_index, doc[last_page_index].rect.y1


def _save_block(images_dir, label, page_start, page_end, images, text):
    block_id = uuid.uuid4().hex
    combined = _stack_images(images)
    filename = f"{block_id}.png"
    combined.save(os.path.join(images_dir, filename))
    return {
        "id": block_id,
        "label": label,
        "page_start": page_start + 1,
        "page_end": page_end + 1,
        "image_file": filename,
        "text": text.strip(),
    }


def process_pdf(pdf_path, images_dir):
    doc = fitz.open(pdf_path)
    try:
        markers = _find_markers(doc, MARKER_PATTERNS)
        if not markers:
            markers = _find_markers(doc, FALLBACK_MARKER_PATTERNS)

        blocks = []
        if not markers:
            for page_index in range(len(doc)):
                page = doc[page_index]
                image, _ = _render_clip(page, page.rect)
                text = page.get_text()
                blocks.append(_save_block(
                    images_dir, f"P{page_index + 1}", page_index, page_index,
                    [image], text,
                ))
            return blocks

        for i, marker in enumerate(markers):
            end_page, end_y = _block_end(doc, markers, i)
            images = []
            texts = []

            if marker.page_index == end_page:
                page = doc[marker.page_index]
                rect = fitz.Rect(page.rect.x0, marker.y0, page.rect.x1, end_y)
                image, rect = _render_clip(page, rect)
                images.append(image)
                texts.append(page.get_text(clip=rect))
                actual_end_page = end_page
            else:
                first_page = doc[marker.page_index]
                rect = fitz.Rect(first_page.rect.x0, marker.y0, first_page.rect.x1, first_page.rect.y1)
                image, rect = _render_clip(first_page, rect)
                images.append(image)
                texts.append(first_page.get_text(clip=rect))
                actual_end_page = marker.page_index

                for mid in range(marker.page_index + 1, end_page):
                    mid_page = doc[mid]
                    image, _ = _render_clip(mid_page, mid_page.rect)
                    images.append(image)
                    texts.append(mid_page.get_text())
                    actual_end_page = mid

                # 次の見出しがページ先頭付近にある場合、そのページには
                # 実質的な内容が無いため画像・ページ範囲に含めない
                last_page = doc[end_page]
                if end_y - last_page.rect.y0 > MIN_MEANINGFUL_HEIGHT:
                    rect = fitz.Rect(last_page.rect.x0, last_page.rect.y0, last_page.rect.x1, end_y)
                    image, rect = _render_clip(last_page, rect)
                    images.append(image)
                    texts.append(last_page.get_text(clip=rect))
                    actual_end_page = end_page

            blocks.append(_save_block(
                images_dir, marker.label, marker.page_index, actual_end_page,
                images, "\n".join(texts),
            ))

        return blocks
    finally:
        doc.close()
