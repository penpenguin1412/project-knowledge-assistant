import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core import MAX_BYTES, parse_file, retrieve, rule_tasks
from model import QA_PROMPT, TASK_PROMPT, generate, model_status

ROOT = Path(__file__).parent
DB_PATH = Path(os.getenv("ASSISTANT_DB", ROOT / "data" / "assistant.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


with db() as conn:
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL, digest TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        UNIQUE(project_id,digest));
    CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
        location TEXT NOT NULL, text TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL, mode TEXT NOT NULL, input TEXT NOT NULL, output TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        source TEXT NOT NULL, items TEXT NOT NULL, mode TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        draft_id TEXT NOT NULL REFERENCES drafts(id), title TEXT NOT NULL, owner TEXT NOT NULL, due_date TEXT,
        quote TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'todo', created_at TEXT NOT NULL);
    """)

app = FastAPI(title="知行 · 项目资料问答与待办助手", version="1.0.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0
        started = False

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_BYTES + 65536:
                raise BodyTooLarge()
            return message

        async def track_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_send)
        except BodyTooLarge:
            if not started:
                await JSONResponse({"detail": "请求超过 5 MB 限制。"}, 413)(scope, receive, send)


app.add_middleware(BodyLimit)


@app.exception_handler(BodyTooLarge)
async def large_error(request, exc):
    return JSONResponse({"detail": "请求超过 5 MB 限制。"}, 413)


@app.exception_handler(sqlite3.Error)
async def database_error(request, exc):
    return JSONResponse({"detail": "数据库暂时不可写，请检查磁盘空间或稍后重试。"}, 503)


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"detail": "仅允许本机访问。"}, 403)
    if request.url.path.startswith("/api/"):
        token = os.getenv("ASSISTANT_TOKEN", "")
        if token and not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse({"detail": "访问口令不正确。"}, 401)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "不允许跨来源请求。"}, 403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-assistant-request") != "1":
            return JSONResponse({"detail": "缺少本机请求校验头。"}, 403)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > MAX_BYTES + 65536:
            return JSONResponse({"detail": "请求体过大或长度无效。"}, 413)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectIn(Strict):
    name: str = Field(min_length=1, max_length=80)


class Question(Strict):
    question: str = Field(min_length=2, max_length=1000)
    mode: Literal["evidence", "llm"] = "evidence"


class Meeting(Strict):
    text: str = Field(min_length=5, max_length=20000)
    mode: Literal["rules", "llm"] = "rules"


class TaskInput(Strict):
    title: str = Field(min_length=1, max_length=240)
    owner: str = Field(max_length=80, default="")
    due_date: date | None = None
    quote: str = Field(min_length=2, max_length=2000)


class Confirmation(Strict):
    confirmed: Literal[True]
    items: list[TaskInput] = Field(min_length=1, max_length=20)


class TaskUpdate(Strict):
    title: str = Field(min_length=1, max_length=240)
    owner: str = Field(max_length=80, default="")
    due_date: date | None = None
    status: Literal["todo", "doing", "done", "cancelled"]


class Archive(Strict):
    archived: bool


def now():
    return datetime.now(timezone.utc).isoformat()


def project_exists(conn, project_id):
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        raise HTTPException(404, "项目不存在。")


def source_chunks(conn, project_id):
    return [dict(r) for r in conn.execute("""SELECT c.*, d.name AS document_name FROM chunks c
        JOIN documents d ON d.id=c.document_id WHERE d.project_id=? AND d.archived=0 ORDER BY c.id""", (project_id,))]


def log_run(project_id, kind, mode, input_text, output, start):
    run_id = uuid.uuid4().hex
    output = {**output, "run_id": run_id, "elapsed_ms": round((time.monotonic() - start) * 1000)}
    with db() as conn:
        conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (run_id, project_id, kind, mode, input_text,
                     json.dumps(output, ensure_ascii=False), output["elapsed_ms"], now()))
    return output


@app.get("/api/health")
def health():
    return {"status": "ok", "model": model_status(), "version": "1.0.0", "storage": "SQLite · 本机持久化"}


@app.get("/api/projects")
def projects():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id")]


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectIn):
    with db() as conn:
        if conn.execute("SELECT count(*) FROM projects").fetchone()[0] >= 50:
            raise HTTPException(409, "本机演示最多 50 个项目。")
        row = conn.execute("INSERT INTO projects(name) VALUES(?)", (payload.name,))
        return {"id": row.lastrowid, "name": payload.name}


def insert_document(conn, project_id, name, raw):
    digest = hashlib.sha256(raw).hexdigest()
    previous = conn.execute("SELECT id FROM documents WHERE project_id=? AND digest=?", (project_id, digest)).fetchone()
    if previous:
        raise HTTPException(409, "相同内容已导入（包括归档资料）；可在资料列表恢复。")
    try:
        chunks = parse_file(name, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(422, "文件损坏或无法解析，请转换为 UTF-8 文本。") from None
    count = conn.execute("SELECT count(*) FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.project_id=?", (project_id,)).fetchone()[0]
    if count + len(chunks) > 3000:
        raise HTTPException(409, "每项目最多 3000 个片段，请创建新项目或缩小资料范围。")
    doc_id = conn.execute("INSERT INTO documents(project_id,name,digest,created_at) VALUES(?,?,?,?)",
                          (project_id, name, digest, now())).lastrowid
    conn.executemany("INSERT INTO chunks(document_id,location,text) VALUES(?,?,?)",
                     [(doc_id, c["location"], c["text"]) for c in chunks])
    return {"id": doc_id, "name": name, "chunk_count": len(chunks)}


@app.post("/api/projects/{project_id}/documents", status_code=201)
def upload(project_id: int, file: UploadFile = File(...)):
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "文件名无效或超过 160 字符。")
    raw = file.file.read(MAX_BYTES + 1)
    with db() as conn:
        project_exists(conn, project_id)
        return insert_document(conn, project_id, name, raw)


@app.post("/api/demo", status_code=201)
def demo():
    with db() as conn:
        previous = conn.execute("SELECT id FROM projects WHERE name='青禾社区调研 · 示例'").fetchone()
        if previous:
            return {"id": previous[0], "existing": True}
        if conn.execute("SELECT count(*) FROM projects").fetchone()[0] >= 50:
            raise HTTPException(409, "项目数量已达上限。")
        project_id = conn.execute("INSERT INTO projects(name) VALUES('青禾社区调研 · 示例')").lastrowid
        for path in sorted((ROOT / "examples").glob("*.md")):
            insert_document(conn, project_id, path.name, path.read_bytes())
        return {"id": project_id, "existing": False}


@app.get("/api/projects/{project_id}/documents")
def documents(project_id: int):
    with db() as conn:
        project_exists(conn, project_id)
        return [dict(r) for r in conn.execute("""SELECT d.id,d.name,d.archived,d.created_at,count(c.id) AS chunk_count
            FROM documents d LEFT JOIN chunks c ON d.id=c.document_id WHERE d.project_id=? GROUP BY d.id ORDER BY d.id DESC""", (project_id,))]


@app.get("/api/projects/{project_id}/documents/{document_id}")
def document(project_id: int, document_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=? AND project_id=?", (document_id, project_id)).fetchone()
        if not row:
            raise HTTPException(404, "资料不存在。")
        return {**dict(row), "chunks": [dict(c) for c in conn.execute("SELECT * FROM chunks WHERE document_id=? ORDER BY id", (document_id,))]}


@app.patch("/api/projects/{project_id}/documents/{document_id}")
def archive(project_id: int, document_id: int, payload: Archive):
    with db() as conn:
        if not conn.execute("UPDATE documents SET archived=? WHERE id=? AND project_id=?", (payload.archived, document_id, project_id)).rowcount:
            raise HTTPException(404, "资料不存在。")
    return {"ok": True}


@app.post("/api/projects/{project_id}/ask")
def ask(project_id: int, payload: Question):
    start = time.monotonic()
    with db() as conn:
        project_exists(conn, project_id)
        sources = retrieve(payload.question, source_chunks(conn, project_id))
    output = {"mode": payload.mode, "abstained": True, "answer": "资料不足，无法据此作答。请补充资料或换用资料中的关键词。", "citations": [], "candidates": sources}
    try:
        if payload.mode == "llm":
            if not model_status()["ready"]:
                raise HTTPException(503, model_status()["message"])
            if sources:
                result, meta = generate(QA_PROMPT, {"question": payload.question, "sources": sources})
                output["provider"] = meta
                claims = result.get("claims")
                valid = result.get("abstained") is False and isinstance(claims, list) and 1 <= len(claims) <= 5
                citations = []
                for claim in claims if valid else []:
                    source = next((s for s in sources if isinstance(claim, dict) and s["id"] == claim.get("source_id")), None)
                    quote = claim.get("quote") if isinstance(claim, dict) else None
                    text = claim.get("text") if isinstance(claim, dict) else None
                    if not source or not isinstance(quote, str) or len(quote.strip()) < 4 or quote not in source["text"] or not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500:
                        valid = False
                        break
                    citations.append({**source, "quote": quote, "claim": text})
                if valid:
                    output.update(abstained=False, answer="\n\n".join(f"{c['claim']} [{i + 1}]" for i, c in enumerate(citations)), citations=citations)
                elif result.get("abstained") is not True:
                    output["answer"] = "模型输出未通过引用校验，已拒绝展示未经验证的回答。请查看候选原文。"
        elif sources:
            output.update(abstained=False, answer="检索到以下相关原文，尚未判断其是否完整回答问题；这不是大模型生成的答案。",
                          citations=[{**s, "quote": s["text"]} for s in sources])
    except HTTPException as exc:
        log_run(project_id, "question", payload.mode, payload.question, {**output, "error": exc.detail, "status": exc.status_code}, start)
        raise
    return log_run(project_id, "question", payload.mode, payload.question, output, start)


@app.post("/api/projects/{project_id}/drafts")
def extract(project_id: int, payload: Meeting):
    start = time.monotonic()
    with db() as conn:
        project_exists(conn, project_id)
    try:
        meta = {}
        if payload.mode == "llm":
            result, meta = generate(TASK_PROMPT, {"meeting": payload.text})
            rows = result.get("items")
        else:
            rows = rule_tasks(payload.text)
        if not isinstance(rows, list) or len(rows) > 20:
            raise HTTPException(422, "待办输出无效或超过 20 项。请拆分会议记录。")
        try:
            items = [TaskInput.model_validate(row).model_dump(mode="json") for row in rows]
        except ValidationError:
            raise HTTPException(422, "提取的字段或日期无效，请修正原文后重试。") from None
        if any(i["quote"] not in payload.text or (i["owner"] and i["owner"] not in i["quote"])
               or (i["due_date"] and i["due_date"] not in i["quote"]) for i in items):
            raise HTTPException(422, "提取结果的原文、负责人或日期无法核对，未创建草稿。")
        draft_id = uuid.uuid4().hex
        with db() as conn:
            conn.execute("INSERT INTO drafts(id,project_id,source,items,mode) VALUES(?,?,?,?,?)",
                         (draft_id, project_id, payload.text, json.dumps(items, ensure_ascii=False), payload.mode))
        output = {"id": draft_id, "items": items, "mode": payload.mode, "provider": meta, "confirmed": False}
    except HTTPException as exc:
        log_run(project_id, "extraction", payload.mode, payload.text, {"error": exc.detail, "status": exc.status_code}, start)
        raise
    return log_run(project_id, "extraction", payload.mode, payload.text, output, start)


@app.get("/api/projects/{project_id}/drafts")
def drafts(project_id: int):
    with db() as conn:
        project_exists(conn, project_id)
        return [{**dict(r), "items": json.loads(r["items"])} for r in conn.execute(
            "SELECT * FROM drafts WHERE project_id=? AND confirmed=0 ORDER BY rowid DESC LIMIT 20", (project_id,))]


@app.post("/api/projects/{project_id}/drafts/{draft_id}/confirm")
def confirm(project_id: int, draft_id: str, payload: Confirmation):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM drafts WHERE id=? AND project_id=?", (draft_id, project_id)).fetchone()
        if not row:
            raise HTTPException(404, "草稿不存在。")
        if row["confirmed"]:
            raise HTTPException(409, "草稿已确认，不能重复生成待办。")
        original_quotes = {i["quote"] for i in json.loads(row["items"])}
        if any(i.quote not in original_quotes for i in payload.items):
            raise HTTPException(422, "原文引用不能更换；标题、负责人和日期可以人工修正。")
        for item in payload.items:
            conn.execute("INSERT INTO tasks(project_id,draft_id,title,owner,due_date,quote,created_at) VALUES(?,?,?,?,?,?,?)",
                         (project_id, draft_id, item.title, item.owner, item.due_date.isoformat() if item.due_date else None, item.quote, now()))
        conn.execute("UPDATE drafts SET confirmed=1 WHERE id=?", (draft_id,))
    return {"created": len(payload.items)}


@app.get("/api/projects/{project_id}/tasks")
def tasks(project_id: int):
    with db() as conn:
        project_exists(conn, project_id)
        return [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id DESC", (project_id,))]


@app.patch("/api/projects/{project_id}/tasks/{task_id}")
def update_task(project_id: int, task_id: int, payload: TaskUpdate):
    with db() as conn:
        if not conn.execute("UPDATE tasks SET title=?,owner=?,due_date=?,status=? WHERE id=? AND project_id=?",
                            (payload.title, payload.owner, payload.due_date.isoformat() if payload.due_date else None,
                             payload.status, task_id, project_id)).rowcount:
            raise HTTPException(404, "待办不存在。")
    return {"ok": True}


@app.get("/api/projects/{project_id}/runs")
def runs(project_id: int):
    with db() as conn:
        project_exists(conn, project_id)
        return [{**dict(r), "output": json.loads(r["output"])} for r in conn.execute(
            "SELECT * FROM runs WHERE project_id=? ORDER BY rowid DESC LIMIT 100", (project_id,))]


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
