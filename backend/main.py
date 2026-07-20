# FastAPI 서버 메인 파일
# 이 파일을 실행하면 백엔드 서버가 켜집니다

import asyncio
import os
import json
import hashlib
import secrets
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query, Body, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware  # CORS 설정용
import itsdangerous
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete, update as sa_update
from typing import Literal
from datetime import datetime, timedelta

from sqlalchemy import text

from database import engine, get_db, Base, AsyncSessionLocal
from models import Document, SearchLog, Chunk, Bookmark, DocumentPermission, User, Comment, AdminEmail, RevokedSession, Notification
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from core.parser import parse_pdf                   # 백엔드 배포 정본 파서 — 최신 parser 브랜치(parser/parser.py) 기준, import only
from core.office_adapter import parse_non_pdf       # 비-PDF 입구: ② hwp_postprocess 정본 + TXT/MD 리더

# .env 파일(backend/.env, git 추적 안 됨)이 있으면 여기서 로드 — GOOGLE_CLIENT_ID·SESSION_SECRET·
# ADMIN_EMAILS 같은 값을 매번 셸 명령에 export하지 않고 파일로 관리할 수 있게 한다.
load_dotenv()

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


# ── 정체 문서 주기적 스윕 (2026-07-06) ────────────────────────────────────────
# 예외처리 방어막(_parse_and_ingest)을 넣었지만, 원본 상태반영과 그 복구 시도가
# 같은 순간의 DB 커넥션 풀 고갈 피크에 둘 다 걸리면 문서가 "parsing"에 남을 수
# 있다(재현 테스트에서 실측: 2건). 서버 재시작 없이도 이런 잔여 정체 문서를
# 상시 자동 정리하는 안전망. 기존 lifespan의 "재시작 시 정체 문서 정리"는
# 그대로 두고(재시작이라는 다른 상황에 대한 최종 안전망), 이 스윕을 추가로 얹는다.
#
# [임시 완화값 — 필요시 조정]
_SWEEP_INTERVAL_SEC  = 5 * 60   # 스윕 실행 간격(초) — 5분마다
_SWEEP_STALE_MINUTES = 10       # updated_at 기준 이만큼(분) 지난 parsing 문서는 정체로 간주

async def _sweep_stale_parsing():
    """updated_at 기준으로 오래 parsing에 머문 문서를 주기적으로 failed 처리한다.
    스윕도 DB 세션을 쓰므로 예외가 나도 태스크가 죽지 않도록 try/except로 감싼다."""
    while True:
        try:
            await asyncio.sleep(_SWEEP_INTERVAL_SEC)
            cutoff = datetime.now() - timedelta(minutes=_SWEEP_STALE_MINUTES)
            async with AsyncSessionLocal() as sess:
                stale = (await sess.execute(
                    select(Document).where(
                        Document.status == "parsing",
                        Document.updated_at < cutoff,
                    )
                )).scalars().all()
                for d in stale:
                    d.status = "failed"
                    d.error  = "처리 시간 초과 — 자동 정리됨 (재시도 필요)"
                if stale:
                    await sess.commit()
                    print(f"[sweep] 정체 문서 {len(stale)}건을 failed로 정리")
        except asyncio.CancelledError:
            raise  # 앱 종료 시 태스크 취소는 그대로 전파시켜 정상 종료되게 한다
        except Exception as e:
            print(f"[sweep] 정체 문서 정리 중 오류: {e}")


@asynccontextmanager
async def lifespan(_):
    """서버 시작 시 DB 테이블 생성 + 스키마 마이그레이션 + turbovec 재구성."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 웹 화면에서 추가한 관리자 이메일을 메모리 캐시로 로드 (.env 고정 관리자와 별개)
    async with AsyncSessionLocal() as sess:
        rows = (await sess.execute(select(AdminEmail.email))).scalars().all()
        _extra_admin_emails.update(e.lower() for e in rows)
        print(f"[startup] 추가 관리자 이메일 {len(_extra_admin_emails)}개 로드")

    # 세션 블랙리스트 청소 — SESSION_MAX_AGE보다 오래된 항목은 그 세션 토큰 자체가
    # 이미 자연 만료됐을 시점이라 더 이상 조회할 필요가 없다(무한정 쌓이는 것 방지).
    async with AsyncSessionLocal() as sess:
        cutoff = datetime.now() - timedelta(seconds=SESSION_MAX_AGE)
        result = await sess.execute(sa_delete(RevokedSession).where(RevokedSession.revoked_at < cutoff))
        await sess.commit()
        if result.rowcount:
            print(f"[startup] 만료된 세션 블랙리스트 {result.rowcount}건 정리")

    # [수정 2026-07-06] ALTER TABLE 5개를 engine.begin() 하나(트랜잭션 하나)에 묶지 않고
    # 각각 독립 트랜잭션에서 실행한다. PostgreSQL은 트랜잭션 안에서 문장 하나가 에러(예:
    # 컬럼 이미 존재)를 내면 그 트랜잭션 전체가 aborted 상태가 되어, 뒤이은 ALTER들이
    # 같은 커넥션에서 전부 InFailedSqlTransaction으로 조용히 실패한다(SQLite는 문장별로
    # 독립적이라 이 문제가 안 터져서 발견이 늦어짐). 독립 트랜잭션으로 나누면 한 문장이
    # 실패해도 그 트랜잭션만 롤백되고 다음 ALTER는 깨끗한 새 트랜잭션에서 실행된다.
    migrations = [
        "ALTER TABLE documents ADD COLUMN file_type VARCHAR",
        "ALTER TABLE documents ADD COLUMN original_path VARCHAR",
        "ALTER TABLE documents ADD COLUMN title VARCHAR",
        "ALTER TABLE documents ADD COLUMN memo TEXT",
        "ALTER TABLE documents ADD COLUMN sha256 VARCHAR(64)",
        "ALTER TABLE documents ADD COLUMN updated_at DATETIME",
        "ALTER TABLE documents ADD COLUMN view_count INTEGER DEFAULT 0",
        "ALTER TABLE documents ADD COLUMN uploaded_by VARCHAR",
        "ALTER TABLE documents ADD COLUMN category_ai_suggested BOOLEAN DEFAULT 0",
        "ALTER TABLE documents ADD COLUMN category_ai_checked BOOLEAN DEFAULT 0",
    ]
    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as e:
            # 이미 존재하는 컬럼이면 정상 — 그래도 어떤 ALTER가 왜 스킵됐는지는 남긴다.
            print(f"[migration] '{stmt}' 스킵/실패: {e}")

    # [비동기화 2026-07-05] 재시작으로 중단된 'parsing' 문서 정리.
    # 파싱은 백그라운드 태스크라 서버가 재시작되면 진행 중이던 작업이 유실된다 →
    # 'parsing' 상태로 영영 멈춘 문서가 남지 않도록 failed로 내려 재시도를 유도한다.
    async with AsyncSessionLocal() as sess:
        stuck = (await sess.execute(
            select(Document).where(Document.status == "parsing")
        )).scalars().all()
        for d in stuck:
            d.status = "failed"
            d.error  = "서버 재시작으로 파싱이 중단되었습니다. 재시도해 주세요."
        if stuck:
            await sess.commit()
            print(f"[startup] 중단된 parsing 문서 {len(stuck)}건을 failed로 정리")

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

    sweep_task = asyncio.create_task(_sweep_stale_parsing())
    print(f"[startup] 정체 문서 스윕 태스크 시작 (주기 {_SWEEP_INTERVAL_SEC}s, 기준 {_SWEEP_STALE_MINUTES}분)")

    yield

    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        pass
    print("[shutdown] 정체 문서 스윕 태스크 종료")


# FastAPI 앱 객체를 만듭니다
app = FastAPI(lifespan=lifespan)

# CORS 설정: 환경변수로 allowed origins 확장 (기본값: localhost:5173)
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # 로그인 세션 쿠키를 프론트(다른 origin)로 주고받으려면 필요
)

# ── 로그인(구글 OAuth, 회사 도메인 제한) ──────────────────────────────────────
# GOOGLE_CLIENT_ID가 설정되지 않으면 로그인 기능 자체가 꺼진 상태로 동작한다
# (지금까지의 무인증 MVP와 동일) — Google Cloud에서 Client ID를 발급받아
# 환경변수로 넣는 순간부터 전체 API가 로그인을 요구하도록 자동 전환된다.
GOOGLE_CLIENT_ID      = os.environ.get("GOOGLE_CLIENT_ID", "")
ALLOWED_EMAIL_DOMAIN  = os.environ.get("ALLOWED_EMAIL_DOMAIN", "")  # 비어있으면 도메인 제한 없음
SESSION_SECRET        = os.environ.get("SESSION_SECRET", "dev-insecure-secret-change-me")
AUTH_ENABLED          = bool(GOOGLE_CLIENT_ID)
# 관리자 탭/API를 볼 수 있는 이메일 목록(쉼표 구분). 로그인이 꺼져 있으면(AUTH_ENABLED=False)
# 지금까지처럼 누구나 관리자 화면에 접근 가능 — 로그인이 켜져야만 이 제한이 실제로 걸린다.
# .env의 이 값은 고정 관리자(웹 화면에서 삭제 불가, 잠금 방지용) — 추가 관리자는
# AdminEmail 테이블로 관리하며 _extra_admin_emails에 캐시해 매 요청마다 DB를 조회하지 않는다.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
_extra_admin_emails: set[str] = set()

def _is_admin(email: str | None) -> bool:
    if not AUTH_ENABLED:
        return True
    if not email:
        return False
    email = email.lower()
    return email in ADMIN_EMAILS or email in _extra_admin_emails

def _current_user_email(request: Request) -> str:
    """로그인이 꺼져 있으면 북마크·조회수 등 '사용자별' 기능을 단일 공용 사용자로 동작시킨다."""
    return getattr(request.state, "user_email", None) or "anonymous"


async def _filter_readable_ids(db: AsyncSession, doc_ids: list[int], user_email: str) -> set[int]:
    """문서별 읽기 권한 필터. 로그인이 꺼져 있거나 관리자면 전부 통과.
    권한이 하나도 설정 안 된 문서(공개)이거나, 본인이 명시적으로 허가된 문서만 남긴다."""
    if not AUTH_ENABLED or _is_admin(user_email) or not doc_ids:
        return set(doc_ids)
    restricted_ids = set((await db.execute(
        select(DocumentPermission.doc_id).where(DocumentPermission.doc_id.in_(doc_ids)).distinct()
    )).scalars().all())
    if not restricted_ids:
        return set(doc_ids)
    granted_ids = set((await db.execute(
        select(DocumentPermission.doc_id).where(
            DocumentPermission.doc_id.in_(doc_ids), DocumentPermission.user_email == user_email
        )
    )).scalars().all())
    return {d for d in doc_ids if d not in restricted_ids or d in granted_ids}


async def _can_read_doc(db: AsyncSession, doc_id: int, user_email: str) -> bool:
    return doc_id in await _filter_readable_ids(db, [doc_id], user_email)


async def _resolve_names(db: AsyncSession, emails: list) -> dict:
    """이메일 목록 → {이메일: 표시 이름} 딕셔너리. 이름이 없으면(User 테이블에 없거나
    구글 계정에 이름이 없는 경우) 이메일 자체를 표시 이름으로 그대로 돌려준다."""
    unique = [e for e in set(emails) if e and e != "anonymous"]
    if not unique:
        return {}
    rows = (await db.execute(select(User.email, User.name).where(User.email.in_(unique)))).all()
    found = {row.email: row.name for row in rows if row.name}
    return {email: found.get(email, email) for email in unique}

SESSION_COOKIE   = "km_session"
SESSION_MAX_AGE  = 7 * 24 * 3600  # 세션 유효기간 7일
_session_signer  = itsdangerous.URLSafeTimedSerializer(SESSION_SECRET, salt="km-session")

def _create_session_token(email: str) -> str:
    # sid: 세션마다 부여하는 무작위 ID. 서명 대상 안에 들어있어 위변조 불가 —
    # 로그아웃 시 이 값만 RevokedSession에 저장해 블랙리스트로 즉시 무효화한다.
    return _session_signer.dumps({"email": email, "sid": secrets.token_urlsafe(16)})

def _verify_session_token(token: str | None) -> dict | None:
    """서명·만료만 확인한다 — 블랙리스트(무효화) 여부는 _is_session_revoked()에서 별도로 확인."""
    if not token:
        return None
    try:
        data = _session_signer.loads(token, max_age=SESSION_MAX_AGE)
        return data if data.get("email") else None
    except itsdangerous.BadData:
        return None

async def _is_session_revoked(session_id: str | None, db: AsyncSession) -> bool:
    if not session_id:
        return False
    row = (await db.execute(
        select(RevokedSession.session_id).where(RevokedSession.session_id == session_id)
    )).scalar_one_or_none()
    return row is not None


@app.get("/auth/config")
async def 인증_설정():
    """프론트가 로그인 화면을 띄울지 말지 판단하는 기준."""
    return {"enabled": AUTH_ENABLED, "google_client_id": GOOGLE_CLIENT_ID if AUTH_ENABLED else None}


@app.post("/auth/google")
async def 구글_로그인(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="로그인 기능이 활성화되어 있지 않습니다")
    credential = body.get("credential")
    if not credential:
        raise HTTPException(status_code=400, detail="credential이 없습니다")

    try:
        idinfo = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="구글 로그인 검증에 실패했습니다")

    email = idinfo.get("email")
    if not email or not idinfo.get("email_verified"):
        raise HTTPException(status_code=401, detail="이메일이 확인되지 않은 계정입니다")

    if ALLOWED_EMAIL_DOMAIN:
        hd     = (idinfo.get("hd") or "").lower()
        domain = email.rsplit("@", 1)[-1].lower()
        if ALLOWED_EMAIL_DOMAIN.lower() not in (hd, domain):
            raise HTTPException(status_code=403, detail=f"{ALLOWED_EMAIL_DOMAIN} 계정만 로그인할 수 있습니다")

    # 이메일→이름 조회용 User 테이블 upsert — 문서 목록/검색결과에 "업로드한 사람 이름"을
    # 보여줄 때, 그 문서를 올린 사람이 지금 로그인한 사람이 아니어도 이름을 찾을 수 있게 한다.
    name, picture = idinfo.get("name"), idinfo.get("picture")
    existing_user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing_user is None:
        db.add(User(email=email, name=name, picture=picture))
    else:
        existing_user.name    = name or existing_user.name
        existing_user.picture = picture or existing_user.picture
    await db.commit()

    resp = JSONResponse({"email": email, "name": name, "picture": picture})
    resp.set_cookie(
        SESSION_COOKIE, _create_session_token(email),
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=False,  # 로컬 http 개발 기준 — 배포(https) 시 True로 바꿀 것
    )
    return resp


@app.get("/auth/me")
async def 내_정보(request: Request, db: AsyncSession = Depends(get_db)):
    data = _verify_session_token(request.cookies.get(SESSION_COOKIE))
    if not data or await _is_session_revoked(data.get("sid"), db):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    email = data["email"]
    return {"email": email, "is_admin": _is_admin(email)}


@app.post("/auth/logout")
async def 로그아웃(request: Request, db: AsyncSession = Depends(get_db)):
    # 이 세션의 sid를 블랙리스트에 올려서, 쿠키가 이미 복사돼 있어도 즉시 무효화되게 한다
    # (쿠키만 지우면 만료시간까지 그 값 자체는 여전히 유효한 서명 토큰이라 남아있는 문제 해결).
    data = _verify_session_token(request.cookies.get(SESSION_COOKIE))
    if data and data.get("sid"):
        exists = (await db.execute(
            select(RevokedSession.session_id).where(RevokedSession.session_id == data["sid"])
        )).scalar_one_or_none()
        if exists is None:
            db.add(RevokedSession(session_id=data["sid"]))
            await db.commit()
    resp = JSONResponse({"message": "로그아웃됐습니다"})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── 관리자 이메일 관리 (/admin 접두사라 미들웨어가 이미 관리자만 통과시킴) ──────────
# .env의 ADMIN_EMAILS는 여기서 삭제할 수 없는 고정 관리자(계정 잠금 방지용).
# 웹 화면에서 추가·삭제하는 건 AdminEmail 테이블 + _extra_admin_emails 메모리 캐시로 관리한다.
# 구글 로그인 자체를 할 수 있는 사람(테스트 사용자)은 Google Cloud Console에서 별도로 관리한다 —
# 여기서 다루는 건 "로그인은 되는 사람 중 누가 관리자 권한을 갖는가"이다.
@app.get("/admin/admin-emails")
async def 관리자_목록(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AdminEmail).order_by(AdminEmail.created_at))).scalars().all()
    return {
        "env_admins": sorted(ADMIN_EMAILS),
        "extra_admins": [
            {"email": r.email, "added_by": r.added_by, "created_at": str(r.created_at)}
            for r in rows
        ],
    }


@app.post("/admin/admin-emails")
async def 관리자_추가(
    request: Request,
    email: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="올바른 이메일을 입력하세요")
    if email in ADMIN_EMAILS:
        raise HTTPException(status_code=400, detail="이미 기본 관리자로 등록된 이메일입니다")
    existing = (await db.execute(select(AdminEmail).where(AdminEmail.email == email))).scalar_one_or_none()
    if existing is None:
        db.add(AdminEmail(email=email, added_by=_current_user_email(request)))
        await db.commit()
    _extra_admin_emails.add(email)
    return {"email": email, "added": True}


@app.delete("/admin/admin-emails/{email}")
async def 관리자_삭제(email: str, db: AsyncSession = Depends(get_db)):
    email = email.strip().lower()
    if email in ADMIN_EMAILS:
        raise HTTPException(status_code=400, detail="기본 관리자는 여기서 삭제할 수 없습니다 (.env에서 직접 수정하세요)")
    await db.execute(sa_delete(AdminEmail).where(AdminEmail.email == email))
    await db.commit()
    _extra_admin_emails.discard(email)
    return {"email": email, "removed": True}


# ── 북마크 — 로그인 켜져 있으면 사용자별, 꺼져 있으면 "anonymous" 단일 공용 목록 ──────
@app.get("/bookmarks")
async def 북마크_목록(request: Request, db: AsyncSession = Depends(get_db)):
    user_email = _current_user_email(request)
    result = await db.execute(
        select(Document, Bookmark.created_at)
        .join(Bookmark, Bookmark.doc_id == Document.id)
        .where(Bookmark.user_email == user_email)
        .order_by(Bookmark.created_at.desc())
    )
    rows = result.all()
    readable = await _filter_readable_ids(db, [doc.id for doc, _ in rows], user_email)
    return [
        {
            "id": doc.id, "filename": doc.filename, "title": doc.title,
            "category": doc.category, "file_type": doc.file_type,
            "page_count": doc.page_count, "view_count": doc.view_count,
            "status": doc.status, "uploaded_at": str(doc.uploaded_at),
            "bookmarked_at": str(bookmarked_at),
        }
        for doc, bookmarked_at in rows if doc.id in readable
    ]


@app.post("/bookmarks/{doc_id}")
async def 북마크_추가(doc_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_email = _current_user_email(request)
    doc = (await db.execute(select(Document.id).where(Document.id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    exists = (await db.execute(
        select(Bookmark.id).where(Bookmark.doc_id == doc_id, Bookmark.user_email == user_email)
    )).scalar_one_or_none()
    if exists is None:
        db.add(Bookmark(doc_id=doc_id, user_email=user_email))
        await db.commit()
    return {"doc_id": doc_id, "bookmarked": True}


@app.delete("/bookmarks/{doc_id}")
async def 북마크_삭제(doc_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user_email = _current_user_email(request)
    await db.execute(sa_delete(Bookmark).where(Bookmark.doc_id == doc_id, Bookmark.user_email == user_email))
    await db.commit()
    return {"doc_id": doc_id, "bookmarked": False}


# ── 문서 상세 팝업의 댓글 — 로그인 켜져 있으면 작성자별, 꺼져 있으면 "anonymous" 공용 ──────
@app.get("/documents/{doc_id}/comments")
async def 댓글_목록(doc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Comment).where(Comment.doc_id == doc_id).order_by(Comment.created_at.asc())
    )
    comments = result.scalars().all()
    names = await _resolve_names(db, [c.user_email for c in comments])
    return [
        {
            "id": c.id, "content": c.content,
            "user_email": c.user_email, "user_name": names.get(c.user_email, c.user_email),
            "created_at": str(c.created_at),
        }
        for c in comments
    ]


@app.post("/documents/{doc_id}/comments")
async def 댓글_작성(
    doc_id: int,
    request: Request,
    content: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    content = content.strip()[:1000]
    if not content:
        raise HTTPException(status_code=400, detail="댓글 내용을 입력하세요")
    doc = (await db.execute(
        select(Document.id, Document.uploaded_by, Document.title, Document.filename).where(Document.id == doc_id)
    )).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    user_email = _current_user_email(request)
    comment = Comment(doc_id=doc_id, user_email=user_email, content=content)
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    names = await _resolve_names(db, [user_email])
    commenter_name = names.get(user_email, user_email)

    # 내 문서에 댓글이 달렸을 때 업로더에게 알림 — 본인이 자기 문서에 단 댓글은 알림 생성 안 함
    if doc.uploaded_by and doc.uploaded_by != "anonymous" and doc.uploaded_by != user_email:
        doc_label = doc.title or doc.filename
        db.add(Notification(
            recipient=doc.uploaded_by,
            doc_id=doc_id,
            comment_id=comment.id,
            message=f"{commenter_name}님이 '{doc_label}' 문서에 댓글을 남겼습니다: {content[:80]}",
        ))
        await db.commit()

    return {
        "id": comment.id, "content": comment.content,
        "user_email": user_email, "user_name": commenter_name,
        "created_at": str(comment.created_at),
    }


@app.delete("/documents/{doc_id}/comments/{comment_id}")
async def 댓글_삭제(doc_id: int, comment_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    comment = (await db.execute(
        select(Comment).where(Comment.id == comment_id, Comment.doc_id == doc_id)
    )).scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="해당 댓글을 찾을 수 없습니다")
    user_email = _current_user_email(request)
    if comment.user_email != user_email and not _is_admin(user_email):
        raise HTTPException(status_code=403, detail="본인이 작성한 댓글만 삭제할 수 있습니다")
    await db.delete(comment)
    await db.commit()
    return {"id": comment_id, "deleted": True}


# ── 알림 (내 문서에 달린 댓글 등) ────────────────────────────────────────────
@app.get("/notifications")
async def 알림_목록(request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    rows = (await db.execute(
        select(Notification, Document.title, Document.filename)
        .join(Document, Document.id == Notification.doc_id)
        .where(Notification.recipient == email)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )).all()
    return [
        {
            "id": row.Notification.id,
            "doc_id": row.Notification.doc_id,
            "message": row.Notification.message,
            "is_read": row.Notification.is_read,
            "created_at": str(row.Notification.created_at),
            "doc_label": row.title or row.filename,
        }
        for row in rows
    ]


@app.get("/notifications/unread-count")
async def 알림_안읽음_수(request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    count = (await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.recipient == email, Notification.is_read == False)
    )).scalar()
    return {"count": count}


@app.post("/notifications/{notification_id}/read")
async def 알림_읽음_처리(notification_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    notif = (await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.recipient == email)
    )).scalar_one_or_none()
    if notif is None:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다")
    notif.is_read = True
    await db.commit()
    return {"message": "읽음 처리됐습니다"}


@app.post("/notifications/read-all")
async def 알림_전체_읽음_처리(request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    await db.execute(
        sa_update(Notification)
        .where(Notification.recipient == email, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "모두 읽음 처리됐습니다"}


# ── 문서별 읽기 권한 (관리자 전용 — /admin 접두사라 미들웨어가 이미 관리자만 통과시킴) ──────
# 문서에 권한이 하나도 없으면 "전체 공개"(기존 동작). 최소 한 명이라도 추가하는 순간부터
# 그 문서는 지정된 사람 + 관리자만 검색·다운로드 가능한 "제한 문서"로 바뀐다.
@app.get("/admin/documents/{doc_id}/permissions")
async def 문서_권한_목록(doc_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(DocumentPermission).where(DocumentPermission.doc_id == doc_id).order_by(DocumentPermission.created_at)
    )).scalars().all()
    return [{"email": r.user_email, "granted_at": str(r.created_at)} for r in rows]


@app.post("/admin/documents/{doc_id}/permissions")
async def 문서_권한_부여(doc_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="이메일을 입력하세요")
    doc = (await db.execute(select(Document.id).where(Document.id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    exists = (await db.execute(
        select(DocumentPermission.id).where(DocumentPermission.doc_id == doc_id, DocumentPermission.user_email == email)
    )).scalar_one_or_none()
    if exists is None:
        db.add(DocumentPermission(doc_id=doc_id, user_email=email))
        await db.commit()
    return {"doc_id": doc_id, "email": email, "granted": True}


@app.delete("/admin/documents/{doc_id}/permissions")
async def 문서_권한_회수(doc_id: int, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    await db.execute(sa_delete(DocumentPermission).where(
        DocumentPermission.doc_id == doc_id, DocumentPermission.user_email == email.strip().lower()
    ))
    await db.commit()
    return {"doc_id": doc_id, "email": email, "granted": False}


# 로그인 기능이 켜져 있으면(AUTH_ENABLED) 아래 목록을 제외한 모든 API가 유효한
# 세션 쿠키를 요구한다. 꺼져 있으면 기존 무인증 동작 그대로 통과시킨다.
_AUTH_PUBLIC_PATHS = {"/", "/auth/config", "/auth/google", "/auth/me", "/auth/logout", "/docs", "/openapi.json", "/redoc"}

@app.middleware("http")
async def _require_login(request: Request, call_next):
    if not AUTH_ENABLED or request.method == "OPTIONS" or request.url.path in _AUTH_PUBLIC_PATHS:
        return await call_next(request)
    data = _verify_session_token(request.cookies.get(SESSION_COOKIE))
    if not data:
        return JSONResponse(status_code=401, content={"detail": "로그인이 필요합니다"})
    # 미들웨어는 라우트가 아니라 Depends(get_db)를 못 쓰므로 세션을 직접 연다 —
    # 블랙리스트(로그아웃된 세션) 조회 한 번만 하고 바로 닫는다.
    async with AsyncSessionLocal() as _sess:
        if await _is_session_revoked(data.get("sid"), _sess):
            return JSONResponse(status_code=401, content={"detail": "로그아웃된 세션입니다. 다시 로그인해 주세요."})
    email = data["email"]
    if request.url.path.startswith("/admin") and not _is_admin(email):
        return JSONResponse(status_code=403, content={"detail": "관리자 권한이 필요합니다"})
    request.state.user_email = email
    return await call_next(request)

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
# [수정] Gemma 3는 Google Custom ToU 원격 서비스 제한 조항이 있어 PRD가 명시적으로
# 배제한 모델이었는데 기본값으로 남아있던 것을 발견 — 상업적 이용 제한 없는
# Apache 2.0 라이선스 모델(Qwen2.5)로 교체. 실제 로컬 Ollama에도 이미 설치돼 있음.
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
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

# ── BM25 검색 캐시 ───────────────────────────────────────────────────────────
# [수정] 예전엔 실제_검색_실행()이 검색할 때마다 매번 문서 본문을 디스크에서 다시 읽고
# Kiwi로 처음부터 재토큰화해 BM25Okapi를 통째로 재구축했다 — 문서 20개 규모에서도
# 검색 1회에 0.5~0.7초가 걸릴 정도로 느렸고, 문서 수에 비례해 계속 나빠지는 구조였다.
# turbovec(의미검색) 인덱스처럼 전역에 캐싱해두고, 문서가 추가·삭제·수정될 때만
# _invalidate_bm25_cache()로 무효화해 다음 검색 시 한 번만 다시 만든다.
#
# 카테고리 필터는 코퍼스 자체를 나누지 않고(캐시 하나만 유지), 점수 계산 후
# "이 청크의 문서가 이번 요청의 카테고리 필터에 해당하는가"로 후필터링한다 —
# 실제 검색엔진에서 흔한 방식이며, 카테고리마다 별도 인덱스를 만들 필요가 없다.
_bm25_cache: dict = {"built": False, "bm25": None, "chunk_doc_ids": []}
_doc_text_cache: dict = {}  # doc_id -> {"full_text": str, "pages": list}  (파싱 JSON 캐시)

def _invalidate_bm25_cache():
    _bm25_cache["built"] = False
    _bm25_cache["bm25"] = None
    _bm25_cache["chunk_doc_ids"] = []

def _invalidate_doc_text(doc_id: int):
    _doc_text_cache.pop(doc_id, None)

def _get_doc_text(doc_id: int) -> dict:
    """문서 본문(파싱 JSON) 캐시 — 재파싱(색인) 시에만 _invalidate_doc_text()로 비워진다."""
    cached = _doc_text_cache.get(doc_id)
    if cached is not None:
        return cached
    full_text  = ""
    pages_list = []
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    if os.path.exists(parsed_path):
        with open(parsed_path, "r", encoding="utf-8") as f:
            pd = json.load(f)
        pages_list = pd.get("pages", [])
        full_text  = "\n".join(p.get("text", "") for p in pages_list)
    result = {"full_text": full_text, "pages": pages_list}
    _doc_text_cache[doc_id] = result
    return result

async def _ensure_bm25_cache(db: AsyncSession):
    """캐시가 비어있을 때만(문서 변경 후 첫 검색) 전체 문서 대상으로 다시 만든다."""
    if _bm25_cache["built"]:
        return
    docs = (await db.execute(select(Document).where(Document.status == "success"))).scalars().all()

    all_chunk_items: list = []  # (doc_id, text)
    for doc in docs:
        emb_path = os.path.join(PARSED_DIR, f"{doc.id}_emb.npz")
        if os.path.exists(emb_path):
            try:
                nd = np.load(emb_path, allow_pickle=True)
                for chunk in nd["chunks"]:
                    all_chunk_items.append((doc.id, str(chunk)))
            except Exception:
                info = _get_doc_text(doc.id)
                combined = " ".join(filter(None, [doc.filename, doc.title or "", doc.memo or "", info["full_text"]]))
                all_chunk_items.append((doc.id, combined))
        else:
            info = _get_doc_text(doc.id)
            combined = " ".join(filter(None, [doc.filename, doc.title or "", doc.memo or "", info["full_text"]]))
            for i in range(0, len(combined), 350):
                seg = combined[i:i + 400].strip()
                if len(seg) >= 20:
                    all_chunk_items.append((doc.id, seg))

    bm25 = None
    if all_chunk_items:
        try:
            from rank_bm25 import BM25Okapi
            corpus = [_tokenize_bm25(t) for _, t in all_chunk_items]
            bm25 = BM25Okapi(corpus)
        except ImportError:
            bm25 = None

    _bm25_cache["bm25"] = bm25
    _bm25_cache["chunk_doc_ids"] = [d for d, _ in all_chunk_items]
    _bm25_cache["built"] = True
    print(f"[search] BM25 캐시 재구축: 문서 {len(docs)}개, 청크 {len(all_chunk_items)}개")


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
    # [수정 2026-07-06] 이 시점은 이미 doc.status="success"로 커밋된 뒤일 수 있으므로
    # 여기서 예외가 나도 status는 건드리지 않는다(건드리면 "성공했는데 실패로 뒤집힘"
    # 이라는 또 다른 혼란이 생김). 대신 조용히 삼키지 않고 반드시 로그를 남겨, 색인이
    # 누락된 문서(검색에 안 잡히는 문서)를 나중에 사람이 로그로 식별할 수 있게 한다.
    idx = _get_vec_index()
    try:
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
        # 새 문서가 색인됐으니 다음 검색부터 BM25 캐시를 다시 만들어야 이 문서가 잡힌다
        _invalidate_doc_text(doc_id)
        _invalidate_bm25_cache()
    except Exception as e:
        print(f"[ingest] doc_id={doc_id} 청킹/색인 실패(검색 누락 가능): {e}")


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


# 파싱 동시 실행 제한 (2026-07-05):
# 파싱은 CPU 집약이라 GIL을 점유한다. 여러 개를 동시에 스레드로 돌리면 이벤트 루프가
# 굶어 API 응답이 급격히 느려진다(실측: 2개 동시 파싱 중 상태조회가 10초 초과).
# GIL 때문에 동시 파싱은 처리량 이득도 없으므로, 세마포어로 "한 번에 하나만" 직렬화한다.
# → 파싱 중에도 API가 안정적으로 응답(단일 파싱 시 실측 ~2.5초)하고, 대용량이 큐를 잡아도
#   요청은 즉시 접수되고 다른 API는 계속 동작한다. (PARSE_CONCURRENCY로 조정 가능)
#   더 큰 서버에서 진짜 병렬 파싱이 필요하면 ProcessPoolExecutor로 승격(별도 프로세스 = GIL 독립).
_PARSE_CONCURRENCY = max(1, int(os.environ.get("PARSE_CONCURRENCY", "1")))
_parse_semaphore = asyncio.Semaphore(_PARSE_CONCURRENCY)


async def _parse_and_ingest(doc_id: int, save_path: str, source_file: str, ext: str):
    """[비동기 파싱 파이프라인] 업로드/재시도 응답을 즉시 돌려준 뒤 백그라운드에서 실행된다.

    ⭐ 왜 이렇게 하나 (2026-07-05):
    파싱은 무겁다 — 페이지별 camelot 표 감지 때문에 대용량 PDF는 수십 분이 걸린다
    (실측: 60쪽 57초, 1,950쪽 추정 ~50분). 예전처럼 업로드 요청 안에서 동기로 파싱하면
    (1) 응답이 수십 분 지연돼 프록시·게이트웨이 타임아웃으로 업로드가 실패하고,
    (2) 동기 함수가 이벤트 루프를 통째로 막아 파싱 동안 서버 전체가 멈춘다.
    → 파싱을 asyncio.to_thread(스레드풀)로 돌려 이벤트 루프를 막지 않게 하고,
      업로드 요청은 'parsing' 상태만 만들고 즉시 응답한다. 프론트는 상태를 폴링한다.

    이 함수는 자체 DB 세션을 연다 — 요청 핸들러의 세션(db)은 응답과 함께 이미 닫히므로
    백그라운드에서 재사용하면 안 된다.
    """
    # 세마포어로 한 번에 하나만 파싱 (위 주석 참고). 대기 중에도 요청·다른 API는 계속 동작.
    async with _parse_semaphore:
        try:
            if ext == '.pdf':
                result = await asyncio.to_thread(parse_pdf, save_path, source_file)
            else:
                result = await asyncio.to_thread(parse_non_pdf, save_path, source_file, ext)
        except Exception as e:
            result = {"source_file": source_file, "status": "failed",
                      "error": f"파싱 중 예외가 발생했습니다: {e}", "pages": []}

    has_flagged = any(p.get("flagged") for p in result.get("pages", []))

    # [수정 2026-07-06] 이 블록에 try/except가 없으면, DB 커넥션 풀 고갈(QueuePool
    # TimeoutError) 같은 예외가 여기서 나는 순간 백그라운드 태스크가 그대로 죽어
    # doc.status가 "parsing"에 영구히 정체된다(재현 테스트에서 실제 관측됨). 어떤
    # 경로로도 문서가 success/failed 중 하나로는 반드시 수렴하도록 방어막을 둔다.
    try:
        # 파싱 도중 문서가 삭제됐을 수도 있으니 존재 확인 후 갱신
        async with AsyncSessionLocal() as sess:
            doc = (await sess.execute(
                select(Document).where(Document.id == doc_id)
            )).scalar_one_or_none()
            if doc is None:
                return
            doc.status      = result.get("status")
            doc.error       = result.get("error")
            doc.page_count  = len(result.get("pages", []))
            doc.has_flagged = has_flagged
            if result.get("status") == "success":
                doc.title = _extract_title(result.get("pages", []))
            await sess.commit()

        # AI 자동 카테고리 분류 — Ollama 호출(수 초 이상 걸릴 수 있음)은 DB 세션을
        # 닫아둔 채로 하고, 결과가 나온 뒤에만 짧게 다시 열어 category만 갱신한다.
        # category_ai_checked는 성공/실패와 무관하게 "시도가 끝났다"는 뜻으로 항상 세운다 —
        # 프론트가 이 값을 보고서야 "카테고리·특이사항 확인" 팝업을 띄우기 때문에, 세우지
        # 않으면 Ollama가 꺼져 있을 때 팝업이 영원히 안 뜨는 문제가 생긴다.
        if result.get("status") == "success":
            full_text = "\n".join(p.get("text", "") for p in result.get("pages", []))
            suggested = None
            if full_text.strip():
                suggested = await asyncio.to_thread(_ollama_classify_category, full_text)
            async with AsyncSessionLocal() as sess3:
                doc3 = (await sess3.execute(
                    select(Document).where(Document.id == doc_id)
                )).scalar_one_or_none()
                if doc3 is not None:
                    if suggested:
                        doc3.category = suggested
                        doc3.category_ai_suggested = True
                    doc3.category_ai_checked = True
                    await sess3.commit()
    except Exception:
        # 결과 반영 자체가 실패 — "parsing" 영구 정체를 막기 위해 failed로 강제 복구 시도
        try:
            async with AsyncSessionLocal() as sess2:
                doc2 = (await sess2.execute(
                    select(Document).where(Document.id == doc_id)
                )).scalar_one_or_none()
                if doc2 is not None:
                    doc2.status = "failed"
                    doc2.error  = "결과 저장 중 오류가 발생했습니다 (재시도 필요)"
                    await sess2.commit()
        except Exception as e2:
            # 복구 기록마저 실패 — 삼키지 않고 로그만 남기고, 태스크는 정상 종료시킨다.
            print(f"[parse] doc_id={doc_id} 상태반영 실패, 복구도 실패: {e2}")
        return

    # 파싱 결과 JSON 저장 (상세 보기·검색 본문 로드용)
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 성공 시 임베딩 + Chunk 색인 (이 함수 자체가 이미 백그라운드라 직접 await)
    if result.get("status") == "success":
        await _ingest_chunks_to_db(doc_id, result.get("pages", []))


# POST /upload — 파일을 받아 파싱하고 DB에 기록합니다
# category:      문서 분류 (spec/research/presentation/report/other)
# original_path: 폴더 업로드 시 원본 상대 경로 (예: 프로젝트A/자료/파일.pdf)
#                프론트에서 file.webkitRelativePath 를 보내면 됩니다
@app.post("/upload")
async def 파일_업로드(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("report"),
    original_path: str = Form(None),   # 폴더 업로드 시 상대 경로 (없으면 None)
    memo: str = Form(None),            # 업로드 시점에 바로 남기는 특이사항 — 검색 결과에 그대로 노출됨
    db: AsyncSession = Depends(get_db),
):
    # 카테고리는 고정 5종(spec/research/presentation/report/other) 외에도 클라이언트가
    # 자유롭게 새 이름을 입력해 만들 수 있다 — category는 단순 문자열 컬럼이라
    # 별도 마이그레이션·사전 등록 없이 그대로 저장·조회된다.
    category = category.strip()[:30] or "report"
    memo = (memo or "").strip()[:1000] or None

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

    # ── 문서 레코드를 'parsing' 상태로 즉시 생성 후 응답 ─────────────────
    # [비동기화 2026-07-05] 파싱은 여기서 하지 않고 백그라운드(_parse_and_ingest)로 넘긴다.
    # 대용량 PDF도 업로드 요청이 즉시 끝나 타임아웃·이벤트루프 블로킹을 피한다.
    # 프론트는 반환된 id로 /documents/{id}/status 를 폴링해 완료를 확인한다.
    file_type = ext.lstrip('.')
    doc = Document(
        filename      = file.filename,
        title         = None,
        saved_path    = save_path,
        file_type     = file_type,
        original_path = original_path,
        category      = category,
        memo          = memo,
        status        = "parsing",   # parsing → (백그라운드) → success | failed
        error         = None,
        page_count    = 0,
        has_flagged   = False,
        sha256        = sha256_hex,
        uploaded_by   = _current_user_email(request),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)  # auto-increment id 가져오기

    # 응답 후 백그라운드에서 파싱 → 상태 갱신 → JSON 저장 → 임베딩 색인
    background_tasks.add_task(_parse_and_ingest, doc.id, save_path, file.filename, ext)

    return {
        "id":       doc.id,
        "filename": file.filename,
        "status":   "parsing",
        "error":    None,
        "message":  "업로드 접수됨 — 파싱이 백그라운드에서 진행됩니다.",
    }


# GET /documents/statuses — 여러 문서 상태를 한 번에 조회 (배치 폴링용)
# [추가 2026-07-06] 프론트가 문서마다 개별 GET /documents/{id}/status 를 각자
# setInterval로 폴링하면, 동시 업로드 문서 수만큼 폴링 요청이 배로 늘어 SQLite
# 커넥션 풀(기본 15개)을 순간적으로 고갈시킨다(실측: 더미 49개 동시 업로드 시
# 폴링 요청 8,698회, QueuePool TimeoutError 발생). 여러 id를 한 세션의 IN 조회로
# 묶어 폴링 요청 수 자체를 줄인다. 응답 필드는 기존 개별 엔드포인트와 동일.
@app.get("/documents/statuses")
async def 문서_상태_배치(ids: str, db: AsyncSession = Depends(get_db)):
    # 숫자가 아닌 값(공백·오타 등)은 조용히 무시한다 — 폴링 입력값으로 500을 내지 않음
    id_list = []
    for part in ids.split(","):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
    if not id_list:
        return []

    docs = (await db.execute(
        select(Document).where(Document.id.in_(id_list))
    )).scalars().all()
    return [
        {
            "id":          d.id,
            "filename":    d.filename,
            "status":      d.status,        # parsing | success | failed
            "page_count":  d.page_count,
            "has_flagged": d.has_flagged,
            "error":       d.error,
            "category":              d.category,
            "category_ai_suggested": d.category_ai_suggested,
            "category_ai_checked":   d.category_ai_checked,
        }
        for d in docs
    ]


# GET /documents/{doc_id}/status — 업로드/재파싱 진행 상태 폴링용 (가벼운 응답)
# 프론트 업로드 화면은 배치 엔드포인트(/documents/statuses)를 사용한다.
# 이 개별 엔드포인트는 다른 화면(예: 상세보기 단건 확인)에서 쓸 수 있어 유지한다.
@app.get("/documents/{doc_id}/status")
async def 문서_상태(doc_id: int, db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    return {
        "id":          doc.id,
        "filename":    doc.filename,
        "status":      doc.status,        # parsing | success | failed
        "page_count":  doc.page_count,
        "has_flagged": doc.has_flagged,
        "error":       doc.error,
    }


# GET /files/{doc_id} — 원본 파일을 다운로드합니다
@app.get("/files/{doc_id}")
async def 파일_다운로드(doc_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    if not await _can_read_doc(db, doc_id, _current_user_email(request)):
        raise HTTPException(status_code=403, detail="이 문서를 읽을 권한이 없습니다")
    if not os.path.exists(doc.saved_path):
        raise HTTPException(status_code=404, detail="파일이 서버에 없습니다")

    return FileResponse(
        path=doc.saved_path,
        filename=doc.filename,
        media_type="application/octet-stream",
    )


# 문서 1건을 디스크(원본파일·파싱JSON·임베딩npz)와 DB(Chunk·turbovec·Document row)에서
# 완전히 삭제하는 공통 로직 — 관리자 단건/일괄 삭제, 본인 문서 삭제(/me/documents)가 모두 이걸 쓴다.
# db.delete(doc)까지만 하고 commit은 호출자가 한다(일괄 삭제는 여러 건을 모아 한 번에 commit).
async def _문서_완전삭제(doc: Document, db: AsyncSession):
    doc_id = doc.id

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

    _invalidate_doc_text(doc_id)
    _invalidate_bm25_cache()

    await db.delete(doc)


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
    await _문서_완전삭제(doc, db)
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
    names = await _resolve_names(db, [doc.uploaded_by for doc in docs])

    # 각 문서 정보를 딕셔너리 형태로 변환해서 돌려줍니다
    return [
        {
            "id":              doc.id,
            "filename":        doc.filename,
            "title":           doc.title,
            "memo":            doc.memo,
            "file_type":       doc.file_type,
            "original_path":   doc.original_path,
            "category":        doc.category,
            "status":          doc.status,
            "error":           doc.error,
            "page_count":      doc.page_count,
            "has_flagged":     doc.has_flagged,
            "view_count":      doc.view_count,
            "uploaded_by":     doc.uploaded_by,
            "uploaded_by_name": names.get(doc.uploaded_by),
            "uploaded_at":     str(doc.uploaded_at),
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

    # [비동기화 2026-07-05] 재파싱도 동기로 하지 않는다 — 'parsing'으로 표시하고
    # 백그라운드(_parse_and_ingest)로 넘겨 즉시 응답한다. (대용량 재시도 블로킹 방지)
    ext_retry = os.path.splitext(doc.saved_path)[1].lower()
    doc.status      = "parsing"
    doc.error       = None
    doc.has_flagged = False
    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(_parse_and_ingest, doc.id, doc.saved_path, doc.filename, ext_retry)

    return {
        "id":       doc.id,
        "filename": doc.filename,
        "status":   "parsing",
        "message":  "재파싱 접수됨 — 백그라운드에서 진행됩니다.",
    }


# GET /admin/documents/{doc_id}/detail — 문서 상세 보기 (파싱된 페이지 텍스트 포함)
@app.get("/admin/documents/{doc_id}/detail")
async def 문서_상세(doc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    # 상세보기 조회수 증가
    doc.view_count = (doc.view_count or 0) + 1
    await db.commit()

    # 저장된 파싱 결과 JSON을 읽어옵니다
    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    pages = []
    if os.path.exists(parsed_path):
        with open(parsed_path, "r", encoding="utf-8") as f:
            parse_data = json.load(f)
        pages = parse_data.get("pages", [])

    names = await _resolve_names(db, [doc.uploaded_by])

    return {
        "id":              doc.id,
        "filename":        doc.filename,
        "title":           doc.title,
        "memo":            doc.memo,
        "file_type":       doc.file_type,
        "original_path":   doc.original_path,
        "category":        doc.category,
        "status":          doc.status,
        "error":           doc.error,
        "page_count":      doc.page_count,
        "has_flagged":     doc.has_flagged,
        "view_count":      doc.view_count,
        "uploaded_by":     doc.uploaded_by,
        "uploaded_by_name": names.get(doc.uploaded_by),
        "uploaded_at":     str(doc.uploaded_at),
        "pages":           pages,
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
#       "category": str,    # spec / research / presentation / report / other
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


async def 실제_검색_실행(q: str, alpha: float, category, db: AsyncSession, uploaded_by=None, date_from=None, date_to=None) -> list:
    """BM25 + MiniLM 시맨틱 + RRF 하이브리드 검색.
    alpha=0.0 → BM25 전용 / alpha=1.0 → 시맨틱 전용 / alpha=0.5 → 균등 혼합
    """
    import re

    # ── 1. 문서 목록 로드 ────────────────────────────────────────────────────
    stmt = select(Document).where(Document.status == "success")
    if category:
        stmt = stmt.where(Document.category == category)
    if uploaded_by:
        stmt = stmt.where(Document.uploaded_by == uploaded_by)
    if date_from:
        stmt = stmt.where(Document.uploaded_at >= date_from)
    if date_to:
        # date_to는 "YYYY-MM-DD" 하루 단위로 받아 해당 날짜 끝까지 포함시킨다(다음날 0시 미만)
        try:
            date_to_exclusive = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            stmt = stmt.where(Document.uploaded_at < date_to_exclusive)
        except ValueError:
            pass
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
        # 키워드 없이 카테고리/파일형식 필터만으로 "둘러보기" — 관련도 순위가 없으므로
        # 최신 업로드순으로 나열하고 score=0으로 반환(프론트가 관련도 배지를 숨기는 신호로 씀)
        browsed = sorted(docs, key=lambda d: d.uploaded_at, reverse=True)
        results = []
        for d in browsed[:200]:
            # 마우스 오버 미리보기용 — 파싱된 본문 앞부분만 잘라서 보여준다(캐시됨, AI 호출 없음)
            info = _get_doc_text(d.id)
            preview = info["full_text"][:150].strip()
            results.append({
                "doc_id":      d.id,
                "filename":    d.filename,
                "category":    d.category,
                "file_type":   d.file_type,
                "pages":       d.page_count,
                "snippet":     d.title or d.filename,
                "content_preview": preview,
                "page_num":    0,
                "score":       0,
                "view_count":  d.view_count,
                "uploaded_by": d.uploaded_by,
                "uploaded_at": str(d.uploaded_at),
            })
        return results

    # ── 3. BM25 (alpha < 1.0) — 캐시된 전역 인덱스 사용(문서 변경 시에만 재구축) ──
    # [수정] 예전엔 여기서 매번 문서 본문을 다시 읽고 Kiwi로 재토큰화해 BM25Okapi를
    # 통째로 재구축했다(검색 1회에 0.5~0.7초, 문서 수에 비례해 악화). 이제
    # _ensure_bm25_cache()가 캐시가 비어있을 때(문서 추가·삭제·수정 직후)만 다시 만들고,
    # 캐시는 카테고리 구분 없이 전체 문서 대상이라 이 요청의 category 필터는
    # "이 청크의 문서가 이번 docs(카테고리 필터 적용됨)에 있는가"로 후필터링한다.
    bm25_scores: dict = {}
    if alpha < 1.0:
        await _ensure_bm25_cache(db)
        bm25 = _bm25_cache["bm25"]
        if bm25 is not None:
            # ③ 정본: 영↔한 기술용어 동의어 보강 (한국어 쿼리→영어 문서,
            # 영어 쿼리→한국어 문서 BM25 매칭 지원) + 쿼리 전용 토크나이저
            if any('가' <= c <= '힣' for c in q):
                bm25_q = _augment_ko_to_en(q)
            else:
                bm25_q = _augment_en_to_ko(q)
            q_tokens   = _tokenize_bm25(bm25_q, is_query=True)
            raw_scores = bm25.get_scores(q_tokens)
            doc_ids_in_scope = {d.id for d in docs}
            for i, did in enumerate(_bm25_cache["chunk_doc_ids"]):
                if did not in doc_ids_in_scope:
                    continue
                s = float(raw_scores[i])
                if s > 0 and s > bm25_scores.get(did, 0):
                    bm25_scores[did] = s
        else:
            # rank-bm25 자체를 못 불러온 극히 드문 경우의 TF 폴백
            for doc in docs:
                info = _get_doc_text(doc.id)
                combined = " ".join(filter(None, [doc.filename, doc.title or "", doc.memo or "", info["full_text"]]))
                hits = sum(combined.lower().count(t) for t in tokens)
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

        # 시맨틱 매칭 청크를 우선 스니펫으로 사용 (한국어 쿼리 → 영어 문서 대응)
        if doc.id in sem_chunks and sem_scores.get(doc.id, 0) >= SEM_THR:
            snippet  = _extract_snippet_multi(sem_chunks[doc.id], tokens)
            page_num = sem_pages.get(doc.id, 0)
        else:
            # 본문은 최종 후보(≤50개)에 대해서만 필요할 때 lazy 로드 — 카테고리에
            # 맞는 문서 전부를 미리 읽던 예전 방식보다 훨씬 적은 파일만 연다.
            info = _get_doc_text(doc.id)
            full_text = info["full_text"]
            snippet  = (
                _extract_snippet_multi(full_text, tokens) if full_text.strip()
                else (doc.title or doc.filename)
            )
            page_num = _best_page_for_tokens(info["pages"], tokens)

        scored.append({
            "doc_id":      doc.id,
            "filename":    doc.filename,
            "category":    doc.category,
            "pages":       doc.page_count,
            "snippet":     snippet,
            "content_preview": snippet,
            "page_num":    page_num,
            "score":       rrf,
            "view_count":  doc.view_count,
            "uploaded_by": doc.uploaded_by,
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
    request: Request,
    q: str = Query(..., description="검색어"),
    alpha: float = Query(0.5, ge=0.0, le=1.0, description="의미검색 비중 (0~1)"),
    category: str = Query(None, description="카테고리 필터 (spec/research/presentation/report/other)"),
    file_type: str = Query(None, description="파일 형식 필터 (pdf/docx/pptx/xlsx/hwp 등)"),
    uploaded_by: str = Query(None, description="작성자(업로더) 이메일 필터"),
    date_from: str = Query(None, description="업로드 날짜 범위 시작 (YYYY-MM-DD)"),
    date_to: str = Query(None, description="업로드 날짜 범위 끝 (YYYY-MM-DD, 해당일 포함)"),
    db: AsyncSession = Depends(get_db),
):
    results = await 실제_검색_실행(q, alpha, category, db, uploaded_by=uploaded_by, date_from=date_from, date_to=date_to)

    # 문서별 읽기 권한 필터 — 권한이 걸린 문서는 허가된 사람(+관리자)에게만 노출
    if results:
        readable = await _filter_readable_ids(db, [r["doc_id"] for r in results], _current_user_email(request))
        results = [r for r in results if r["doc_id"] in readable]

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

        names = await _resolve_names(db, [r.get("uploaded_by") for r in results])
        for r in results:
            r["uploaded_by_name"] = names.get(r.get("uploaded_by"))

    # 파일 형식 필터 (실제_검색_실행은 건드리지 않고 여기서 후처리)
    if file_type:
        results = [r for r in results if r.get("file_type") == file_type]

    # 검색 기록 저장
    log = SearchLog(query=q, alpha=alpha, result_count=len(results))
    db.add(log)
    await db.commit()

    return {"query": q, "alpha": alpha, "category": category, "file_type": file_type,
            "uploaded_by": uploaded_by, "date_from": date_from, "date_to": date_to,
            "total": len(results), "results": results}


# GET /uploaders — 검색 화면의 "작성자" 필터 드롭다운용, 실제로 문서를 올린 적 있는
# 이메일 목록만 이름과 함께 반환한다 (anonymous 제외).
@app.get("/uploaders")
async def 작성자_목록(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Document.uploaded_by).where(
            Document.uploaded_by != None, Document.uploaded_by != "anonymous"
        ).distinct()
    )).scalars().all()
    names = await _resolve_names(db, rows)
    uploaders = [{"email": email, "name": names.get(email) or email} for email in rows]
    uploaders.sort(key=lambda u: u["name"])
    return uploaders


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
    category: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    category = category.strip()[:30]
    if not category:
        raise HTTPException(status_code=400, detail="카테고리 이름을 입력하세요")
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
    category: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    category = category.strip()[:30]
    if not category:
        raise HTTPException(status_code=400, detail="카테고리 이름을 입력하세요")
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
            await _문서_완전삭제(doc, db)
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
    _invalidate_bm25_cache()  # memo는 BM25 코퍼스(combined 텍스트)에 들어가는 내용이라 무효화 필요
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
    _invalidate_bm25_cache()  # title도 BM25 코퍼스(combined 텍스트)에 들어감
    return {"id": doc_id, "title": doc.title}


# ── 내 정보(마이페이지): 로그인한 사람이 자기가 올린 문서를 직접 수정·삭제 ──────────
# 관리자 전용 /admin/* 와 별개 경로 — 로그인만 되어 있으면 접근 가능하되, 본인이 올린
# 문서가 아니면(관리자가 아닌 한) 403으로 막는다.

def _소유자_또는_관리자(doc: Document, user_email: str):
    if doc.uploaded_by != user_email and not _is_admin(user_email):
        raise HTTPException(status_code=403, detail="본인이 업로드한 문서만 수정·삭제할 수 있습니다")


# GET /me/documents — 내가 업로드한 문서 목록
@app.get("/me/documents")
async def 내_문서_목록(request: Request, db: AsyncSession = Depends(get_db)):
    user_email = _current_user_email(request)
    result = await db.execute(
        select(Document).where(Document.uploaded_by == user_email).order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id":          doc.id,
            "filename":    doc.filename,
            "title":       doc.title,
            "memo":        doc.memo,
            "category":    doc.category,
            "file_type":   doc.file_type,
            "status":      doc.status,
            "view_count":  doc.view_count,
            "uploaded_at": str(doc.uploaded_at),
        }
        for doc in docs
    ]


# PATCH /me/documents/{doc_id} — 제목·카테고리·메모를 한 번에 수정(모두 선택 항목)
@app.patch("/me/documents/{doc_id}")
async def 내_문서_수정(
    doc_id: int,
    request: Request,
    title:    str | None = Body(None, embed=True),
    category: str | None = Body(None, embed=True),
    memo:     str | None = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    _소유자_또는_관리자(doc, _current_user_email(request))

    if title is not None:
        doc.title = title.strip() or None
    if category is not None:
        category = category.strip()[:30]
        if category:
            doc.category = category
    if memo is not None:
        doc.memo = memo.strip()[:1000] or None

    await db.commit()
    if title is not None or memo is not None:
        _invalidate_bm25_cache()  # title/memo가 BM25 코퍼스에 들어가므로 바뀌면 무효화
    return {"id": doc.id, "title": doc.title, "category": doc.category, "memo": doc.memo}


# DELETE /me/documents/{doc_id} — 내가 올린 문서 삭제(관리자 단건 삭제와 동일한 정리 로직 사용)
@app.delete("/me/documents/{doc_id}")
async def 내_문서_삭제(doc_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")
    _소유자_또는_관리자(doc, _current_user_email(request))

    filename = doc.filename
    await _문서_완전삭제(doc, db)
    await db.commit()
    return {"message": f"'{filename}' 문서가 삭제됐습니다", "id": doc_id}


# ── AI 답변 (Ollama + OLLAMA_MODEL) ─────────────────────────────────────────

# Qwen2.5는 한국어 프롬프트에도 가끔 한자(중국어)를 섞어 내는 알려진 모델 특성이 있다
# (https://github.com/QwenLM/Qwen/issues/543). 프롬프트 지시만으로는 완전히 막을 수
# 없어서, 응답에 한자가 섞이면 온도를 낮춰 재시도하고, 그래도 남으면 강제로 지운다.
_CJK_HAN_RE = _re.compile(r'[一-鿿]')

def _ollama_generate_korean(prompt: str, max_retries: int = 2) -> dict:
    import urllib.request, json as _json
    data = None
    for _attempt in range(max_retries + 1):
        payload = _json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = _json.loads(r.read())
        if not _CJK_HAN_RE.search(data.get("response", "")):
            return data
    data["response"] = _CJK_HAN_RE.sub("", data.get("response", ""))
    return data


# ── 업로드 시 AI 자동 카테고리 분류 (Ollama + OLLAMA_MODEL) ──────────────────
# 파싱이 끝나 실제 본문이 확보된 시점에만 호출한다 — 업로드 직후(파싱 전)엔 분류할
# 내용이 없다. 실패(Ollama 미가동, 응답이 넷 중 무엇과도 안 맞음 등)하면 None을
# 반환하고, 호출부는 업로드 시 사용자가 고른 카테고리를 그대로 둔다.
_CATEGORY_OPTIONS = ["spec", "research", "presentation", "report"]
_CATEGORY_PROMPT_DESC = (
    "- spec: 사양서 (제품/기술 스펙, 요구사항 정의서)\n"
    "- research: 연구자료 (조사·분석 자료, 논문, 학습자료)\n"
    "- presentation: 발표자료 (슬라이드, 데모/발표용 자료)\n"
    "- report: 보고서 (진행 상황 보고, 회의록, 그 외 일반 문서)"
)

def _ollama_classify_category(text: str) -> str | None:
    prompt = (
        "다음은 방금 업로드된 문서의 본문 일부입니다. 아래 네 가지 카테고리 중 이 문서에 "
        f"가장 알맞은 것 하나를 골라 그 영문 코드로만 답하세요.\n{_CATEGORY_PROMPT_DESC}\n\n"
        f"문서 내용:\n{text[:4000]}\n\n"
        "다른 설명 없이 spec / research / presentation / report 중 하나의 단어로만 답하세요."
    )
    try:
        data = _ollama_generate_korean(prompt, max_retries=0)
    except Exception:
        return None
    raw = data.get("response", "").strip().lower()
    for opt in _CATEGORY_OPTIONS:
        if opt in raw:
            return opt
    return None


@app.get("/ask/status")
async def ai_status():
    """Ollama 서버 가용 여부 및 OLLAMA_MODEL(설치된 모델) 확인."""
    import urllib.request, json as _json
    def _check():
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            return _json.loads(r.read())
    try:
        data   = await asyncio.to_thread(_check)
        models = [m["name"] for m in data.get("models", [])]
        # "gemma" 고정 문자열이 아니라 OLLAMA_MODEL의 모델명(태그 앞부분)이 실제
        # 설치돼 있는지로 판단 — 모델을 qwen 등으로 바꿔도 가용성 체크가 맞게 동작하게 함.
        target_name = OLLAMA_MODEL.split(":")[0].lower()
        ok = any(m.split(":")[0].lower() == target_name for m in models)
        return {"available": ok, "models": models, "target": OLLAMA_MODEL}
    except Exception:
        return {"available": False, "models": [], "target": OLLAMA_MODEL}


@app.post("/ask")
async def ai_ask(
    q:        str       = Body(..., embed=True),
    snippets: list[str] = Body(..., embed=True),
):
    """검색 결과 스니펫을 컨텍스트로 로컬 Ollama 모델에게 질문합니다."""
    context_text = "\n\n".join(
        f"[문서 {i+1}]\n{s.strip()}" for i, s in enumerate(snippets[:5]) if s.strip()
    )
    prompt = (
        "다음은 사내 문서에서 검색한 관련 내용입니다.\n\n"
        f"{context_text}\n\n"
        f"질문: {q}\n\n"
        "위 문서 내용을 바탕으로 질문에 간결하게 답해 주세요. "
        "문서에 없는 내용은 추측하지 말고 '문서에서 찾을 수 없습니다'라고 답하세요. "
        "반드시 한국어로만 답변하세요(중국어나 다른 언어를 절대 섞지 마세요)."
    )
    try:
        data = await asyncio.to_thread(_ollama_generate_korean, prompt)
        return {"answer": data.get("response", ""), "model": data.get("model", OLLAMA_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"AI 답변 생성 실패: {e}")


# GET /documents/{doc_id}/summary — 문서 상세 팝업에서 "이 문서 요약해줘" 용도.
# /ask와 달리 검색 스니펫이 아니라 문서 본문 전체(파싱 결과 JSON)를 컨텍스트로 쓴다.
@app.get("/documents/{doc_id}/summary")
async def 문서_요약(doc_id: int, db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    parsed_path = os.path.join(PARSED_DIR, f"{doc_id}.json")
    if not os.path.exists(parsed_path):
        raise HTTPException(status_code=400, detail="문서 본문을 찾을 수 없습니다 (파싱 결과 없음)")
    with open(parsed_path, "r", encoding="utf-8") as f:
        pd = json.load(f)
    full_text = "\n".join(p.get("text", "") for p in pd.get("pages", []))
    if not full_text.strip():
        raise HTTPException(status_code=400, detail="요약할 본문 텍스트가 없습니다")

    # 컨텍스트 길이 안전장치 — 매우 긴 문서(수백~수천 페이지)는 앞부분만 사용.
    # qwen2.5:7b 컨텍스트(32K 토큰)엔 여유가 있지만, 그래도 상한을 둬 응답 지연을 막는다.
    MAX_CHARS = 12000
    truncated = full_text[:MAX_CHARS]
    truncated_note = "\n\n(문서가 길어 앞부분만 사용했습니다)" if len(full_text) > MAX_CHARS else ""

    # [수정] Qwen2.5는 한국어 프롬프트에도 가끔 중국어 토큰을 섞어 내는 걸로 알려진
    # 모델 특성이 있어(https://github.com/QwenLM/Qwen/issues/543), 답변 언어를
    # 명시적으로 지시해 이탈을 줄인다. 요약도 상세 요약이 아니라 "대략 무슨 내용인지"
    # 감만 잡을 수 있는 1~2문장으로 짧게 바꿈.
    prompt = (
        f"다음은 '{doc.title or doc.filename}' 문서의 본문입니다.\n\n"
        f"{truncated}\n\n"
        "이 문서가 대략 어떤 내용인지 1~2문장으로 간단히 알려주세요. "
        "반드시 한국어로만 답변하세요(중국어나 다른 언어를 절대 섞지 마세요)."
    )

    try:
        data = await asyncio.to_thread(_ollama_generate_korean, prompt)
        return {
            "summary": data.get("response", "") + truncated_note,
            "model": data.get("model", OLLAMA_MODEL),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"요약 생성 실패: {e}")


# ── 프론트 정적 서빙 (배포용 통합 서빙) ──────────────────────────────────────
# Dockerfile이 React 빌드 산출물(dist/)을 backend/static/에 복사해 넣는다.
# 이 mount는 "파일 맨 끝"에 있어야 한다 — FastAPI는 등록 순서대로 매칭하므로
# 위의 API 라우트들(/upload, /search, /admin/* 등)이 항상 먼저 잡히고,
# 그 외 경로(/assets/*.js 등)만 정적 파일로 처리된다.
# 로컬 개발처럼 static/ 폴더가 없으면 아무것도 하지 않는다 (기존 동작 유지).
if os.path.isdir(_STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")

    # SPA 클라이언트 라우팅 폴백 — /admin처럼 실제 파일이 없는 경로를 직접 주소로 열거나
    # 새로고침하면 StaticFiles가 404를 내는데, 브라우저 내비게이션(GET + Accept: text/html)이면
    # index.html을 대신 돌려줘서 프론트의 pathname 기반 라우팅(main.jsx)이 처리하게 한다.
    @app.exception_handler(StarletteHTTPException)
    async def _spa_fallback(request: Request, exc: StarletteHTTPException):
        if (
            exc.status_code == 404
            and request.method == "GET"
            and "text/html" in request.headers.get("accept", "")
        ):
            index_path = os.path.join(_STATIC_DIR, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
        raise exc
