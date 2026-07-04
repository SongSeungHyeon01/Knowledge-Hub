# FastAPI 서버 메인 파일
# 이 파일을 실행하면 백엔드 서버가 켜집니다

import asyncio
import os
import json
import hashlib
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query, Body, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware  # CORS 설정용
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from typing import Literal
from datetime import datetime, timedelta

from sqlalchemy import text

from database import engine, get_db, Base, AsyncSessionLocal
from models import Document, SearchLog, Chunk
from contextlib import asynccontextmanager
from core.parser import parse_pdf                   # 백엔드 배포 정본 파서 — 최신 parser 브랜치(parser/parser.py) 기준, import only
from core.office_adapter import parse_non_pdf       # 비-PDF 입구: ② hwp_postprocess 정본 + TXT/MD 리더

# ③ 김태훈 검색 정본 (backend/search/search.py) — 알고리즘 구성요소만 가져다 쓴다.
#   채택: 섹션 헤더 우선 청킹 / 언어감지 토크나이저 / 비대칭 RRF k / 영↔한 동의어 보강
#   저장은 설계도 C5 규칙(PostgreSQL CHUNKS PK = turbovec 벡터 ID 1:1)대로 이 파일이 담당.
#   (_로 시작하는 이름도 가져오는 이유: ③ 정본의 내부 상수·함수를 재구현 없이 그대로
#    재사용하기 위함 — 값을 바꾸지 말 것)
from search.search import (
    chunk_text as _sb_chunk_text,        # 섹션 헤더 우선 청킹 (PRD 기준)
    _augment_ko_to_en, _augment_en_to_ko,  # 영↔한 기술용어 쿼리 보강
    _RRF_K_BM25, _RRF_K_VEC,             # 비대칭 RRF k (BM25=30, 벡터=60)
)

# ── 경로 설정 — env var 우선, 없으면 코드 파일 기준 상대 경로 ─────────────────
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get("DATA_DIR", os.path.join(_BASE_DIR, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PARSED_DIR = os.path.join(DATA_DIR, "parsed")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PARSED_DIR, exist_ok=True)

# 지원 파일 형식 목록
# (png/jpg 단독 이미지는 미지원으로 정리 — 2026-07-04 결정. 문서 속 이미지는
#  PDF OCR 경로·DOCX embedded 이미지 OCR로 이미 처리된다)
SUPPORTED_EXTENSIONS = {
    '.pdf', '.docx', '.pptx', '.ppt', '.xlsx', '.xls',
    '.hwp', '.hwpx', '.txt', '.md',
}

# 파일 크기 상한 (500MB — OOM 방지 1MB 스트리밍)
MAX_UPLOAD_SIZE = 500 * 1024 * 1024


# ── turbovec 인덱스 (전역, 지연 초기화) ──────────────────────────────────────
_vec_index = None   # turbovec.IdMapIndex or None (설치 안 됐을 때)

def _get_vec_index():
    global _vec_index
    if _vec_index is None:
        try:
            import turbovec
            _vec_index = turbovec.IdMapIndex(dim=384, bit_width=4)
        except ImportError:
            pass
    return _vec_index


@asynccontextmanager
async def lifespan(_):
    """서버 시작 시 DB 테이블 생성 + 스키마 마이그레이션 + turbovec 재구성."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrations = [
            "ALTER TABLE documents ADD COLUMN file_type VARCHAR",
            "ALTER TABLE documents ADD COLUMN original_path VARCHAR",
            "ALTER TABLE documents ADD COLUMN title VARCHAR",
            "ALTER TABLE documents ADD COLUMN memo TEXT",
            "ALTER TABLE documents ADD COLUMN sha256 VARCHAR(64)",
        ]
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # 이미 존재하는 컬럼 → 정상적으로 무시

    # turbovec 인덱스 재구성 (DB CHUNKS에서 임베딩 로드)
    idx = _get_vec_index()
    if idx is not None:
        async with AsyncSessionLocal() as sess:
            rows = (await sess.execute(
                select(Chunk).where(Chunk.embedding != None)
            )).scalars().all()
            # [수정 2026-07-04] turbovec IdMapIndex에는 add()가 없고 add_with_ids()
            # (배치 전용)만 있다. 기존 idx.add(...)는 try/except에 삼켜져 조용히
            # 전부 실패했음 → 벡터·id를 모아 한 번에 배치 등록한다. (C5: id=Chunk PK)
            vecs, ids = [], []
            for row in rows:
                try:
                    vecs.append(np.frombuffer(row.embedding, dtype=np.float32))
                    ids.append(int(row.id))
                except Exception:
                    pass
            count = 0
            if vecs:
                try:
                    idx.add_with_ids(np.vstack(vecs), np.array(ids, dtype=np.uint64))
                    count = len(ids)
                except Exception as e:
                    print(f"[startup] turbovec 재구성 실패: {e}")
        print(f"[startup] turbovec 재구성 완료: {count}개 벡터")

    yield


# FastAPI 앱 객체를 만듭니다
app = FastAPI(lifespan=lifespan)

# CORS 설정: 환경변수로 allowed origins 확장 (기본값: localhost:5173)
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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


# ── ③ 정본 BM25 토크나이저 (지연 로딩) ──────────────────────────────────────
# BM25Indexer 인스턴스를 토크나이저로만 사용한다 (색인 저장 기능은 쓰지 않음 —
# 저장은 DB CHUNKS가 담당). 언어 감지 + 영어 불용어 + 기술 토큰(1.8V, 0x60) 지원.
_search_indexer = None

def _get_search_indexer():
    global _search_indexer
    if _search_indexer is None:
        try:
            from search.search import BM25Indexer
            _search_indexer = BM25Indexer()   # 내부에서 Kiwi 로드 (첫 호출만 느림)
        except Exception as e:
            print(f"[search] ③ 토크나이저 로드 실패: {e}")
    return _search_indexer


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


def _tokenize_bm25(text: str, is_query: bool = False) -> list:
    """BM25 토크나이저 — ③ 정본(BM25Indexer)에 위임. 실패 시 regex fallback.
    is_query=True면 쿼리 전용 토크나이저(한/영 동시 추출)를 쓴다."""
    indexer = _get_search_indexer()
    if indexer is not None:
        try:
            if is_query:
                return indexer._tokenize_query(text)
            return indexer._tokenize(text)
        except Exception:
            pass
    # regex fallback (③ 토크나이저 로드 실패 시에만)
    import re
    raw = [t.lower() for t in re.split(r'[\s,;:.!?()\[\]{}/"\'<>=]+', text) if len(t) >= 2]
    result = []
    for t in raw:
        result.append(t)
        s = _stem_ko(t)
        if s != t and len(s) >= 2:
            result.append(s)
    return result


def _build_chunks(pages: list) -> tuple:
    """페이지 텍스트를 ③ 정본 청킹(섹션 헤더 우선, 400자/80자 오버랩)으로 분할.
    (chunks, pnums) 반환.

    - flagged 페이지(저신뢰 OCR)는 인덱싱에서 제외한다 — ③ 정본 정책.
      관리자가 /admin/flagged에서 텍스트를 수정하면 flagged가 풀려 재인덱싱 대상이 된다.
    - ③ chunk_text는 문서 전체를 이어붙여 청킹하므로(섹션이 페이지에 걸칠 수 있음),
      각 페이지의 시작 위치(offset)를 기록해 두었다가 청크의 char_start로
      "이 청크가 몇 페이지에서 시작했는지"를 역산한다 (검색 결과 출처 표시용).
    """
    import bisect

    page_texts = []
    offsets    = []   # (이어붙인 문자열에서의 시작 위치, 페이지 번호)
    pos = 0
    for page in pages:
        if page.get("flagged"):
            continue   # 저신뢰 페이지 제외 (오인식 텍스트가 검색 품질을 떨어뜨림)
        text = (page.get("text") or "").strip()
        if not text:
            continue
        pnum = page.get("page_num") or page.get("page", 0)
        offsets.append((pos, pnum))
        page_texts.append(text)
        pos += len(text) + 1   # 아래 "\n".join의 개행 1자 보정

    if not page_texts:
        return [], []

    joined = "\n".join(page_texts)
    raw_chunks = _sb_chunk_text("doc", joined)   # doc_id는 chunk_id 생성용 — 여기선 미사용

    chunks, pnums = [], []
    starts = [off for off, _ in offsets]
    for c in raw_chunks:
        if len(c["text"]) < 20:   # 지나치게 짧은 조각은 색인 가치 없음 (기존 기준 유지)
            continue
        i = bisect.bisect_right(starts, c["char_start"]) - 1
        pnums.append(offsets[max(i, 0)][1])
        chunks.append(c["text"])
    return chunks, pnums


def _generate_embeddings_sync(doc_id: int, pages: list):
    """임베딩 생성 + 디스크 저장 (동기 — asyncio.to_thread로 호출). 호환성 유지용."""
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
    """백그라운드 임베딩 생성 (async wrapper). 호환성 유지용."""
    await asyncio.to_thread(_generate_embeddings_sync, doc_id, pages)


async def _ingest_chunks_to_db(doc_id: int, pages: list):
    """청크 분할 → MiniLM 임베딩 → CHUNKS 테이블 저장 → turbovec 색인."""
    model = _get_embed_model()
    chunks, pnums = _build_chunks(pages)
    if not chunks:
        return

    # 임베딩 생성
    if model is not None:
        embs = await asyncio.to_thread(
            model.encode, chunks,
            batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        embs = embs.astype(np.float32)
    else:
        embs = None

    # npz 저장 (BM25 fallback용 — 기존 호환성)
    if embs is not None:
        npz_path = os.path.join(PARSED_DIR, f"{doc_id}_emb.npz")
        np.savez_compressed(npz_path,
            embeddings=embs,
            chunks=np.array(chunks, dtype=object),
            pnums=np.array(pnums, dtype=np.int32))

    # DB에 Chunk 저장 + turbovec 색인
    idx = _get_vec_index()
    async with AsyncSessionLocal() as sess:
        # 기존 청크 삭제 (재시도 시) — C5 규칙: DB에서 지우는 청크는 turbovec에서도
        # 함께 제거해야 한다. 안 그러면 삭제된 id의 "유령 벡터"가 인덱스에 남아
        # 메모리를 차지하고, 검색 상위 k 자리를 뺏어 실제 결과를 밀어낸다.
        old_ids = (await sess.execute(
            select(Chunk.id).where(Chunk.doc_id == doc_id)
        )).scalars().all()
        if idx is not None:
            for cid in old_ids:
                try:
                    idx.remove(int(cid))
                except Exception:
                    pass
        await sess.execute(
            sa_delete(Chunk).where(Chunk.doc_id == doc_id)
        )
        for i, (text, pnum) in enumerate(zip(chunks, pnums)):
            emb_bytes = embs[i].tobytes() if embs is not None else None
            chunk_row = Chunk(doc_id=doc_id, text=text, page_num=pnum, chunk_idx=i, embedding=emb_bytes)
            sess.add(chunk_row)
        await sess.flush()  # IDs 확보
        await sess.commit()
        # turbovec에 추가 — add()는 없는 API. add_with_ids() 배치 등록 (C5: id=Chunk PK)
        if idx is not None and embs is not None:
            refreshed = (await sess.execute(
                select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.chunk_idx)
            )).scalars().all()
            new_ids = np.array([int(row.id) for row in refreshed], dtype=np.uint64)
            if len(new_ids):
                try:
                    idx.add_with_ids(embs[:len(new_ids)], new_ids)
                except Exception as e:
                    print(f"[ingest] turbovec 색인 실패: {e}")
    print(f"[ingest] doc_id={doc_id}: {len(chunks)}청크 저장 완료")


# "/" 주소로 접속하면 이 함수가 실행됩니다.
# 배포(통합 서빙) 시: 프론트 빌드 산출물(backend/static/index.html)이 있으면 화면을 돌려주고,
# 로컬 개발처럼 static이 없으면 기존 확인 메시지를 돌려줍니다.
_STATIC_DIR = os.path.join(_BASE_DIR, "static")

@app.get("/")
def 서버_확인():
    index_html = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index_html):
        return FileResponse(index_html, media_type="text/html")
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

    # ── 검사 3: 파일 크기 제한 — 1MB 스트리밍 (OOM 방지, 최대 500MB) ──────
    hasher = hashlib.sha256()
    chunks_data = []
    total = 0
    while True:
        chunk_bytes = await file.read(1024 * 1024)
        if not chunk_bytes:
            break
        total += len(chunk_bytes)
        if total > MAX_UPLOAD_SIZE:
            mb = round(total / (1024 * 1024), 1)
            return {"source_file": file.filename, "status": "failed",
                    "error": f"파일 크기 초과 ({mb}MB). 최대 {MAX_UPLOAD_SIZE // (1024*1024)}MB까지 지원합니다", "pages": []}
        hasher.update(chunk_bytes)
        chunks_data.append(chunk_bytes)
    sha256_hex = hasher.hexdigest()
    contents = b"".join(chunks_data)

    # ── SHA-256 중복 체크 — 이미 같은 내용의 파일이 있으면 409 ──────────
    dup = (await db.execute(select(Document).where(Document.sha256 == sha256_hex))).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"동일한 파일이 이미 존재합니다 (id={dup.id}, '{dup.filename}')")

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
    # PDF: core/parser.py parse_pdf 사용 (pdfplumber + camelot + EasyOCR 파이프라인)
    # 그 외: ② hwp_postprocess 정본 (docx/pptx/xlsx/hwp/hwpx) + TXT/MD 리더
    file_type = ext.lstrip('.')
    if ext == '.pdf':
        result = parse_pdf(filepath=save_path, source_file=file.filename)
    else:
        result = parse_non_pdf(filepath=save_path, source_file=file.filename, ext=ext)

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
        sha256        = sha256_hex,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)  # auto-increment id 가져오기

    # 파싱 결과를 JSON 파일로 저장합니다 — 상세 보기에서 페이지 텍스트를 읽어옵니다
    parsed_path = os.path.join(PARSED_DIR, f"{doc.id}.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 임베딩 생성 + Chunk DB 저장 (응답 후 백그라운드에서 실행)
    if result.get("status") == "success":
        background_tasks.add_task(_ingest_chunks_to_db, doc.id, result.get("pages", []))

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

    # Chunks 삭제 + turbovec 제거
    chunk_ids = (await db.execute(
        select(Chunk.id).where(Chunk.doc_id == doc_id)
    )).scalars().all()
    await db.execute(sa_delete(Chunk).where(Chunk.doc_id == doc_id))

    idx = _get_vec_index()
    if idx is not None:
        for cid in chunk_ids:
            try:
                idx.remove(int(cid))
            except Exception:
                pass

    # npz 삭제
    npz_path = os.path.join(PARSED_DIR, f"{doc_id}_emb.npz")
    if os.path.exists(npz_path):
        os.remove(npz_path)

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

    # 파서 재호출 — PDF는 parse_pdf, 나머지는 parse_non_pdf (② 정본 + TXT/MD)
    ext_retry    = os.path.splitext(doc.saved_path)[1].lower()
    file_type    = doc.file_type or ext_retry.lstrip('.')
    if ext_retry == '.pdf':
        parse_result = parse_pdf(filepath=doc.saved_path, source_file=doc.filename)
    else:
        parse_result = parse_non_pdf(filepath=doc.saved_path, source_file=doc.filename, ext=ext_retry)

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

    # 임베딩 재생성 + Chunk DB 저장
    if parse_result.get("status") == "success":
        background_tasks.add_task(_ingest_chunks_to_db, doc.id, parse_result.get("pages", []))

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
                # ③ 정본: 영↔한 기술용어 동의어 보강 (한국어 쿼리→영어 문서,
                # 영어 쿼리→한국어 문서 BM25 매칭 지원) + 쿼리 전용 토크나이저
                if any('가' <= c <= '힣' for c in q):
                    bm25_q = _augment_ko_to_en(q)
                else:
                    bm25_q = _augment_en_to_ko(q)
                q_tokens   = _tokenize_bm25(bm25_q, is_query=True)
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
        idx = _get_vec_index()
        model = _get_embed_model()
        if idx is not None and model is not None:
            q_emb = (await asyncio.to_thread(
                model.encode, [q], normalize_embeddings=True, show_progress_bar=False
            ))[0].astype(np.float32)
            try:
                # [수정 2026-07-04] turbovec search는 2차원 쿼리 배열을 받고
                # (scores, ids) "순서"로 반환한다 — 기존 코드는 (ids, scores)로
                # 거꾸로 받고 1차원을 넘겨서 시맨틱 경로가 항상 예외→fallback으로 빠졌음.
                scores_2d, ids_2d = idx.search(q_emb.reshape(1, -1), 50)
                vec_scores, vec_ids = scores_2d[0], ids_2d[0]
                # chunk_ids → doc_id 매핑
                if vec_ids is not None and len(vec_ids) > 0:
                    chunk_rows = (await db.execute(
                        select(Chunk.id, Chunk.doc_id, Chunk.text, Chunk.page_num)
                        .where(Chunk.id.in_([int(i) for i in vec_ids]))
                    )).all()
                    id_to_row = {row.id: row for row in chunk_rows}
                    for cid, score in zip(vec_ids, vec_scores):
                        row = id_to_row.get(int(cid))
                        if row is None:
                            continue
                        did = row.doc_id
                        if float(score) > sem_scores.get(did, -1):
                            sem_scores[did] = float(score)
                            sem_chunks[did] = row.text
                            sem_pages[did]  = row.page_num
            except Exception as e:
                print(f"[search] turbovec 오류, numpy fallback: {e}")
                # numpy .npz fallback
                if model is not None:
                    try:
                        q_emb_np = (await asyncio.to_thread(
                            model.encode, [q],
                            normalize_embeddings=True,
                            show_progress_bar=False,
                        ))[0]
                        for doc in docs:
                            emb_path = os.path.join(PARSED_DIR, f"{doc.id}_emb.npz")
                            if not os.path.exists(emb_path):
                                continue
                            data     = np.load(emb_path, allow_pickle=True)
                            embs     = data["embeddings"]
                            sims     = embs @ q_emb_np
                            best_idx = int(np.argmax(sims))
                            sem_scores[doc.id] = float(sims[best_idx])
                            if "chunks" in data:
                                sem_chunks[doc.id] = str(data["chunks"][best_idx])
                            if "pnums" in data:
                                sem_pages[doc.id]  = int(data["pnums"][best_idx])
                    except Exception as e2:
                        print(f"[search] 시맨틱 오류: {e2}")
        else:
            # turbovec 없으면 기존 npz 방식 fallback
            try:
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
                        embs     = data["embeddings"]
                        sims     = embs @ q_emb
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
    # ③ 정본의 비대칭 k 채택: BM25 k=30(정확 매칭 rank 1 이점 강화), 벡터 k=60(표준).
    # alpha 가중은 프론트 슬라이더(의미↔키워드 비중) 지원을 위해 유지 — ③ 정본에는
    # 없는 백엔드 확장이며, alpha=0.5일 때 ③의 균등 합산과 같은 취지가 되도록 절반씩 배분.
    n = len(docs)

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
        rrf = (1 - alpha) / (_RRF_K_BM25 + br) + alpha / (_RRF_K_VEC + sr)

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
            # Chunks 삭제 + turbovec 제거
            chunk_ids = (await db.execute(
                select(Chunk.id).where(Chunk.doc_id == doc_id)
            )).scalars().all()
            await db.execute(sa_delete(Chunk).where(Chunk.doc_id == doc_id))
            idx = _get_vec_index()
            if idx is not None:
                for cid in chunk_ids:
                    try:
                        idx.remove(int(cid))
                    except Exception:
                        pass
            # npz 삭제
            npz_path = os.path.join(PARSED_DIR, f"{doc_id}_emb.npz")
            if os.path.exists(npz_path):
                os.remove(npz_path)
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
    background_tasks: BackgroundTasks,
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

    # 재인덱싱 — flagged 페이지는 검색 인덱싱에서 제외되므로(③ 정본 정책),
    # 관리자가 텍스트를 수정해 플래그가 풀린 페이지는 여기서 다시 색인해야
    # "수정한 내용이 검색에 잡힌다". (이걸 빼먹으면 수정해도 검색에 안 나옴)
    background_tasks.add_task(_ingest_chunks_to_db, doc_id, parse_data.get("pages", []))

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


# ── 프론트 정적 서빙 (배포용 통합 서빙) ──────────────────────────────────────
# Dockerfile이 React 빌드 산출물(dist/)을 backend/static/에 복사해 넣는다.
# 이 mount는 "파일 맨 끝"에 있어야 한다 — FastAPI는 등록 순서대로 매칭하므로
# 위의 API 라우트들(/upload, /search, /admin/* 등)이 항상 먼저 잡히고,
# 그 외 경로(/assets/*.js 등)만 정적 파일로 처리된다.
# 로컬 개발처럼 static/ 폴더가 없으면 아무것도 하지 않는다 (기존 동작 유지).
if os.path.isdir(_STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
