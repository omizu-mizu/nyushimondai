"""問題集PDFを解析するモジュール。

想定するレイアウト(入試問題集でよく見られる形式):
  - 1つの「エントリ」= 1大学・1日程分の過去問。ページ上部に大学名を含む
    見出し行が現れることでエントリの開始を検知する。
  - エントリの前半ページ = 問題文(大問ごとに黒背景・白文字の番号アイコンが
    左マージンに振られている)。
  - エントリの後半ページ = 解答(同様に番号アイコンがあり、各小問に
    【単元名】のようなタグが付いている)。

このPDFは文字がアウトライン化されており通常のテキスト抽出ができないため、
ページを画像化してOCR(Tesseract)でテキストを取得し、番号アイコンは
画像処理(黒い正方形ブロックの検出)で位置を特定する。
"""
import difflib
import io
import os
import re
import uuid

import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image
from pytesseract import Output
from scipy import ndimage

from units import UNIT_KEYWORDS

ZOOM = 2.0

# 大問番号アイコン(黒背景・白文字の正方形)検出パラメータ
DARK_THRESHOLD = 100
MARKER_MIN_SIZE = 18  # これ未満は文字の一部などのノイズが多いため除外
MARKER_MAX_SIZE = 60
MARKER_FILL_MIN = 0.5
MARKER_MARGIN_RATIO = 0.65  # 2段組みの右カラムも拾えるよう広めに取る
MARKER_ASPECT_MIN = 0.8  # 番号アイコンはほぼ正方形なので、これ以外の縦横比は除外する
MARKER_ASPECT_MAX = 1.25
MARKER_COLUMN_TOLERANCE = 30  # 同じカラムとみなすx座標の許容誤差(px)
MIN_MEANINGFUL_HEIGHT = 20  # これ未満の高さの断片は内容なしとみなし除外

UNIT_TAG_RE = re.compile(r'[【\[〔](.{1,20}?)[】\]〕]')
DATE_RE = re.compile(r'(\d{4})\s*年')
UNIV_LINE_RE = re.compile(r'([^\s]{1,20}大学[^\s]{0,15})')
# 大学名だけの行(ページ左上の「28 札幌大学・A日程」のようなノンブル付き
# ランニングヘッダーではなく、独立した大きな見出し行)を優先的に拾う
TITLE_LINE_RE = re.compile(r'^([^\s\d]{1,20}大学[^\s]{0,15})$')
# 「試験日」はエントリの最初のページにのみ現れるため、これをエントリ開始の
# 目印として使う(大学名はランニングヘッダーとして全ページに出るため使えない)
EXAM_DATE_LINE_RE = re.compile(r'試験日')
UNIT_TAG_MATCH_THRESHOLD = 0.6  # OCRされたタグと辞書キーの類似度がこれ以上なら採用(低いと誤タグが増える)
HEADER_SCAN_LINES = 10

SUBJECT_KEYWORDS = [
    "数学", "英語", "国語", "現代文", "古文", "漢文",
    "物理", "化学", "生物", "地学",
    "日本史", "世界史", "地理", "倫理", "政治", "公民",
]


def render_page(page, zoom=ZOOM):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def find_markers(img):
    """黒背景・白文字の番号アイコンを検出し、読み順(左カラム上→下、
    右カラム上→下)のy座標一覧を返す。2段組みレイアウトに対応するため、
    x座標でカラムをクラスタリングしてから並べ替える。
    """
    arr = np.array(img.convert("L"))
    height, width = arr.shape
    dark = arr < DARK_THRESHOLD
    labeled, _ = ndimage.label(dark)
    objs = ndimage.find_objects(labeled)
    candidates = []
    for sl in objs:
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        h, w = y1 - y0, x1 - x0
        if not (MARKER_MIN_SIZE <= h <= MARKER_MAX_SIZE and MARKER_MIN_SIZE <= w <= MARKER_MAX_SIZE):
            continue
        aspect = w / h
        if not (MARKER_ASPECT_MIN <= aspect <= MARKER_ASPECT_MAX):
            continue
        if x0 > width * MARKER_MARGIN_RATIO:
            continue
        fill = dark[sl].sum() / (h * w)
        if fill < MARKER_FILL_MIN:
            continue
        candidates.append((x0, y0))

    if not candidates:
        return []

    columns = []  # [{"x": 代表x座標, "items": [y, ...]}, ...]
    for x0, y0 in sorted(candidates):
        col = next((c for c in columns if abs(c["x"] - x0) <= MARKER_COLUMN_TOLERANCE), None)
        if col is None:
            columns.append({"x": x0, "items": [y0]})
        else:
            col["items"].append(y0)
    columns.sort(key=lambda c: c["x"])

    ordered = []
    for col in columns:
        ordered.extend((col["x"], y) for y in sorted(col["items"]))
    return ordered


def ocr_lines(img):
    """OCR結果を行単位(テキスト+位置)にまとめて返す。"""
    data = pytesseract.image_to_data(img, lang="jpn", output_type=Output.DICT)
    lines = {}
    n = len(data["text"])
    for i in range(n):
        word = data["text"][i]
        if not word or not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = lines.setdefault(key, {"words": [], "top": [], "bottom": [], "left": []})
        entry["words"].append(word)
        entry["top"].append(data["top"][i])
        entry["bottom"].append(data["top"][i] + data["height"][i])
        entry["left"].append(data["left"][i])

    result = [
        {
            "text": "".join(v["words"]),
            "top": min(v["top"]),
            "bottom": max(v["bottom"]),
            "left": min(v["left"]),
        }
        for v in lines.values()
    ]
    result.sort(key=lambda l: l["top"])
    return result


def is_entry_start(lines):
    return any(EXAM_DATE_LINE_RE.search(l["text"]) for l in lines[:HEADER_SCAN_LINES])


def find_university_title(lines):
    texts = [l["text"].strip() for l in lines[:HEADER_SCAN_LINES] if l["text"].strip()]
    for line in texts:
        m = TITLE_LINE_RE.match(line)
        if m:
            return m.group(1)
    for line in texts:
        m = UNIV_LINE_RE.search(line)
        if m:
            return m.group(1)
    return None


def find_first_unit_tag_y(lines):
    for line in lines:
        if UNIT_TAG_RE.search(line["text"]):
            return line["top"]
    return None


def extract_year(text):
    m = DATE_RE.search(text)
    return m.group(1) if m else ""


PAREN_RE = re.compile(r'[\(（][^\)）]*[\)）]')


def extract_subject(text):
    # 「(地歴公民との選択)」のような注記から科目を誤検出しないよう、
    # 括弧内の文字列は判定対象から除外する
    cleaned = PAREN_RE.sub('', text)
    found = []
    for kw in SUBJECT_KEYWORDS:
        if kw in cleaned and kw not in found:
            found.append(kw)
    return "・".join(found)


def extract_unit_tags(text):
    canonical = set()
    for raw in UNIT_TAG_RE.findall(text):
        raw = raw.strip()
        if not raw:
            continue
        best_key, best_score = None, 0.0
        for key, synonyms in UNIT_KEYWORDS.items():
            for candidate in [key] + synonyms:
                if candidate in raw or raw in candidate:
                    # OCR誤読で前後にゴミが付いても、辞書語そのものが
                    # 部分文字列として現れていれば高信頼とみなす
                    score = 1.0
                else:
                    score = difflib.SequenceMatcher(None, raw, candidate).ratio()
                if score > best_score:
                    best_key, best_score = key, score
        if best_key and best_score >= UNIT_TAG_MATCH_THRESHOLD:
            canonical.add(best_key)
    return sorted(canonical)


def _stack_images(images):
    images = [im for im in images if im.height > 0 and im.width > 0]
    if not images:
        return None
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


def _lines_text_in_range(lines, y0, y1):
    return "\n".join(l["text"] for l in lines if y0 <= l["top"] < y1)


def _split_by_markers(pages):
    """問題ページ列([{img,lines,markers}, ...])をマーカー位置で分割し、
    大問ごとの画像とテキストを返す。問題ページは単一カラムのレイアウトを
    前提とし、y座標だけで範囲を決める(このアプリでは表示用画像が必要な
    問題ページにのみ使う)。
    """
    if not pages:
        return []

    all_markers = []
    for i, p in enumerate(pages):
        for _x, y in p["markers"]:
            all_markers.append((i, y))

    if not all_markers:
        images = [p["img"] for p in pages]
        text = "\n".join(_lines_text_in_range(p["lines"], 0, p["img"].height) for p in pages)
        return [{"images": images, "text": text}]

    segments = []
    for idx, (page_i, y0) in enumerate(all_markers):
        if idx + 1 < len(all_markers):
            end_page_i, end_y = all_markers[idx + 1]
        else:
            end_page_i, end_y = len(pages) - 1, pages[-1]["img"].height

        images = []
        texts = []
        if page_i == end_page_i:
            img = pages[page_i]["img"]
            images.append(img.crop((0, y0, img.width, max(end_y, y0 + 1))))
            texts.append(_lines_text_in_range(pages[page_i]["lines"], y0, end_y))
        else:
            first_img = pages[page_i]["img"]
            images.append(first_img.crop((0, y0, first_img.width, first_img.height)))
            texts.append(_lines_text_in_range(pages[page_i]["lines"], y0, first_img.height))

            for mid in range(page_i + 1, end_page_i):
                images.append(pages[mid]["img"])
                texts.append(_lines_text_in_range(pages[mid]["lines"], 0, pages[mid]["img"].height))

            last_img = pages[end_page_i]["img"]
            if end_y > MIN_MEANINGFUL_HEIGHT:
                images.append(last_img.crop((0, 0, last_img.width, end_y)))
            texts.append(_lines_text_in_range(pages[end_page_i]["lines"], 0, end_y))

        segments.append({"images": images, "text": "\n".join(texts)})

    return segments


def _solution_text_segments(pages):
    """解答ページ群から大問ごとのテキストを抽出する(画像は使わない)。

    解答ページは2段組みレイアウトのことが多く、単純なy座標範囲では
    カラムをまたぐと破綻するため、(ページ, カラム, y)の読み順で
    テキストをマーカーに割り当てる。
    """
    if not pages:
        return []

    # 番号アイコンとそれに付随するタグ・解答冒頭のテキストはほぼ同じ行に
    # あるため、数px単位のズレで前後関係が逆転しないよう行単位に丸めて
    # 比較する。同じ行内ではマーカーを常にテキストより先に並べる。
    ROW_BUCKET = 15

    events = []
    for page_i, p in enumerate(pages):
        width = p["img"].width
        for x0, y0 in p["markers"]:
            column = 0 if x0 < width * 0.5 else 1
            events.append(((page_i, column, y0 // ROW_BUCKET), 0, "marker", None))
        for line in p["lines"]:
            column = 0 if line["left"] < width * 0.5 else 1
            events.append(((page_i, column, line["top"] // ROW_BUCKET), 1, "line", line["text"]))

    events.sort(key=lambda e: (e[0], e[1]))

    segments = []
    current = None
    for _key, _tie, kind, payload in events:
        if kind == "marker":
            current = []
            segments.append(current)
        elif current is not None:
            current.append(payload)

    return ["\n".join(lines) for lines in segments]


def _build_blocks(entry, images_dir):
    problem_segments = _split_by_markers(entry["problem_pages"])
    solution_texts = _solution_text_segments(entry["solution_pages"])

    blocks = []
    for i, seg in enumerate(problem_segments):
        solution_text = solution_texts[i] if i < len(solution_texts) else ""
        combined_text = (seg["text"] + "\n" + solution_text).strip()
        unit_tags = extract_unit_tags(combined_text)

        image = _stack_images(seg["images"])
        if image is None:
            continue

        block_id = uuid.uuid4().hex
        filename = f"{block_id}.png"
        image.save(os.path.join(images_dir, filename))

        blocks.append({
            "id": block_id,
            "label": f"大問{i + 1}",
            "university": entry["university"],
            "year": entry["year"],
            "subject": entry["subject"],
            "unit_tags": unit_tags,
            "image_file": filename,
            "text": combined_text,
        })
    return blocks


def process_pdf(pdf_path, images_dir, progress_cb=None):
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    entries = []
    current = None
    mode = None  # "problem" | "solution"

    try:
        for page_index in range(total_pages):
            page = doc[page_index]
            img = render_page(page)
            lines = ocr_lines(img)
            full_text = "\n".join(l["text"] for l in lines)

            if is_entry_start(lines):
                title = find_university_title(lines)
                if title:
                    if current:
                        entries.append(current)
                    current = {
                        "university": title,
                        "year": extract_year(full_text),
                        "subject": extract_subject(full_text),
                        "problem_pages": [],
                        "solution_pages": [],
                    }
                    mode = "problem"

            if current is not None:
                markers = find_markers(img)
                tag_y = find_first_unit_tag_y(lines) if mode == "problem" else None

                if tag_y is not None:
                    # このページの途中で問題→解答に切り替わる場合、境界で
                    # 画像・行データ・マーカー座標を分けて両方に登録する
                    problem_img = img.crop((0, 0, img.width, tag_y))
                    solution_img = img.crop((0, tag_y, img.width, img.height))
                    problem_markers = [(x, y) for x, y in markers if y < tag_y]
                    solution_markers = [(x, y - tag_y) for x, y in markers if y >= tag_y]
                    problem_lines = [l for l in lines if l["top"] < tag_y]
                    solution_lines = [
                        {
                            "text": l["text"], "left": l["left"],
                            "top": l["top"] - tag_y, "bottom": l["bottom"] - tag_y,
                        }
                        for l in lines if l["top"] >= tag_y
                    ]

                    current["problem_pages"].append({
                        "img": problem_img, "lines": problem_lines, "markers": problem_markers,
                    })
                    current["solution_pages"].append({
                        "img": solution_img, "lines": solution_lines, "markers": solution_markers,
                    })
                    mode = "solution"
                else:
                    page_entry = {"img": img, "lines": lines, "markers": markers}
                    if mode == "problem":
                        current["problem_pages"].append(page_entry)
                    else:
                        current["solution_pages"].append(page_entry)

            if progress_cb:
                progress_cb(page_index + 1, total_pages)
    finally:
        doc.close()

    if current:
        entries.append(current)

    blocks = []
    for entry in entries:
        blocks.extend(_build_blocks(entry, images_dir))
    return blocks
