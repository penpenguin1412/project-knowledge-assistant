"""Small, inspectable retrieval and extraction pipeline. No simulated LLM."""
import io
import math
import re
import zipfile
from collections import Counter
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 120_000


def parse_file(name, raw):
    suffix = Path(name).suffix.lower()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("文件为空或超过 5 MB。")
    if suffix in {".md", ".txt"}:
        try:
            pages = [("文本", raw.decode("utf-8-sig"))]
        except UnicodeDecodeError:
            raise ValueError("文本必须使用 UTF-8 编码。") from None
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("PDF 不得加密，且最多 100 页。")
        pages = []
        for i, page in enumerate(reader.pages):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 5 * MAX_BYTES:
                raise ValueError("PDF 单页内容过大。")
            pages.append((f"第 {i + 1} 页", page.extract_text() or ""))
    elif suffix == ".docx":
        from docx import Document
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(z.file_size for z in archive.infolist()) > 20 * MAX_BYTES:
                raise ValueError("DOCX 解压内容过大。")
        doc = Document(io.BytesIO(raw))
        pages = [(f"段落 {i + 1}", p.text) for i, p in enumerate(doc.paragraphs)]
        pages += [(f"表 {i + 1} 行 {j + 1}", " | ".join(c.text for c in row.cells))
                  for i, table in enumerate(doc.tables) for j, row in enumerate(table.rows)]
    else:
        raise ValueError("仅支持 TXT、MD、可提取文字的 PDF、DOCX。")
    if sum(len(t) for _, t in pages) > MAX_TEXT:
        raise ValueError("提取文字超过 12 万字符，请拆分资料。")
    chunks = []
    for label, text in pages:
        if "\x00" in text:
            raise ValueError("文件含有不支持的二进制内容。")
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            for offset in range(0, len(line), 500):
                part = line[offset:offset + 600]
                if part:
                    chunks.append({"location": f"{label} · 行 {line_no} · 字 {offset + 1}", "text": part})
    if not chunks:
        raise ValueError("未发现文字。扫描 PDF 请先使用 OCR，本系统不伪造识别结果。")
    return chunks


def tokens(text):
    result = re.findall(r"[a-z0-9]+", text.lower())
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        result += [segment[i:i + 2] for i in range(len(segment) - 1)]
    return result


def retrieve(question, chunks, limit=5):
    query = set(tokens(question))
    if not query or not chunks:
        return []
    # ponytail: linear BM25 scan for <= 3000 chunks; use an indexed search engine above this cap.
    counts = [Counter(tokens(c["text"])) for c in chunks]
    avg = sum(sum(c.values()) for c in counts) / len(counts) or 1
    df = {t: sum(t in c for c in counts) for t in query}
    ranked = []
    for chunk, count in zip(chunks, counts):
        matched = query & count.keys()
        if not matched:
            continue
        score = sum(math.log(1 + (len(chunks) - df[t] + .5) / (df[t] + .5)) *
                    count[t] * 2.2 / (count[t] + 1.2 * (.25 + .75 * sum(count.values()) / avg))
                    for t in matched)
        # Only a lexical relevance gate, not a proof that a question is answerable.
        if len(matched) / len(query) >= .18 and score >= .8:
            ranked.append({**chunk, "score": round(score, 3)})
    return sorted(ranked, key=lambda c: (-c["score"], c["id"]))[:limit]


def rule_tasks(text):
    """Explicit structured lines only. Missing owners/dates remain unknown."""
    rows = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*待办[：:]\s*(.+?)\s*[|｜]\s*负责人[：:]\s*(.*?)\s*[|｜]\s*截止[：:]\s*(.*?)\s*", line)
        if match:
            title, owner, due = match.groups()
            rows.append({"title": title, "owner": owner if owner != "待定" else "",
                         "due_date": due if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else None,
                         "quote": line.strip()})
    return rows
