# FastAPI 서버 메인 파일
# 이 파일을 실행하면 백엔드 서버가 켜집니다

import asyncio
import os
import json
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query, Body, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware  # CORS 설정용
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Literal
from datetime import datetime, timedelta

from sqlalchemy import text

from database import engine, get_db, Base
from models import Document, SearchLog
from contextlib import asynccontextmanager
from core.parser import parse_pdf                   # NEVER MODIFY — import only
from core.docling_parser import parse_document      # 신규 멀티포맷 파서

# 업로드된 파일을 저장할 폴더 경로
_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.environ.get("UPLOAD_DIR", os.path.join(_BASE_DIR, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 파싱 결과 JSON을 저장할 폴더 경로 (문서 상세 보기에서 사용)
PARSED_DIR  = os.path.join(UPLOAD_DIR, "parsed")
os.makedirs(PARSED_DIR, exist_ok=True)

# 지원 파일 형식 목록
SUPPORTED_EXTENSIONS = {
    '.pdf', '.docx', '.pptx', '.ppt', '.xlsx', '.xls',
    '.hwp', '.hwpx', '.txt', '.md', '.png', '.jpg', '.jpeg',
}

# 파일 크기 상한 (200MB — Docling이 대용량도 페이지 단위로 처리)
MAX_UPLOAD_SIZE = 200 * 1024 * 1024


@asynccontextmanager
async def lifespan(_):
    """서버 시작 시 DB 테이블 생성 + 스키마 마이그레이션."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrations = [
            "ALTER TABLE documents ADD COLUMN file_type VARCHAR",
            "ALTER TABLE documents ADD COLUMN original_path VARCHAR",
            "ALTER TABLE documents ADD COLUMN title VARCHAR",
            "ALTER TABLE documents ADD COLUMN memo TEXT",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # 이미 존재하는 컬럼 → 정상적으로 무시
    yield


# FastAPI 앱 객체를 만듭니다
app = FastAPI(lifespan=lifespan)

# CORS 설정: 프론트엔드(localhost:5173)에서 백엔드로 요청을 보낼 수 있게 허용합니다
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite 개발 서버 주소
    allow_methods=["*"],
    allow_headers=["*"],
)

import re as _re
_TITLE_SKIP = _re.compile(
    r'^[\d\s\-_./·•·]+$'
    r'|^(목차|목\s*차|차례|내용|목록|개요|서론|결론|참고|첨부|주의사항|주의|INDEX|CONTENTS|Table\s+of\s+Contents)$',
    _re.IGNORECASE,
)

def _extract_title(pages: list) -> str:
    """파싱된 페이지 목록에서 문서 제목을 추출합니다."""
    for page in pages[:3]:
        for line in page.get("text", "").splitlines():
            line = line.strip()
            if 4 <= len(line) <= 120 and not _TITLE_SKIP.match(line):
                return line
    return ""


# 한국어 조사 제거 — 검색 토큰 확장에 사용 ("모터를" → "모터", "설계에서" → "설계")
_JOSA = _re.compile(
    r'(으로부터|로부터|에게서|한테서|이라도|이라고|이지만|이면서|라도|라고|지만|면서|에게|까지|부터|한테|에서|으로|을|를|은|는|의|에|로|과|와|도|만|이|가)$'
)

def _stem_ko(word: str) -> str:
    """단순 규칙 기반 한국어 조사 제거 (최대 2회 반복, 복합 조사 처리)."""
    for _ in range(2):
        new = _JOSA.sub('', word)
        if new == word:
            break
        word = new
    return word


# ── 임베딩 / BM25 / AI 설정 ──────────────────────────────────────────────────
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:4b")
_embed_model  = None

def _get_embed_model():
    """paraphrase-multilingual-MiniLM-L12-v2 지연 로딩 (한국어 ↔ 영어 교차 검색 지원)."""
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            print("[embed] MiniLM 로딩 중 (첫 실행 시 ~470MB 다운로드)...")
            _embed_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            print("[embed] 로딩 완료")
        except Exception as e:
            print(f"[embed] 로드 실패: {e}")
    return _embed_model


def _best_page_for_tokens(pages: list, tokens: list) -> int:
    """토큰이 가장 많이 등장하는 페이지 번호를 반환합니다."""
    if not pages or not tokens:
        return 0
    best_page, best_score = 0, 0
    for page in pages:
        score = sum(page.get("text", "").lower().count(t) for t in tokens)
        if score > best_score:
            best_score = score
            best_page = page.get("page_num") or page.get("page", 0)
    return best_page


def _tokenize_bm25(text: str) -> list:
    """BM25 코퍼스/쿼리 토크나이저: 구두점 분리 + 조사 제거 확장."""
    import re
    raw = [t.lower() for t in re.split(r'[\s,;:.!?()\[\]{}/"\'<>=]+', text) if len(t) >= 2]
    result = []
    for t in raw:
        result.append(t)
        s = _stem_ko(t)
        if s != t and len(s) >= 2:
            result.append(s)
    return result


def _build_chunks(pages: list, max_len: int = 400) -> tuple:
    """페이지 텍스트를 청크로 분할. (chunks, pnums) 반환."""
    chunks, pnums = [], []
    for page in pages:
        text = page.get("text", "").strip()
        if not text:
            continue
        pnum = page.get("page_num") or page.get("page", 0)
        step = max_len - 50
        for i in range(0, len(text), step):
            c = text[i:i + max_len].strip()
            if len(c) >= 20:
                chunks.append(c)
                pnums.append(pnum)
    return chunks, pnums


def _generate_embeddings_sync(doc_id: int, pages: list):
    """임베딩 생성 + 디스크 저장 (동기 — asyncio.to_thread로 호출)."""
    model = _get_embed_model()
    if model is None:
        return
    chunks, pnums = _build_chunks(pages)
    if not chunks:
        return
    try:
        embs = model.encode(
            chunks, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        path = os.path.join(PARSED_DIR, f"{doc_id}_emb.npz")
        np.savez_compressed(
            path,
            embeddings=embs.astype(np.float32),
            chunks=np.array(chunks, dtype=object),
            pnums=np.array(pnums, dtype=np.int32),
        )
        print(f"[embed] doc_id={doc_id} 완료 ({len(chunks)}청크)")
    except Exception as e:
        print(f"[embed] doc_id={doc_id} 실패: {e}")


async def _embed_background(doc_id: int, pages: list):
    """백그라운드 임베딩 생성 (async wrapper)."""
    await asyncio.to_thread(_generate_embeddings_sync, doc_id, pages)


# "/" 주소로 접속하면 이 함수가 실행됩니다
@app.get("/")
def 서버_확인():
    return {"message": "서버 켜졌어요"}

# GET /upload/check?filename=... — 같은 파일명이 이미 있는지 확인합니다
@app.get("/upload/check")
async def 중복_확인(filename: str = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.filename == filename))
    existing = result.scalar_one_or_none()
    if existing:
        return {
            "exists": True,
            "uploaded_at": str(existing.uploaded_at),
            "status": existing.status,
        }
    return {"exists": False}


# POST /upload — 파일을 받아 파싱하고 DB에 기록합니다
# category:      문서 분류 (spec/research/presentation/report)
# original_path: 폴더 업로드 시 원본 상대 경로 (예: 프로젝트A/자료/파일.pdf)
#                프론트에서 file.webkitRelativePath 를 보내면 됩니다
@app.post("/upload")
async def 파일_업로드(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: Literal["spec", "research", "presentation", "report"] = Form("report"),
    original_path: str = Form(None),   # 폴더 업로드 시 상대 경로 (없으면 None)
    db: AsyncSession = Depends(get_db),
):
    # ── 검사 1: 파일이 아예 안 온 경우 ──────────────────────────────────
    if not file or not file.filename:
        return {"source_file": "", "status": "failed",
                "error": "파일이 전송되지 않았습니다", "pages": []}

    # ── 검사 2: 지원하지 않는 형식 차단 ─────────────────────────────────
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        return {"source_file": file.filename, "status": "failed",
                "error": f"지원하지 않는 형식입니다 ({ext}). 지원 형식: {supported}", "pages": []}

    # ── 검사 3: 파일 크기 제한 (200MB) ──────────────────────────────────
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        mb = round(len(contents) / (1024 * 1024), 1)
        return {"source_file": file.filename, "status": "failed",
                "error": f"파일 크기 초과 ({mb}MB). 최대 200MB까지 지원합니다", "pages": []}

    # ── 저장 경로 결정 — original_path 있으면 디렉토리 구조 보존 ─────────
    # 경로 순회 공격 방지: '..' './' 절대경로 등 제거
    if original_path:
        from pathlib import PurePosixPath
        safe_parts = [
            p for p in PurePosixPath(original_path).parts
            if p not in ('..', '.', '/', '\\')
        ]
        safe_relative = os.path.join(*safe_parts) if safe_parts else file.filename
    else:
        safe_relative = file.filename

    save_path = os.path.join(UPLOAD_DIR, safe_relative)
    save_dir  = os.path.dirname(save_path)
    os.makedirs(save_dir, exist_ok=True)

    try:
        with open(save_path, "wb") as f:
            f.write(contents)
    except Exception:
        return {"source_file": file.filename, "status": "failed",
                "error": "파일 저장 중 오류가 발생했습니다", "pages": []}

    # ── 파서 호출 ────────────────────────────────────────────────────────
    # PDF: core/parser.py parse_pdf 사용 (PyMuPDF + EasyOCR, 기존 파이프라인)
    # 그 외: core/docling_parser.py parse_document 사용 (Docling 설치 시 자동 전환)
    file_type = ext.lstrip('.')
    if ext == '.pdf':
        result = parse_pdf(filepath=save_path, source_file=file.filename)
    else:
        result = parse_document(filepath=save_path, source_file=file.filename, file_type=file_type)

    # ── DB에 문서 정보 저장 ──────────────────────────────────────────────
    has_flagged = any(p.get("flagged") for p in result.get("pages", []))

    doc = Document(
        filename      = file.filename,
        title         = _extract_title(result.get("pages", [])) if result.get("status") == "success" else None,
        saved_path    = save_path,
        file_type     = file_type,
        original_path = original_path,
        category      = category,
        status        = result.get("status"),
        error         = result.get("error"),
        page_count    = len(result.get("pages", [])),
        has_flagged   = has_flagged,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)  # auto-increment id 가져오기

    # 파싱 결과를 JSON 파일로 저장합니다 — 상세 보기에서 페이지 텍스트를 읽어옵니다
    parsed_path = os.path.join(PARSED_DIR, f"{doc.id}.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 임베딩 생성 (응답 후 백그라운드에서 실행 — 검색 품질 향상)
    if result.get("status") == "success":
        background_tasks.add_task(_embed_background, doc.id, result.get("pages", []))

    return result


# GET /files/{doc_id} — 원본 파일을 다운로드합니다
@app.get("/files/{doc_id}")
async def 파일_다운로드(doc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    if not os.path.exists(doc.saved_path):
        raise HTTPException(status_code=404, detail="파일이 서버에 없습니다")

    return FileResponse(
        path=doc.saved_path,
        filename=doc.filename,
        media_type="application/octet-stream",
    )


# DELETE /admin/documents/{id} — 문서를 DB와 디스크에서 삭제합니다
# {id} 는 삭제할 문서의 고유 번호입니다 (문서 목록에서 확인 가능)
@app.delete("/admin/documents/{doc_id}")
async def 문서_삭제(doc_id: int, db: AsyncSession = Depends(get_db)):
    # DB에서 해당 ID의 문서를 찾습니다
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    # ID에 해당하는 문서가 없으면 404 에러를 돌려줍니다
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    filename = doc.filename

    # 디스크에 저장된 실제 파일도 삭제합니다
    if os.path.exists(doc.saved_path):
        os.remove(doc.saved_path)

    # 파싱 결과 JSON도 삭제합니다
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    if os.path.exists(parsed_path):
        os.remove(parsed_path)

    # DB에서도 삭제합니다
    await db.delete(doc)
    await db.commit()

    return {"message": f"'{filename}' 문서가 삭제됐습니다", "id": doc_id}


# GET /admin/flagged — OCR 신뢰도가 낮은 문서 목록만 돌려줍니다
# OCR 신뢰도란? 스캔 PDF를 글자로 변환할 때 얼마나 정확한지 점수(0~1)
# 0.7 미만이면 "저신뢰" = 사람이 직접 확인해야 할 문서
@app.get("/admin/flagged")
async def 저신뢰_문서_목록(db: AsyncSession = Depends(get_db)):
    # has_flagged 가 True 인 문서만 최신순으로 가져옵니다
    result = await db.execute(
        select(Document)
        .where(Document.has_flagged == True)
        .order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()

    return [
        {
            "id":          doc.id,
            "filename":    doc.filename,
            "status":      doc.status,
            "page_count":  doc.page_count,
            "uploaded_at": str(doc.uploaded_at),
        }
        for doc in docs
    ]


# GET /admin/documents — 업로드된 문서 전체 목록을 돌려줍니다
# 관리자 화면에서 이 주소를 호출하면 문서 리스트가 나옵니다
@app.get("/admin/documents")
async def 문서_목록(db: AsyncSession = Depends(get_db)):
    # DB에서 모든 문서를 업로드 시각 최신순으로 가져옵니다
    result = await db.execute(
        select(Document).order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()

    # 각 문서 정보를 딕셔너리 형태로 변환해서 돌려줍니다
    return [
        {
            "id":            doc.id,
            "filename":      doc.filename,
            "title":         doc.title,
            "memo":          doc.memo,
            "file_type":     doc.file_type,
            "original_path": doc.original_path,
            "category":      doc.category,
            "status":        doc.status,
            "error":         doc.error,
            "page_count":    doc.page_count,
            "has_flagged":   doc.has_flagged,
            "uploaded_at":   str(doc.uploaded_at),
        }
        for doc in docs
    ]


# POST /admin/documents/{doc_id}/retry — 파싱 실패 문서를 다시 파싱합니다
@app.post("/admin/documents/{doc_id}/retry")
async def 문서_재시도(doc_id: int, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    # 원본 파일이 디스크에 없으면 재시도 불가
    if not os.path.exists(doc.saved_path):
        raise HTTPException(status_code=400, detail="원본 파일이 없어서 재시도할 수 없습니다")

    # 파서 재호출 — PDF는 parse_pdf, 나머지는 parse_document
    ext_retry    = os.path.splitext(doc.saved_path)[1].lower()
    file_type    = doc.file_type or ext_retry.lstrip('.')
    if ext_retry == '.pdf':
        parse_result = parse_pdf(filepath=doc.saved_path, source_file=doc.filename)
    else:
        parse_result = parse_document(filepath=doc.saved_path, source_file=doc.filename, file_type=file_type)

    # DB 업데이트
    has_flagged = any(p.get("flagged") for p in parse_result.get("pages", []))
    doc.status     = parse_result.get("status")
    doc.error      = parse_result.get("error")
    doc.page_count = len(parse_result.get("pages", []))
    doc.has_flagged = has_flagged
    if parse_result.get("status") == "success":
        doc.title = _extract_title(parse_result.get("pages", []))
    await db.commit()
    await db.refresh(doc)

    # 파싱 결과 JSON 저장 (상세 보기용)
    parsed_path = os.path.join(PARSED_DIR, f"{doc.id}.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(parse_result, f, ensure_ascii=False, indent=2)

    # 임베딩 재생성
    if parse_result.get("status") == "success":
        background_tasks.add_task(_embed_background, doc.id, parse_result.get("pages", []))

    return {
        "id":          doc.id,
        "filename":    doc.filename,
        "status":      doc.status,
        "page_count":  doc.page_count,
        "has_flagged": doc.has_flagged,
        "error":       doc.error,
    }


# GET /admin/documents/{doc_id}/detail — 문서 상세 보기 (파싱된 페이지 텍스트 포함)
@app.get("/admin/documents/{doc_id}/detail")
async def 문서_상세(doc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    # 저장된 파싱 결과 JSON을 읽어옵니다
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    pages = []
    if os.path.exists(parsed_path):
        with open(parsed_path, "r", encoding="utf-8") as f:
            parse_data = json.load(f)
        pages = parse_data.get("pages", [])

    return {
        "id":            doc.id,
        "filename":      doc.filename,
        "title":         doc.title,
        "memo":          doc.memo,
        "file_type":     doc.file_type,
        "original_path": doc.original_path,
        "category":      doc.category,
        "status":        doc.status,
        "error":         doc.error,
        "page_count":    doc.page_count,
        "has_flagged":   doc.has_flagged,
        "uploaded_at":   str(doc.uploaded_at),
        "pages":         pages,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 검색 엔진 교체 지점 (김태훈 ③)
#
# 아래 함수 `실제_검색_실행` 하나만 교체하면 됩니다.
# 입력:  q (검색어), alpha (의미검색 비중 0~1), category (카테고리 or None), db (DB 세션)
# 출력:  아래 형태의 딕셔너리 리스트
#   [
#     {
#       "doc_id":   int,    # DB의 Document.id
#       "filename": str,    # 파일명
#       "category": str,    # spec / research / presentation / report
#       "pages":    int,    # 페이지 수
#       "snippet":  str,    # 검색 결과 미리보기 텍스트
#       "score":    float,  # 관련도 점수 (0~1)
#     },
#     ...
#   ]
# ──────────────────────────────────────────────────────────────────────────────
def _extract_snippet_multi(text: str, tokens: list, max_len: int = 200) -> str:
    """여러 토큰이 가장 밀집된 위치에서 스니펫을 추출합니다."""
    if not tokens or not text.strip():
        stripped = text.strip()
        return stripped[:max_len] + ("…" if len(stripped) > max_len else "")
    lower = text.lower()
    best_pos, best_score = 0, 0
    for t in tokens:
        pos = 0
        while True:
            idx = lower.find(t, pos)
            if idx == -1:
                break
            start = max(0, idx - 60)
            end   = min(len(text), idx + max_len - 60)
            score = sum(lower[start:end].count(tok) for tok in tokens)
            if score > best_score:
                best_score, best_pos = score, idx
            pos = idx + 1
    start   = max(0, best_pos - 60)
    end     = min(len(text), best_pos + max_len - 60)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet


async def 실제_검색_실행(q: str, alpha: float, category, db: AsyncSession) -> list:
    """BM25 + MiniLM 시맨틱 + RRF 하이브리드 검색.
    alpha=0.0 → BM25 전용 / alpha=1.0 → 시맨틱 전용 / alpha=0.5 → 균등 혼합
    """
    import re

    # ── 1. 문서 목록 로드 ────────────────────────────────────────────────────
    stmt = select(Document).where(Document.status == "success")
    if category:
        stmt = stmt.where(Document.category == category)
    docs = (await db.execute(stmt)).scalars().all()
    if not docs:
        return []

    # ── 2. 쿼리 토큰화 (조사 제거 확장) ─────────────────────────────────────
    raw_tok = [t.lower() for t in re.split(r"[\s\W]+", q) if len(t) >= 2]
    tokens  = list(raw_tok)
    for t in raw_tok:
        s = _stem_ko(t)
        if s != t and len(s) >= 2 and s not in tokens:
            tokens.append(s)
    if not tokens:
        return []

    # ── 3. 각 문서 본문 텍스트 로드 ─────────────────────────────────────────
    doc_info: dict = {}
    for doc in docs:
        full_text = ""
        pages_list = []
        parsed_path = os.path.join(PARSED_DIR, f"{doc.id}.json")
        if os.path.exists(parsed_path):
            with open(parsed_path, "r", encoding="utf-8") as f:
                pd = json.load(f)
            pages_list = pd.get("pages", [])
            full_text  = "\n".join(p.get("text", "") for p in pages_list)
        combined = " ".join(filter(None, [doc.filename, doc.title or "", doc.memo or "", full_text]))
        doc_info[doc.id] = {"full_text": full_text, "combined": combined, "pages": pages_list}

    # ── 4. BM25 (alpha < 1.0) — 청크 단위로 실행해 음수 점수 문제 회피 ────────
    bm25_scores: dict = {}
    if alpha < 1.0:
        try:
            from rank_bm25 import BM25Okapi
            # 청크 단위 코퍼스 구성: npz 있으면 청크, 없으면 전체 텍스트를 분할
            all_chunk_items: list = []   # (doc_id, text)
            for doc in docs:
                emb_path = os.path.join(PARSED_DIR, f"{doc.id}_emb.npz")
                if os.path.exists(emb_path):
                    try:
                        nd = np.load(emb_path, allow_pickle=True)
                        for chunk in nd["chunks"]:
                            all_chunk_items.append((doc.id, str(chunk)))
                    except Exception:
                        all_chunk_items.append((doc.id, doc_info[doc.id]["combined"]))
                else:
                    # 임베딩 없으면 400자 단위로 직접 분할
                    text = doc_info[doc.id]["combined"]
                    for i in range(0, len(text), 350):
                        seg = text[i:i + 400].strip()
                        if len(seg) >= 20:
                            all_chunk_items.append((doc.id, seg))
            if all_chunk_items:
                corpus     = [_tokenize_bm25(t) for _, t in all_chunk_items]
                bm25       = BM25Okapi(corpus)
                q_tokens   = _tokenize_bm25(q)
                raw_scores = bm25.get_scores(q_tokens)
                for i, (did, _) in enumerate(all_chunk_items):
                    s = float(raw_scores[i])
                    if s > 0 and s > bm25_scores.get(did, 0):
                        bm25_scores[did] = s
        except ImportError:
            # rank-bm25 없으면 간단한 TF 폴백
            for doc in docs:
                hits = sum(doc_info[doc.id]["combined"].lower().count(t) for t in tokens)
                if hits > 0:
                    bm25_scores[doc.id] = float(hits)

    # ── 5. 시맨틱 검색 (alpha > 0.0) ────────────────────────────────────────
    # MiniLM 다국어 모델: 한국어 쿼리 → 영어 기술문서 교차 검색 지원
    sem_scores: dict   = {}
    sem_chunks: dict   = {}   # doc_id → 가장 유사한 청크 텍스트 (스니펫용)
    sem_pages:  dict   = {}   # doc_id → 가장 유사한 청크의 페이지 번호
    if alpha > 0.0:
        try:
            model = _get_embed_model()
            if model is not None:
                q_emb = (await asyncio.to_thread(
                    model.encode, [q],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ))[0]
                for doc in docs:
                    emb_path = os.path.join(PARSED_DIR, f"{doc.id}_emb.npz")
                    if not os.path.exists(emb_path):
                        continue
                    data     = np.load(emb_path, allow_pickle=True)
                    embs     = data["embeddings"]          # [N, 384] float32 L2-정규화
                    sims     = embs @ q_emb                # 코사인 유사도
                    best_idx = int(np.argmax(sims))
                    sem_scores[doc.id] = float(sims[best_idx])
                    if "chunks" in data:
                        sem_chunks[doc.id] = str(data["chunks"][best_idx])
                    if "pnums" in data:
                        sem_pages[doc.id]  = int(data["pnums"][best_idx])
        except Exception as e:
            print(f"[search] 시맨틱 오류: {e}")

    # ── 6. 후보 선정 ─────────────────────────────────────────────────────────
    SEM_THR = 0.25   # 코사인 유사도 임계값
    candidates: set = set()
    for did, s in bm25_scores.items():
        if s > 0:
            candidates.add(did)
    for did, s in sem_scores.items():
        if s >= SEM_THR:
            candidates.add(did)

    if not candidates:
        return []

    # ── 7. RRF 융합 (Reciprocal Rank Fusion) ────────────────────────────────
    n = len(docs)
    k = 60

    bm25_rank = {}
    if bm25_scores:
        for i, did in enumerate(sorted(bm25_scores, key=bm25_scores.get, reverse=True)):
            bm25_rank[did] = i + 1

    sem_rank = {}
    if sem_scores:
        for i, did in enumerate(sorted(sem_scores, key=sem_scores.get, reverse=True)):
            sem_rank[did] = i + 1

    scored = []
    for doc in docs:
        if doc.id not in candidates:
            continue
        br  = bm25_rank.get(doc.id, n + 1)
        sr  = sem_rank.get(doc.id, n + 1)
        rrf = (1 - alpha) / (k + br) + alpha / (k + sr)

        full_text = doc_info[doc.id]["full_text"]
        # 시맨틱 매칭 청크를 우선 스니펫으로 사용 (한국어 쿼리 → 영어 문서 대응)
        if doc.id in sem_chunks and sem_scores.get(doc.id, 0) >= SEM_THR:
            snippet  = _extract_snippet_multi(sem_chunks[doc.id], tokens)
            page_num = sem_pages.get(doc.id, 0)
        else:
            snippet  = (
                _extract_snippet_multi(full_text, tokens) if full_text.strip()
                else (doc.title or doc.filename)
            )
            page_num = _best_page_for_tokens(doc_info[doc.id]["pages"], tokens)

        scored.append({
            "doc_id":      doc.id,
            "filename":    doc.filename,
            "category":    doc.category,
            "pages":       doc.page_count,
            "snippet":     snippet,
            "page_num":    page_num,
            "score":       rrf,
            "uploaded_at": str(doc.uploaded_at),
        })

    scored.sort(key=lambda r: r["score"], reverse=True)

    # 최고 점수 기준으로 0~1 정규화
    if scored:
        max_s = scored[0]["score"]
        if max_s > 0:
            for r in scored:
                r["score"] = round(r["score"] / max_s, 4)

    return scored[:50]


# GET /search — 문서 검색 엔드포인트
# 파라미터:
#   q        : 검색어 (예: "모터 설계 사양")
#   alpha    : 키워드 검색 ↔ 의미 검색 비중 (0.0 = 키워드만, 1.0 = 의미만, 0.5 = 반반)
#   category : 카테고리 필터 (없으면 전체, 있으면 해당 카테고리만)
@app.get("/search")
async def 검색(
    q: str = Query(..., description="검색어"),
    alpha: float = Query(0.5, ge=0.0, le=1.0, description="의미검색 비중 (0~1)"),
    category: str = Query(None, description="카테고리 필터 (spec/research/presentation/report)"),
    file_type: str = Query(None, description="파일 형식 필터 (pdf/docx/pptx/xlsx/hwp 등)"),
    db: AsyncSession = Depends(get_db),
):
    results = await 실제_검색_실행(q, alpha, category, db)

    # 검색 결과에 file_type / title 정보 보강
    if results:
        doc_ids = [r["doc_id"] for r in results]
        rows = (await db.execute(
            select(Document.id, Document.file_type, Document.title, Document.memo)
            .where(Document.id.in_(doc_ids))
        )).all()
        info = {row.id: row for row in rows}
        for r in results:
            meta = info.get(r["doc_id"])
            if meta:
                r["file_type"] = meta.file_type
                r["title"]     = meta.title
                r["memo"]      = meta.memo

    # 파일 형식 필터 (실제_검색_실행은 건드리지 않고 여기서 후처리)
    if file_type:
        results = [r for r in results if r.get("file_type") == file_type]

    # 검색 기록 저장
    log = SearchLog(query=q, alpha=alpha, result_count=len(results))
    db.add(log)
    await db.commit()

    return {"query": q, "alpha": alpha, "category": category, "file_type": file_type,
            "total": len(results), "results": results}


# DELETE /admin/history — 검색 기록 전체 삭제
@app.delete("/admin/history")
async def 검색_기록_초기화(db: AsyncSession = Depends(get_db)):
    await db.execute(SearchLog.__table__.delete())
    await db.commit()
    return {"message": "검색 기록이 모두 삭제됐습니다"}


# PATCH /admin/documents/{doc_id}/category — 문서 카테고리 변경
@app.patch("/admin/documents/{doc_id}/category")
async def 카테고리_변경(
    doc_id: int,
    category: Literal["spec", "research", "presentation", "report"] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    doc.category = category
    await db.commit()
    return {"id": doc_id, "category": category}


# PATCH /admin/documents/bulk-category — 여러 문서 카테고리 일괄 변경
@app.patch("/admin/documents/bulk-category")
async def 문서_일괄_카테고리_변경(
    ids: list[int] = Body(..., embed=True),
    category: Literal["spec", "research", "presentation", "report"] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id.in_(ids)))
    docs = result.scalars().all()
    for doc in docs:
        doc.category = category
    await db.commit()
    return {"updated": [doc.id for doc in docs], "count": len(docs), "category": category}


# DELETE /admin/documents/bulk — 여러 문서 일괄 삭제
@app.delete("/admin/documents/bulk")
async def 문서_일괄_삭제(ids: list[int] = Body(...), db: AsyncSession = Depends(get_db)):
    deleted = []
    for doc_id in ids:
        result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if doc:
            if os.path.exists(doc.saved_path):
                os.remove(doc.saved_path)
            parsed_path = os.path.join(PARSED_DIR, f"{doc.id}.json")
            if os.path.exists(parsed_path):
                os.remove(parsed_path)
            await db.delete(doc)
            deleted.append(doc_id)
    await db.commit()
    return {"deleted": deleted, "count": len(deleted)}


# PATCH /admin/documents/{doc_id}/memo — 문서 메모(설명) 수정
@app.patch("/admin/documents/{doc_id}/memo")
async def 메모_수정(
    doc_id: int,
    memo: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    doc.memo = memo or None
    await db.commit()
    return {"id": doc_id, "memo": doc.memo}


# PATCH /admin/documents/{doc_id}/pages/{page_num} — OCR 저신뢰 페이지 텍스트 수동 수정
@app.patch("/admin/documents/{doc_id}/pages/{page_num}")
async def 페이지_텍스트_수정(
    doc_id: int,
    page_num: int,
    text: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    if not os.path.exists(parsed_path):
        raise HTTPException(status_code=404, detail="파싱 데이터가 없습니다")

    with open(parsed_path, "r", encoding="utf-8") as f:
        parse_data = json.load(f)

    updated = False
    for page in parse_data.get("pages", []):
        if (page.get("page_num") or page.get("page")) == page_num:
            page["text"]    = text
            page["flagged"] = False   # 수정 완료 → 저신뢰 플래그 해제
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="해당 페이지를 찾을 수 없습니다")

    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(parse_data, f, ensure_ascii=False, indent=2)

    # has_flagged 재평가
    has_flagged = any(p.get("flagged") for p in parse_data.get("pages", []))
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc:
        doc.has_flagged = has_flagged
        await db.commit()

    return {"doc_id": doc_id, "page_num": page_num, "has_flagged": has_flagged}


# GET /admin/history — 검색 기록 목록을 돌려줍니다
@app.get("/admin/history")
async def 검색_기록(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SearchLog).order_by(SearchLog.searched_at.desc()).limit(100)
    )
    logs = result.scalars().all()

    return [
        {
            "id":           log.id,
            "query":        log.query,
            "alpha":        log.alpha,
            "result_count": log.result_count,
            "searched_at":  str(log.searched_at),
        }
        for log in logs
    ]


# GET /admin/stats/trend — 최근 7일 일별 업로드 건수
@app.get("/admin/stats/trend")
async def 업로드_트렌드(period: int = Query(7, ge=7, le=30), db: AsyncSession = Depends(get_db)):
    today = datetime.now().date()
    days  = [(today - timedelta(days=i)) for i in range(period - 1, -1, -1)]

    result = []
    for day in days:
        start = datetime(day.year, day.month, day.day, 0, 0, 0)
        end   = datetime(day.year, day.month, day.day, 23, 59, 59)
        count = (await db.execute(
            select(func.count()).select_from(Document)
            .where(Document.uploaded_at >= start)
            .where(Document.uploaded_at <= end)
        )).scalar()
        result.append({
            "date":  day.strftime("%m/%d"),
            "count": count,
        })
    return result


# GET /admin/stats — 관리자 대시보드 통계
@app.get("/admin/stats")
async def 통계(db: AsyncSession = Depends(get_db)):
    # 총 문서 수
    total = (await db.execute(select(func.count()).select_from(Document))).scalar()

    # 카테고리별 문서 수
    cat_rows = (await db.execute(
        select(Document.category, func.count().label("cnt"))
        .group_by(Document.category)
    )).all()
    by_category = {row.category: row.cnt for row in cat_rows}

    # OCR 검토 필요 문서 수
    flagged = (await db.execute(
        select(func.count()).select_from(Document).where(Document.has_flagged == True)
    )).scalar()

    # 실패 문서 수
    failed = (await db.execute(
        select(func.count()).select_from(Document).where(Document.status == "failed")
    )).scalar()

    # 파일 형식별 문서 수
    type_rows = (await db.execute(
        select(Document.file_type, func.count().label("cnt"))
        .where(Document.file_type != None)
        .group_by(Document.file_type)
    )).all()
    by_file_type = {row.file_type: row.cnt for row in type_rows}

    # 인기 검색어 Top 5 (검색 횟수 기준)
    top_queries = (await db.execute(
        select(SearchLog.query, func.count().label("cnt"))
        .group_by(SearchLog.query)
        .order_by(func.count().desc())
        .limit(5)
    )).all()

    # 총 검색 횟수
    total_searches = (await db.execute(
        select(func.count()).select_from(SearchLog)
    )).scalar()

    return {
        "total_documents": total,
        "by_category":    by_category,
        "by_file_type":   by_file_type,
        "flagged_count":  flagged,
        "failed_count":   failed,
        "total_searches": total_searches,
        "top_queries":    [{"query": r.query, "count": r.cnt} for r in top_queries],
    }


# PATCH /admin/documents/{doc_id}/title — 문서 제목 수동 편집
@app.patch("/admin/documents/{doc_id}/title")
async def 제목_수정(
    doc_id: int,
    title:  str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    doc.title = title.strip() or None
    await db.commit()
    return {"id": doc_id, "title": doc.title}


# ── AI 답변 (Ollama Gemma 3 4B) ─────────────────────────────────────────────

@app.get("/ask/status")
async def ai_status():
    """Ollama 서버 가용 여부 및 Gemma 모델 확인."""
    import urllib.request, json as _json
    def _check():
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            return _json.loads(r.read())
    try:
        data   = await asyncio.to_thread(_check)
        models = [m["name"] for m in data.get("models", [])]
        ok     = any("gemma" in m.lower() for m in models)
        return {"available": ok, "models": models, "target": OLLAMA_MODEL}
    except Exception:
        return {"available": False, "models": [], "target": OLLAMA_MODEL}


@app.post("/ask")
async def ai_ask(
    q:        str       = Body(..., embed=True),
    snippets: list[str] = Body(..., embed=True),
):
    """검색 결과 스니펫을 컨텍스트로 Ollama Gemma에게 질문합니다."""
    import urllib.request, json as _json
    context_text = "\n\n".join(
        f"[문서 {i+1}]\n{s.strip()}" for i, s in enumerate(snippets[:5]) if s.strip()
    )
    prompt = (
        "다음은 사내 문서에서 검색한 관련 내용입니다.\n\n"
        f"{context_text}\n\n"
        f"질문: {q}\n\n"
        "위 문서 내용을 바탕으로 질문에 간결하게 답해 주세요. "
        "문서에 없는 내용은 추측하지 말고 '문서에서 찾을 수 없습니다'라고 답하세요."
    )
    def _generate():
        payload = _json.dumps(
            {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
        ).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return _json.loads(r.read())
    try:
        data = await asyncio.to_thread(_generate)
        return {"answer": data.get("response", ""), "model": data.get("model", OLLAMA_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"AI 답변 생성 실패: {e}")
