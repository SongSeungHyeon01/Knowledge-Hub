# FastAPI 서버 메인 파일
# 이 파일을 실행하면 백엔드 서버가 켜집니다

import asyncio
import io
import os
import json
import hashlib
import secrets
import shutil
import subprocess
import tarfile
import time
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query, Body, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware  # CORS 설정용
import itsdangerous
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete, update as sa_update
from datetime import datetime, timedelta

from sqlalchemy import text

from database import engine, get_db, Base, AsyncSessionLocal
from models import Document, SearchLog, Chunk, Bookmark, DocumentPermission, User, Comment, AdminEmail, RevokedSession, Notification, DeletionLog
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from core.parser import parse_pdf, count_pdf_pages, HARD_PAGE_LIMIT   # 백엔드 배포 정본 파서 — 최신 parser 브랜치(parser/parser.py) 기준, import only
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
)

# ── 경로 설정 — env var 우선, 없으면 코드 파일 기준 상대 경로 ─────────────────
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get("DATA_DIR", os.path.join(_BASE_DIR, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
PARSED_DIR = os.path.join(DATA_DIR, "parsed")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")   # DB 백업 파일 저장 위치 — Railway Volume에 위치해 DB 자체와 물리적으로 분리됨
# 실시간 파일 백업 미러 (2026-07-23) — 업로드 즉시 복사, 삭제 시 함께 제거된다.
# 매일 도는 _run_backup()의 전체 스냅샷(최근 14회 보관)과 달리, 이 미러는 항상
# "현재 살아있는 문서 상태"만 반영해서 용량이 무한정 쌓이지 않는다. 다만 이 미러는
# 실수로 문서를 삭제한 경우까지는 복구해주지 못한다 — 그 안전망은 여전히 매일 스냅샷
# (14회 보관)이 담당한다.
MIRROR_DIR        = os.path.join(DATA_DIR, "backup_mirror")
MIRROR_UPLOAD_DIR = os.path.join(MIRROR_DIR, "uploads")
MIRROR_PARSED_DIR = os.path.join(MIRROR_DIR, "parsed")
# 문서를 삭제해도 미러 사본을 바로 지우지 않고 여기(trash)로 옮겨 며칠간 보관한다
# (2026-07-23) — 실수로 지운 문서를 이 보관기간 안에는 그대로 복구할 수 있게 하기 위함.
# 보관기간이 지난 뒤에는 _sweep_mirror_trash()가 주기적으로 영구 삭제한다.
MIRROR_TRASH_DIR        = os.path.join(MIRROR_DIR, "trash")
MIRROR_TRASH_UPLOAD_DIR = os.path.join(MIRROR_TRASH_DIR, "uploads")
MIRROR_TRASH_PARSED_DIR = os.path.join(MIRROR_TRASH_DIR, "parsed")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PARSED_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(MIRROR_UPLOAD_DIR, exist_ok=True)
os.makedirs(MIRROR_PARSED_DIR, exist_ok=True)
os.makedirs(MIRROR_TRASH_UPLOAD_DIR, exist_ok=True)
os.makedirs(MIRROR_TRASH_PARSED_DIR, exist_ok=True)

# 지원 파일 형식 목록
# (png/jpg 단독 이미지는 미지원으로 정리 — 2026-07-04 결정. 문서 속 이미지는
#  PDF OCR 경로·DOCX embedded 이미지 OCR로 이미 처리된다)
SUPPORTED_EXTENSIONS = {
    '.pdf', '.docx', '.pptx', '.ppt', '.xlsx', '.xls',
    '.hwp', '.hwpx', '.txt', '.md',
}

# 파일 크기 상한 (500MB — OOM 방지 1MB 스트리밍)
MAX_UPLOAD_SIZE = 500 * 1024 * 1024

# ── STEP 3: 비-PDF 형식 크기 상한 (2026-07-22, 대용량 문서 처리방어) ──────────────
# DOCX/HWP는 페이지 루프 자체가 없고(문서 전체를 한 번에 처리), XLSX/HWPX는
# 시트·섹션 수를 페이지처럼 세도 실제 처리 비용과 상관관계가 약해(예: 시트 1개에
# 수백만 행) 페이지 수 가드레일(SOFT/HARD_PAGE_LIMIT, core/parser.py)을 의미 있게
# 적용할 수 없다(STEP 3-B 조사 결론). 이 형식들은 파일 크기만으로 방어한다 — PDF
# 전용 전역 상한(MAX_UPLOAD_SIZE)보다 더 타이트한 값을 별도로 둔다.
NON_PDF_MAX_SIZE_MB = int(os.environ.get("NON_PDF_MAX_SIZE_MB", "100"))
NON_PDF_MAX_SIZE = NON_PDF_MAX_SIZE_MB * 1024 * 1024


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


# ── 실시간 파일 백업 미러 (2026-07-23) ────────────────────────────────────
# 하루 한 번 도는 전체 스냅샷(_run_backup)만으로는 업로드 직후 ~24시간 동안은
# 그 파일이 백업본에 전혀 없는 공백이 생긴다. 업로드되는 "그 순간" 바로
# 미러 디렉터리로 복사해 공백을 없앤다. 문서를 삭제하면 미러에서도 같이
# 지워서, 이미 지운 문서의 사본이 미러에 계속 쌓이는 일이 없게 한다
# (하루 스냅샷은 최근 14회만 보관하므로 그쪽도 무한정 쌓이지 않는다 — 두 메커니즘 모두 용량이 유계).
def _mirror_upload(save_path: str):
    """업로드 원본 파일을 실시간 미러로 복사한다. UPLOAD_DIR 기준 상대 경로를 그대로 유지한다."""
    rel = os.path.relpath(save_path, UPLOAD_DIR)
    dest = os.path.join(MIRROR_UPLOAD_DIR, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(save_path, dest)


def _mirror_upload_safe(save_path: str, filename: str):
    """[수정 2026-07-25] 업로드 응답 이후(BackgroundTasks)로 미룬 미러 복사 — 실패해도
    업로드 자체는 이미 성공 응답이 나간 뒤라 로그만 남긴다. 큰 파일일수록 디스크에
    사실상 두 번(원본 저장 + 미러 복사) 쓰는 시간을 응답 전에 전부 기다리게 했던
    문제를 없애 업로드 응답 속도를 개선한다."""
    try:
        _mirror_upload(save_path)
    except Exception as e:
        print(f"[mirror] {filename}: 실시간 백업 미러 복사 실패 — {e}")


def _mirror_parsed(doc_id: int):
    """파싱 결과(JSON)와 임베딩(npz)을 실시간 미러로 복사한다. 존재하는 파일만 복사한다."""
    for fname in (f"{doc_id}.json", f"{doc_id}_emb.npz"):
        src = os.path.join(PARSED_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(MIRROR_PARSED_DIR, fname))


_MIRROR_TRASH_RETENTION_DAYS      = int(os.environ.get("MIRROR_TRASH_RETENTION_DAYS", "3"))       # trash 보관 일수
_MIRROR_TRASH_SWEEP_INTERVAL_SEC  = int(os.environ.get("MIRROR_TRASH_SWEEP_INTERVAL_SEC", str(6 * 3600)))  # 정리 주기(기본 6시간)

def _move_to_trash(src: str, dest: str):
    """dest에 같은 이름의 예전 trash 항목이 있으면 지우고 옮긴다. mtime을 지금 시각으로 찍어
    보관기간 계산 기준(삭제된 시점)으로 삼는다."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(src, dest)
    os.utime(dest, None)


def _mirror_remove(doc_id: int, saved_path: str):
    """문서 삭제 시 미러 사본을 바로 지우지 않고 trash로 옮긴다 — 보관기간 동안은
    실수로 지운 문서를 복구할 수 있고, 기간이 지나면 _sweep_mirror_trash()가 영구 삭제한다."""
    rel = os.path.relpath(saved_path, UPLOAD_DIR)
    mirror_upload_path = os.path.join(MIRROR_UPLOAD_DIR, rel)
    if os.path.exists(mirror_upload_path):
        _move_to_trash(mirror_upload_path, os.path.join(MIRROR_TRASH_UPLOAD_DIR, rel))

    for fname in (f"{doc_id}.json", f"{doc_id}_emb.npz"):
        src = os.path.join(MIRROR_PARSED_DIR, fname)
        if os.path.exists(src):
            _move_to_trash(src, os.path.join(MIRROR_TRASH_PARSED_DIR, fname))


def _sweep_mirror_trash() -> int:
    """trash에서 보관기간(_MIRROR_TRASH_RETENTION_DAYS)이 지난 파일만 영구 삭제한다.
    반환값은 삭제한 파일 개수."""
    cutoff = time.time() - _MIRROR_TRASH_RETENTION_DAYS * 86400
    removed = 0
    for root in (MIRROR_TRASH_UPLOAD_DIR, MIRROR_TRASH_PARSED_DIR):
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        removed += 1
                except OSError:
                    pass
        # original_path로 인한 하위 폴더 구조가 trash에도 그대로 생기므로, 빈 폴더는 정리한다
        for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
            if dirpath != root and not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    return removed


async def _scheduled_mirror_trash_sweep_task():
    while True:
        try:
            await asyncio.sleep(_MIRROR_TRASH_SWEEP_INTERVAL_SEC)
            removed = await asyncio.to_thread(_sweep_mirror_trash)
            if removed:
                print(f"[mirror] trash 정리: {removed}개 파일 영구 삭제 (보관기간 {_MIRROR_TRASH_RETENTION_DAYS}일 경과)")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[mirror] trash 정리 실패: {e}")


# ── 삭제 보고서(DeletionLog) 보관 정리 (2026-07-24) ──────────────────────────
# 관리자가 문서를 삭제할 때마다 감사 로그가 쌓이는데, 무한정 누적되면 안 되므로
# 일정 기간(기본 3개월)이 지난 행은 주기적으로 영구 삭제한다.
_DELETION_LOG_RETENTION_DAYS   = int(os.environ.get("DELETION_LOG_RETENTION_DAYS", "90"))          # 보관 기간(기본 90일 ≈ 3개월)
_DELETION_LOG_SWEEP_INTERVAL_SEC = int(os.environ.get("DELETION_LOG_SWEEP_INTERVAL_SEC", str(24 * 3600)))  # 정리 주기(기본 24시간)


async def _sweep_deletion_logs():
    cutoff = datetime.now() - timedelta(days=_DELETION_LOG_RETENTION_DAYS)
    async with AsyncSessionLocal() as sess:
        result = await sess.execute(sa_delete(DeletionLog).where(DeletionLog.deleted_at < cutoff))
        await sess.commit()
        return result.rowcount


async def _scheduled_deletion_log_sweep_task():
    while True:
        try:
            await asyncio.sleep(_DELETION_LOG_SWEEP_INTERVAL_SEC)
            removed = await _sweep_deletion_logs()
            if removed:
                print(f"[deletion-log] 삭제 보고서 {removed}건 정리 (보관기간 {_DELETION_LOG_RETENTION_DAYS}일 경과)")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[deletion-log] 정리 실패: {e}")


# ── 세션 블랙리스트(RevokedSession) 정리 (2026-07-25) ─────────────────────────
# [수정] 원래는 lifespan 시작 시 딱 한 번만 청소했다 — 서버를 오래 재시작하지 않으면
# SESSION_MAX_AGE(7일)가 지나 더 이상 조회할 필요 없는 항목이 계속 쌓이는 문제가 있었다.
# 다른 3개 정리 태스크(백업·미러trash·삭제로그)와 같은 반복 백그라운드 패턴으로 통일한다.
_REVOKED_SESSION_SWEEP_INTERVAL_SEC = int(os.environ.get("REVOKED_SESSION_SWEEP_INTERVAL_SEC", str(24 * 3600)))  # 정리 주기(기본 24시간)


async def _sweep_revoked_sessions():
    cutoff = datetime.now() - timedelta(seconds=SESSION_MAX_AGE)
    async with AsyncSessionLocal() as sess:
        result = await sess.execute(sa_delete(RevokedSession).where(RevokedSession.revoked_at < cutoff))
        await sess.commit()
        return result.rowcount


async def _scheduled_revoked_session_sweep_task():
    while True:
        try:
            await asyncio.sleep(_REVOKED_SESSION_SWEEP_INTERVAL_SEC)
            removed = await _sweep_revoked_sessions()
            if removed:
                print(f"[session] 만료된 세션 블랙리스트 {removed}건 정리")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[session] 세션 블랙리스트 정리 실패: {e}")


# ── DB 백업 (2026-07-23) ──────────────────────────────────────────────────
# 지금까지 백업 수단이 전혀 없었다 — 로컬 SQLite 파일이 지워지거나 프로덕션
# PostgreSQL이 손상되면 복구할 방법이 없는 상태였다. Railway Volume(DATA_DIR)에
# 주기적으로 덤프를 남겨, DB 자체와는 물리적으로 분리된 곳에 최근 상태를 보관한다.
_BACKUP_INTERVAL_SEC = int(os.environ.get("BACKUP_INTERVAL_SEC", str(24 * 3600)))  # 기본 24시간
_BACKUP_RETENTION     = int(os.environ.get("BACKUP_RETENTION_COUNT", "14"))         # 최근 14개만 보관
_RAW_DATABASE_URL     = os.environ.get("DATABASE_URL", "")  # database.py가 asyncpg용으로 바꾸기 전 원본(pg_dump는 이 형식을 그대로 이해함)


def _run_backup() -> dict:
    """DB + 원본 파일(uploads·parsed)을 둘 다 백업하고, 오래된 백업은 정리한다.
    DB만 복원해봐야 문서 row가 가리키는 실제 파일이 없으면 무용지물이라 반드시 같이 뜬다.
    반환값: {"db": DB 백업 파일명, "files": 파일 백업 파일명}."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) DB 백업
    if _RAW_DATABASE_URL:
        # PostgreSQL — pg_dump로 SQL 덤프 생성 (시스템에 postgresql-client 설치 필요, Dockerfile 참고)
        db_filename = f"km_backup_{timestamp}.sql"
        db_filepath = os.path.join(BACKUP_DIR, db_filename)
        result = subprocess.run(
            ["pg_dump", _RAW_DATABASE_URL, "-f", db_filepath],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pg_dump 실패: {result.stderr}")
    else:
        # SQLite — 파일 자체를 그대로 복사 (database.py와 동일한 경로 규칙)
        db_filename = f"km_backup_{timestamp}.db"
        db_filepath = os.path.join(BACKUP_DIR, db_filename)
        sqlite_path = os.path.join(_BASE_DIR, "km.db")
        if not os.path.exists(sqlite_path):
            raise RuntimeError(f"SQLite 파일을 찾을 수 없습니다: {sqlite_path}")
        shutil.copy2(sqlite_path, db_filepath)

    # 2) 파일 백업 — 원본 업로드 파일(uploads) + 파싱 결과·임베딩(parsed)을 tar.gz 하나로 묶는다.
    # parsed도 같이 담는 이유: 복원 후 OCR·임베딩을 처음부터 다시 돌리려면 시간이 오래 걸려서,
    # 이미 계산해둔 결과까지 같이 보관해두는 편이 실제 복구 상황에서 훨씬 빠르다.
    files_filename = f"km_files_{timestamp}.tar.gz"
    files_filepath = os.path.join(BACKUP_DIR, files_filename)
    with tarfile.open(files_filepath, "w:gz") as tar:
        if os.path.isdir(UPLOAD_DIR):
            tar.add(UPLOAD_DIR, arcname="uploads")
        if os.path.isdir(PARSED_DIR):
            tar.add(PARSED_DIR, arcname="parsed")

    # 보관 개수(_BACKUP_RETENTION)를 넘는 오래된 백업은 종류별로 각각 정리
    for prefix in ("km_backup_", "km_files_"):
        old_files = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix)),
            reverse=True,
        )
        for old in old_files[_BACKUP_RETENTION:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass

    return {"db": db_filename, "files": files_filename}


async def _scheduled_backup_task():
    """주기적으로 자동 백업을 실행하는 백그라운드 태스크. 스윕 태스크와 같은 방식으로
    예외가 나도 태스크 자체는 죽지 않게 방어한다."""
    while True:
        try:
            await asyncio.sleep(_BACKUP_INTERVAL_SEC)
            result = await asyncio.to_thread(_run_backup)
            print(f"[backup] 자동 백업 완료: DB={result['db']}, 파일={result['files']}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[backup] 자동 백업 실패: {e}")


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

    # 세션 블랙리스트 청소 — 서버 시작 시 한 번 정리하고, 이후로는 반복 백그라운드
    # 태스크(_scheduled_revoked_session_sweep_task)가 주기적으로 계속 정리한다.
    removed = await _sweep_revoked_sessions()
    if removed:
        print(f"[startup] 만료된 세션 블랙리스트 {removed}건 정리")

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
        "ALTER TABLE documents ADD COLUMN department VARCHAR",
        "ALTER TABLE users ADD COLUMN department VARCHAR",
        "ALTER TABLE search_logs ADD COLUMN user_email VARCHAR",
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

    backup_task = asyncio.create_task(_scheduled_backup_task())
    print(f"[startup] DB 자동 백업 태스크 시작 (주기 {_BACKUP_INTERVAL_SEC}s, 보관 {_BACKUP_RETENTION}개)")

    mirror_trash_task = asyncio.create_task(_scheduled_mirror_trash_sweep_task())
    print(f"[startup] 미러 trash 정리 태스크 시작 (주기 {_MIRROR_TRASH_SWEEP_INTERVAL_SEC}s, 보관 {_MIRROR_TRASH_RETENTION_DAYS}일)")

    deletion_log_sweep_task = asyncio.create_task(_scheduled_deletion_log_sweep_task())
    print(f"[startup] 삭제 보고서 정리 태스크 시작 (주기 {_DELETION_LOG_SWEEP_INTERVAL_SEC}s, 보관 {_DELETION_LOG_RETENTION_DAYS}일)")

    revoked_session_sweep_task = asyncio.create_task(_scheduled_revoked_session_sweep_task())
    print(f"[startup] 세션 블랙리스트 정리 태스크 시작 (주기 {_REVOKED_SESSION_SWEEP_INTERVAL_SEC}s)")

    yield

    sweep_task.cancel()
    backup_task.cancel()
    mirror_trash_task.cancel()
    deletion_log_sweep_task.cancel()
    revoked_session_sweep_task.cancel()
    for t in (sweep_task, backup_task, mirror_trash_task, deletion_log_sweep_task, revoked_session_sweep_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    print("[shutdown] 정체 문서 스윕·백업·미러 trash·삭제 보고서·세션 블랙리스트 정리 태스크 종료")


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

    두 가지 제한이 함께 적용된다:
    - 부서 제한(기본): 문서에 department가 지정돼 있으면, 그 부서 소속만 열람 가능.
      department가 비어있는 문서(부서 미지정 유저가 올렸거나 이 기능 도입 전 문서)는 이 제한이 없다.
    - 개별 허가(DocumentPermission, 예외 추가): 부서가 달라도(또는 부서 미지정 문서에 개별
      허가만 걸어둔 예전 방식 그대로) 명시적으로 허가된 사람은 항상 열람 가능.
    """
    if not AUTH_ENABLED or _is_admin(user_email) or not doc_ids:
        return set(doc_ids)

    user_dept = (await db.execute(
        select(User.department).where(User.email == user_email)
    )).scalar_one_or_none()

    doc_rows = (await db.execute(
        select(Document.id, Document.department).where(Document.id.in_(doc_ids))
    )).all()

    granted_ids = set((await db.execute(
        select(DocumentPermission.doc_id).where(
            DocumentPermission.doc_id.in_(doc_ids), DocumentPermission.user_email == user_email
        )
    )).scalars().all())
    restricted_ids = set((await db.execute(
        select(DocumentPermission.doc_id).where(DocumentPermission.doc_id.in_(doc_ids)).distinct()
    )).scalars().all())

    readable = set()
    for doc_id, doc_dept in doc_rows:
        if doc_dept:
            if doc_dept == user_dept or doc_id in granted_ids:
                readable.add(doc_id)
        else:
            # 부서 미지정 문서 — 기존 방식 그대로: 개별 허가가 하나도 없으면 공개, 있으면 허가된 사람만
            if doc_id not in restricted_ids or doc_id in granted_ids:
                readable.add(doc_id)
    return readable


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
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    return {
        "email": email,
        "is_admin": _is_admin(email),
        "name": user.name if user else None,
        "picture": user.picture if user else None,
        "department": user.department if user else None,
    }


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
# 프론트가 "누가·어디에·뭐라고"를 각각 따로 표시할 수 있도록, 미리 조합해둔 message
# 문자열 대신 댓글 원본(Comment)까지 조인해 필드를 나눠서 돌려준다. 알림이 만들어진
# 뒤 댓글이 삭제됐을 수도 있어 outer join(isouter) — 그 경우 comment_content는 None.
@app.get("/notifications")
async def 알림_목록(request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    rows = (await db.execute(
        select(Notification, Document.title, Document.filename, Comment.content, Comment.user_email)
        .join(Document, Document.id == Notification.doc_id)
        .outerjoin(Comment, Comment.id == Notification.comment_id)
        .where(Notification.recipient == email)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )).all()
    names = await _resolve_names(db, [row.user_email for row in rows])
    return [
        {
            "id": row.Notification.id,
            "doc_id": row.Notification.doc_id,
            "doc_label": row.title or row.filename,
            "is_read": row.Notification.is_read,
            "created_at": str(row.Notification.created_at),
            "commenter_email": row.user_email,
            "commenter_name": names.get(row.user_email, row.user_email),
            "comment_content": row.content,
            "message": row.Notification.message,  # 옛 알림 등 댓글이 삭제된 경우의 대체 표시용
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


@app.delete("/notifications/{notification_id}")
async def 알림_삭제(notification_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    notif = (await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.recipient == email)
    )).scalar_one_or_none()
    if notif is None:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다")
    await db.delete(notif)
    await db.commit()
    return {"id": notification_id, "deleted": True}


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


# ── 사용자 부서 관리 (/admin 접두사라 미들웨어가 이미 관리자만 통과시킴) ─────────
# 부서는 관리자가 여기서 직접 지정한다. 문서는 업로드 시점의 업로드자 부서를 그대로
# 물려받아(main.py 업로드 핸들러) 부서별 열람 제한(_filter_readable_ids)의 기준이 된다.
@app.get("/admin/users")
async def 사용자_목록(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(User).order_by(User.email))).scalars().all()
    return [
        {"email": u.email, "name": u.name, "department": u.department}
        for u in rows
    ]


@app.patch("/admin/users/{email}/department")
async def 사용자_부서_지정(email: str, department: str | None = Body(None, embed=True), db: AsyncSession = Depends(get_db)):
    email = email.strip().lower()
    department = (department or "").strip() or None
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        # 아직 한 번도 로그인 안 한 사람에게도 미리 부서를 지정해둘 수 있게 upsert
        user = User(email=email)
        db.add(user)
    user.department = department
    await db.commit()
    return {"email": email, "department": department}


# 사용자 완전 삭제("내보내기") — 부서 관리 탭에서 관리자가 특정 계정을 시스템에서 제거한다.
# User 테이블 행(부서·이름·사진 기억)과 추가 관리자 지정을 함께 지운다.
# 주의: Google 로그인 자체는 여기서 막지 못한다 — 다시 로그인하면 User 행이 새로 생기며
# 부서 미지정 상태로 돌아간다(도메인 제한 없다면). 완전한 접근 차단은 별도 기능이 필요하다.
@app.delete("/admin/users/{email}")
async def 사용자_내보내기(email: str, db: AsyncSession = Depends(get_db)):
    email = email.strip().lower()
    if _is_admin(email):
        raise HTTPException(status_code=400, detail="관리자 계정은 여기서 내보낼 수 없습니다")
    await db.execute(sa_delete(User).where(User.email == email))
    await db.execute(sa_delete(AdminEmail).where(AdminEmail.email == email))
    _extra_admin_emails.discard(email)
    await db.commit()
    return {"email": email, "removed": True}


# 로그인 기능이 켜져 있으면(AUTH_ENABLED) 아래 목록을 제외한 모든 API가 유효한
# 세션 쿠키를 요구한다. 꺼져 있으면 기존 무인증 동작 그대로 통과시킨다.
# [2026-07-23] /docs·/openapi.json·/redoc은 여기서 뺐다 — API 전체 구조(엔드포인트·
# 파라미터)를 로그인 없이 누구나 볼 수 있었던 건 크롤러·스캐너에게 정찰 정보를
# 그대로 내주는 셈이라 로그인 필요 목록으로 옮김(로그인한 사람은 그대로 볼 수 있음).
_AUTH_PUBLIC_PATHS = {"/", "/auth/config", "/auth/google", "/auth/me", "/auth/logout", "/favicon.svg", "/robots.txt"}
# 프론트 빌드 정적 파일(/assets/index-XXXX.js·css 등)은 로그인 화면 자체를 띄우는 데
# 필요하므로 항상 통과시킨다 — 안 그러면 "로그인 화면을 보려면 로그인이 필요"한
# 모순이 생겨(그 파일들이 401로 막혀 화면이 통째로 빈 채로 남음, 실제로 겪은 버그).
_AUTH_PUBLIC_PREFIXES = ("/assets/",)

# ── 요청 속도 제한 (크롤링·스크래핑 방어, 2026-07-23) ─────────────────────────
# 로그인된 사용자는 이메일 단위로, 로그인 전이거나 인증이 꺼진 환경에서는 클라이언트
# IP 단위로 최근 _RATE_LIMIT_WINDOW_SEC 안의 요청 수를 센다. 정상적인 사용(클릭
# 몇 번, 검색 몇 번)은 절대 안 걸리는 넉넉한 값이고, 스크립트로 API를 빠르게
# 반복 호출하는 패턴만 막는다. 프로세스 메모리에만 저장 — Railway 인스턴스가
# 1개인 지금 구조에 맞는 가장 단순한 방식(인스턴스를 여러 개로 늘리면 Redis 등
# 공유 저장소로 바꿔야 한다).
_RATE_LIMIT_WINDOW_SEC = int(os.environ.get("RATE_LIMIT_WINDOW_SEC", "60"))
_RATE_LIMIT_MAX_REQ    = int(os.environ.get("RATE_LIMIT_MAX_REQ", "180"))
_rate_limit_buckets: dict = {}

def _check_rate_limit(key: str) -> bool:
    """True면 허용, False면 이 요청은 제한 초과로 막아야 함."""
    now = time.monotonic()
    bucket = _rate_limit_buckets.setdefault(key, [])
    cutoff = now - _RATE_LIMIT_WINDOW_SEC
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= _RATE_LIMIT_MAX_REQ:
        return False
    bucket.append(now)
    return True


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path

    # 정적 자원(/assets/*)은 페이지 하나 열 때도 수십 개씩 요청되므로 속도 제한 대상에서 제외
    if not path.startswith(_AUTH_PUBLIC_PREFIXES):
        _rl_session = _verify_session_token(request.cookies.get(SESSION_COOKIE))
        _rl_key = _rl_session["email"] if _rl_session else (request.client.host if request.client else "unknown")
        if not _check_rate_limit(_rl_key):
            return JSONResponse(status_code=429, content={"detail": "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요."})

    if (not AUTH_ENABLED or request.method == "OPTIONS"
            or path in _AUTH_PUBLIC_PATHS or path.startswith(_AUTH_PUBLIC_PREFIXES)):
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


# ── 임베딩 설정 ────────────────────────────────────────────────────────────
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


# ── 유사 검색 재랭킹용 BM25 토크나이저 (지연 로딩) ──────────────────────────
# 2026-07-23: 유사 검색(임베딩) 후보 안에서 랭킹 품질을 보강하기 위해서만 쓴다 —
# 예전처럼 전체 문서를 대상으로 한 글로벌 BM25 인덱스/캐시가 아니라, 매 검색마다
# "이미 의미상 후보로 뽑힌" 소규모 문서 집합에 대해서만 그때그때 새로 만든다
# (후보가 원래도 최대 수십 건이라 캐싱 없이도 충분히 빠르다). 이 방식이면 BM25가
# 후보를 "추가"하는 일은 없고 순위만 보강하므로, 예전에 겪었던 "형태소 축약으로
# 무관한 문서가 정확검색으로 잘못 분류되는" 문제가 재발하지 않는다.
_search_indexer = None

def _get_search_indexer():
    global _search_indexer
    if _search_indexer is None:
        try:
            from search.search import BM25Indexer
            _search_indexer = BM25Indexer()   # 내부에서 Kiwi 로드 (첫 호출만 느림)
        except Exception as e:
            print(f"[search] BM25 토크나이저 로드 실패: {e}")
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


_doc_text_cache: dict = {}  # doc_id -> {"full_text": str, "pages": list}  (파싱 JSON 캐시)

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

    # npz 저장 (turbovec 인덱스 없을 때 유사 검색의 numpy fallback용)
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
            for i, (chunk_text_val, pnum) in enumerate(zip(chunks, pnums)):
                emb_bytes = embs[i].tobytes() if embs is not None else None
                chunk_row = Chunk(doc_id=doc_id, text=chunk_text_val, page_num=pnum, chunk_idx=i, embedding=emb_bytes)
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
        _invalidate_doc_text(doc_id)
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


# GET /robots.txt — 사내 전용 도구라 검색엔진 등 정상적인 크롤러의 색인을 차단한다.
# (악의적인 스크래퍼는 애초에 robots.txt를 지키지 않으므로 실제 방어는 로그인 요구가
# 담당하고, 이건 우리 사이트가 실수로 외부에 공유됐을 때의 최소한의 안전장치다.)
@app.get("/robots.txt")
def 로봇_배제():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")

# GET /upload/check?filename=... — 같은 파일명이 이미 있는지 확인합니다
@app.get("/upload/check")
async def 중복_확인(filename: str = Query(...), db: AsyncSession = Depends(get_db)):
    # [수정 2026-07-25] "버전 추가" 때문에 같은 파일명 문서가 2개 이상 있을 수 있어
    # scalar_one_or_none()이 MultipleResultsFound로 죽을 수 있다(main.py의 sha256 중복
    # 확인 쿼리와 동일한 버그) — 가장 최근 문서 하나만 가져오도록 limit(1) 적용.
    result = await db.execute(
        select(Document).where(Document.filename == filename)
        .order_by(Document.uploaded_at.desc()).limit(1)
    )
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

    # 파싱 결과(JSON·임베딩 npz)도 실시간 백업 미러로 복사
    try:
        await asyncio.to_thread(_mirror_parsed, doc_id)
    except Exception as e:
        print(f"[mirror] doc_id={doc_id}: 파싱 결과 미러 복사 실패 — {e}")


# POST /upload — 파일을 받아 파싱하고 DB에 기록합니다
# category:      문서 분류 (spec/research/presentation/report/other)
# original_path: 폴더 업로드 시 원본 상대 경로 (예: 프로젝트A/자료/파일.pdf)
#                프론트에서 file.webkitRelativePath 를 보내면 됩니다
@app.post("/upload")
async def 파일_업로드(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    category: str | None = Form(None),
    original_path: str = Form(None),   # 폴더 업로드 시 상대 경로 (없으면 None)
    memo: str = Form(None),            # 업로드 시점에 바로 남기는 특이사항 — 검색 결과에 그대로 노출됨
    db: AsyncSession = Depends(get_db),
):
    # 카테고리는 고정 5종(spec/research/presentation/report/other) 외에도 클라이언트가
    # 자유롭게 새 이름을 입력해 만들 수 있다 — category는 단순 문자열 컬럼이라
    # 별도 마이그레이션·사전 등록 없이 그대로 저장·조회된다.
    category = (category or "").strip()[:30]
    memo = (memo or "").strip()[:1000] or None

    # [임시 계측 2026-07-25] 업로드가 느리다는 반복 신고 때문에, 추측 대신 실제로
    # 어느 단계가 오래 걸리는지 로그로 확인하기 위한 구간별 타이머. 원인이 파악되면
    # 제거할 것.
    _t0 = time.perf_counter()
    def _lap(label: str):
        print(f"[upload-timing] {file.filename}: {label} 까지 누적 {time.perf_counter() - _t0:.2f}초")

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

    # ── 검사 3: 파일 크기 제한 — 1MB 스트리밍 (OOM 방지) ─────────────────
    # PDF는 전역 상한(MAX_UPLOAD_SIZE)을 그대로 쓰고, 비-PDF는 페이지 수 가드레일이
    # 의미가 없어(위 NON_PDF_MAX_SIZE_MB 주석 참고) 더 타이트한 상한을 적용한다.
    effective_max_size = MAX_UPLOAD_SIZE if ext == '.pdf' else min(MAX_UPLOAD_SIZE, NON_PDF_MAX_SIZE)
    hasher = hashlib.sha256()
    chunks_data = []
    total = 0
    while True:
        chunk_bytes = await file.read(1024 * 1024)
        if not chunk_bytes:
            break
        total += len(chunk_bytes)
        if total > effective_max_size:
            mb = round(total / (1024 * 1024), 1)
            limit_mb = effective_max_size // (1024 * 1024)
            print(f"[upload] {file.filename}: 크기 {mb}MB — 상한({limit_mb}MB, {ext}) 초과로 업로드 거부")
            return {"source_file": file.filename, "status": "failed",
                    "error": f"파일 크기 초과 ({mb}MB). 최대 {limit_mb}MB까지 지원합니다", "pages": []}
        hasher.update(chunk_bytes)
        chunks_data.append(chunk_bytes)
    sha256_hex = hasher.hexdigest()
    contents = b"".join(chunks_data)
    _lap("파일 수신+해시")

    # ── 검사 4: PDF 페이지 수 상한 (STEP 3, 2026-07-22) ──────────────────
    # 디스크 저장·Document 행 생성 전, 메모리에 있는 바이트로 바로 카운트한다
    # (classify_pdf의 전체 페이지 텍스트 추출 루프보다 훨씬 가볍고, "parsing" 상태를
    #  거치지도 않은 채 업로드 응답 자체에서 즉시 거부된다). /admin/documents/{id}/retry
    # 처럼 이 업로드 핸들러를 거치지 않는 경로를 위한 안전망은 core/parser.py의
    # parse_pdf() 진입부에 별도로 있다(재시도 시에도 반드시 거른다).
    if ext == '.pdf':
        try:
            page_count = await asyncio.to_thread(count_pdf_pages, io.BytesIO(contents))
        except Exception as e:
            print(f"[upload] {file.filename}: PDF 페이지 수 확인 실패 — {e}")
            return {"source_file": file.filename, "status": "failed",
                    "error": "PDF를 열 수 없습니다 (파일이 손상되었거나 형식이 올바르지 않습니다)", "pages": []}
        if page_count > HARD_PAGE_LIMIT:
            print(f"[upload] {file.filename}: 페이지 수 {page_count}쪽 — 상한({HARD_PAGE_LIMIT}쪽) 초과로 업로드 거부")
            return {"source_file": file.filename, "status": "failed",
                    "error": f"페이지 수 초과 ({page_count}쪽). 최대 {HARD_PAGE_LIMIT}쪽까지 지원합니다 — 문서를 분할해 나눠 업로드해 주세요.",
                    "pages": []}
    _lap("PDF 페이지 수 확인")

    # ── 중복 파일 처리 (2026-07-24 변경) — 사내 문서라 "덮어쓰기"로 예전 문서를
    # 지우면 안 된다는 요청에 따라, 같은 파일명이든 내용(SHA-256)이 완전히 같든
    # 예전 문서를 삭제하지 않고 항상 새 문서로 별도 등록한다("버전 추가"). 다만
    # 내용까지 완전히 같은 파일이 이미 있으면 그 사실만 응답에 담아 프론트가
    # 안내 팝업을 띄우게 한다(업로드 자체는 막지 않음).
    # [수정 2026-07-25] "버전 추가" 때문에 같은 sha256을 가진 문서가 2개 이상 쌓일 수
    # 있는데, scalar_one_or_none()은 결과가 최대 1개라고 가정해 2개 이상이면
    # MultipleResultsFound로 죽는다(실제 배포에서 같은 파일을 반복 업로드하다 재현
    # 확인) — 가장 최근 문서 하나만 가져오도록 limit(1)로 명시한다.
    dup = (await db.execute(
        select(Document).where(Document.sha256 == sha256_hex)
        .order_by(Document.uploaded_at.desc()).limit(1)
    )).scalar_one_or_none()
    duplicate_of = {"id": dup.id, "filename": dup.filename} if dup is not None else None
    _lap("중복 확인 DB 조회")

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

    # 예전 문서를 안 지우게 되면서(바로 위 "버전 추가" 변경) 같은 파일명의 문서가
    # 이미 디스크에 있을 수 있다 — 그대로 저장하면 그 예전 파일의 실제 내용을
    # 새 파일로 조용히 덮어써버려서, DB엔 예전 문서 행이 남아있는데 디스크 내용은
    # 새 파일이 되는 데이터 불일치가 생긴다. 충돌하면 " (2)", " (3)"... 을 붙여 피한다.
    if os.path.exists(save_path):
        stem, save_ext = os.path.splitext(save_path)
        counter = 2
        while os.path.exists(f"{stem} ({counter}){save_ext}"):
            counter += 1
        save_path = f"{stem} ({counter}){save_ext}"

    try:
        with open(save_path, "wb") as f:
            f.write(contents)
    except Exception:
        return {"source_file": file.filename, "status": "failed",
                "error": "파일 저장 중 오류가 발생했습니다", "pages": []}
    _lap("디스크 저장")

    # 업로드된 원본을 실시간 백업 미러로 복사 (파싱 성공/실패와 무관하게 원본은 보호) —
    # 응답을 막지 않도록 BackgroundTasks로 미룬다(2026-07-25, 업로드 속도 개선).
    background_tasks.add_task(_mirror_upload_safe, save_path, file.filename)

    # ── 문서 레코드를 'parsing' 상태로 즉시 생성 후 응답 ─────────────────
    # [비동기화 2026-07-05] 파싱은 여기서 하지 않고 백그라운드(_parse_and_ingest)로 넘긴다.
    # 대용량 PDF도 업로드 요청이 즉시 끝나 타임아웃·이벤트루프 블로킹을 피한다.
    # 프론트는 반환된 id로 /documents/{id}/status 를 폴링해 완료를 확인한다.
    file_type = ext.lstrip('.')
    uploader_email = _current_user_email(request)
    # 업로드자의 부서를 그대로 문서에 박아둔다 — 부서별 열람 제한의 기준값(_filter_readable_ids).
    # 부서가 지정 안 된 사람이 올리면 department는 None(=제한 없음)으로 남는다.
    uploader_department = (await db.execute(
        select(User.department).where(User.email == uploader_email)
    )).scalar_one_or_none()
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
        uploaded_by   = uploader_email,
        department    = uploader_department,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)  # auto-increment id 가져오기
    _lap("DB insert(문서 행 생성)")

    # 응답 후 백그라운드에서 파싱 → 상태 갱신 → JSON 저장 → 임베딩 색인
    background_tasks.add_task(_parse_and_ingest, doc.id, save_path, file.filename, ext)
    _lap("응답 반환 직전(전체)")

    return {
        "id":       doc.id,
        "filename": file.filename,
        "status":   "parsing",
        "error":    None,
        "message":  "업로드 접수됨 — 파싱이 백그라운드에서 진행됩니다.",
        "duplicate_of": duplicate_of,  # 내용(SHA-256)까지 완전히 같은 기존 문서가 있으면 그 정보, 없으면 null
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
            "category": d.category,
        }
        for d in docs
    ]


# GET /documents/recent — 업로드 화면 "최근 업로드" 탭용 공개(관리자 아니어도 되는) 문서 목록
# [2026-07-24] 원래 업로드 화면이 관리자 전용 /admin/documents를 그대로 갖다 쓰고 있었다
# (로그인·권한 기능이 생기기 전 MVP 시절 코드가 안 고쳐진 채 남아있었음) — 그래서 관리자가
# 아닌 계정은 이 요청이 항상 403으로 실패했다(업로드 화면에서 8초마다 폴링하니 더 나빴음).
# /admin/documents와 달리 부서별 열람 권한 필터(_filter_readable_ids)를 반드시 거친다 —
# 안 그러면 관리자 전용 API가 하던 "전체 문서 다 보여주기"를 일반 사용자에게도 그대로
# 노출해 다른 부서 문서까지 보이는 권한 우회가 생긴다.
@app.get("/documents/recent")
async def 최근_문서_목록(request: Request, limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(
        select(Document).order_by(Document.uploaded_at.desc()).limit(limit * 2)
    )).scalars().all()
    readable = await _filter_readable_ids(db, [d.id for d in docs], _current_user_email(request))
    filtered = [d for d in docs if d.id in readable][:limit]
    return [
        {
            "id":          d.id,
            "filename":    d.filename,
            "category":    d.category,
            "status":      d.status,
            "uploaded_at": str(d.uploaded_at),
        }
        for d in filtered
    ]


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

    # 실시간 백업 미러에서도 함께 제거 — 안 그러면 지운 문서의 사본이 미러에 계속 남아 쌓인다
    try:
        await asyncio.to_thread(_mirror_remove, doc_id, doc.saved_path)
    except Exception as e:
        print(f"[mirror] doc_id={doc_id}: 미러 제거 실패 — {e}")

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

    await db.delete(doc)


# DELETE /admin/documents/bulk — 여러 문서 일괄 삭제
# ⚠ 반드시 /admin/documents/{doc_id} 보다 먼저 등록해야 한다 — 순서가 뒤바뀌면
# "bulk"라는 문자열이 {doc_id}(int)로 파싱 시도되어 422로 실패하고, 이 라우트는
# 영원히 호출되지 않는 죽은 코드가 된다(2026-07-21 실제로 이 상태였음).
@app.delete("/admin/documents/bulk")
async def 문서_일괄_삭제(
    request: Request,
    ids: list[int] = Body(...),
    reason: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="삭제 사유를 입력해야 합니다")

    deleted_by = _current_user_email(request)
    deleted = []
    for doc_id in ids:
        result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if doc:
            db.add(DeletionLog(doc_id=doc.id, filename=doc.filename, category=doc.category, deleted_by=deleted_by, reason=reason))
            await _문서_완전삭제(doc, db)
            deleted.append(doc_id)
    await db.commit()
    return {"deleted": deleted, "count": len(deleted)}


# DELETE /admin/documents/{id} — 문서를 DB와 디스크에서 삭제합니다
# {id} 는 삭제할 문서의 고유 번호입니다 (문서 목록에서 확인 가능)
@app.delete("/admin/documents/{doc_id}")
async def 문서_삭제(
    doc_id: int,
    request: Request,
    reason: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="삭제 사유를 입력해야 합니다")

    # DB에서 해당 ID의 문서를 찾습니다
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    # ID에 해당하는 문서가 없으면 404 에러를 돌려줍니다
    if doc is None:
        raise HTTPException(status_code=404, detail="해당 문서를 찾을 수 없습니다")

    filename = doc.filename
    db.add(DeletionLog(doc_id=doc.id, filename=doc.filename, category=doc.category, deleted_by=_current_user_email(request), reason=reason))
    await _문서_완전삭제(doc, db)
    await db.commit()

    return {"message": f"'{filename}' 문서가 삭제됐습니다", "id": doc_id}


# GET /admin/deletion-logs — 삭제 보고서 목록 (관리자 전용, 최신순)
@app.get("/admin/deletion-logs")
async def 삭제_보고서_목록(limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(DeletionLog).order_by(DeletionLog.deleted_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id,
            "doc_id": r.doc_id,
            "filename": r.filename,
            "category": r.category,
            "deleted_by": r.deleted_by,
            "reason": r.reason,
            "deleted_at": str(r.deleted_at),
        }
        for r in rows
    ]


# PATCH /admin/deletion-logs/{id} — 삭제 보고서의 사유를 나중에 수정(예: "재업로드 예정" → 실제 처리 결과로 갱신)
@app.patch("/admin/deletion-logs/{log_id}")
async def 삭제_보고서_사유_수정(log_id: int, reason: str = Body(..., embed=True), db: AsyncSession = Depends(get_db)):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="삭제 사유를 입력해야 합니다")

    result = await db.execute(select(DeletionLog).where(DeletionLog.id == log_id))
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail="해당 삭제 기록을 찾을 수 없습니다")

    log.reason = reason
    await db.commit()
    return {"id": log.id, "reason": log.reason}


# ── DB 백업 관리 (관리자 전용) ────────────────────────────────────────────────
@app.post("/admin/backup")
async def 백업_실행():
    try:
        result = await asyncio.to_thread(_run_backup)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"백업 실패: {e}")
    return result


@app.get("/admin/backups")
async def 백업_목록():
    files = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.startswith(("km_backup_", "km_files_"))),
        reverse=True,
    )
    return [
        {
            "filename": f,
            "size_bytes": os.path.getsize(os.path.join(BACKUP_DIR, f)),
            "created_at": datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUP_DIR, f))).isoformat(),
        }
        for f in files
    ]


@app.get("/admin/backups/{filename}")
async def 백업_다운로드(filename: str):
    # 실제로 BACKUP_DIR에 있는 파일명인지 확인 후에만 서빙 — 경로 조작(path traversal) 방지
    if filename not in os.listdir(BACKUP_DIR):
        raise HTTPException(status_code=404, detail="백업 파일을 찾을 수 없습니다")
    return FileResponse(os.path.join(BACKUP_DIR, filename), filename=filename)


@app.delete("/admin/backups/{filename}")
async def 백업_삭제(filename: str):
    filepath = os.path.join(BACKUP_DIR, filename)
    if filename not in os.listdir(BACKUP_DIR):
        raise HTTPException(status_code=404, detail="백업 파일을 찾을 수 없습니다")
    os.remove(filepath)
    return {"filename": filename, "deleted": True}


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


async def 실제_검색_실행(q: str, mode: str, category, db: AsyncSession, uploaded_by=None, date_from=None, date_to=None) -> dict:
    """두 모드로 나뉜 검색.
    - mode="filename": 파일명에 검색어가 그대로 포함된 문서만 찾는다(문서 내용은 보지 않음).
    - mode="semantic": MiniLM 임베딩 코사인 유사도로 문서 "내용"을 찾는다. 모호한 문구를
      넣어도 폭넓게 잡아내도록 임계값 없이(0보다 크면 전부) 후보로 삼는다. 후보가 정해진
      뒤에는 그 안에서만 BM25로 순위를 보강해 랭킹 품질을 높인다(후보 자체를 늘리거나
      줄이지는 않음 — 그래서 무관한 문서가 BM25 하나만으로 끼어드는 일은 없다).
    검색어가 비어 있으면(카테고리 등 필터만으로 "둘러보기") 모드와 무관하게 최신순으로
    나열한다.
    """
    import re

    # -- 1. 문서 목록 로드 --------------------------------------------------
    stmt = select(Document).where(Document.status == "success")
    if category:
        stmt = stmt.where(Document.category == category)
    if uploaded_by:
        stmt = stmt.where(Document.uploaded_by == uploaded_by)
    # [수정 2026-07-24] uploaded_at(TIMESTAMP 컬럼)에 날짜 "문자열"을 그대로 비교하면
    # SQLite는 느슨하게 봐줘서 로컬에선 잘 되지만, PostgreSQL(프로덕션)은 타입을 엄격히
    # 따져 text vs timestamp 비교에서 오류를 낸다 — datetime 객체로 변환해서 비교해야 한다.
    if date_from:
        try:
            stmt = stmt.where(Document.uploaded_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        # date_to는 "YYYY-MM-DD" 하루 단위로 받아 해당 날짜 끝까지 포함시킨다(다음날 0시 미만)
        try:
            date_to_exclusive = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            stmt = stmt.where(Document.uploaded_at < date_to_exclusive)
        except ValueError:
            pass
    docs = (await db.execute(stmt)).scalars().all()
    if not docs:
        return {"results": []}

    q_stripped = q.strip()
    if not q_stripped:
        # 키워드 없이 카테고리/파일형식 필터만으로 "둘러보기" -- 관련도 순위가 없으므로
        # 최신 업로드순으로 나열하고 score=0으로 반환(프론트가 관련도 배지를 숨기는 신호로 씀)
        browsed = sorted(docs, key=lambda d: d.uploaded_at, reverse=True)
        results = []
        for d in browsed[:200]:
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
        return {"results": results}

    if mode == "filename":
        # -- 파일명 검색 -- 문서 "내용"은 전혀 보지 않고 파일명 문자열만 본다 --------
        q_lower = q_stripped.lower()
        matched = [d for d in docs if q_lower in d.filename.lower()]
        matched.sort(key=lambda d: d.uploaded_at, reverse=True)
        results = []
        for d in matched[:50]:
            info = _get_doc_text(d.id)
            preview = info["full_text"][:150].strip()
            results.append({
                "doc_id":      d.id,
                "filename":    d.filename,
                "category":    d.category,
                "pages":       d.page_count,
                "snippet":     d.title or d.filename,
                "content_preview": preview,
                "page_num":    0,
                "score":       0,   # 파일명 포함 여부는 이분법이라 관련도 배지를 붙이지 않는다
                "view_count":  d.view_count,
                "uploaded_by": d.uploaded_by,
                "uploaded_at": str(d.uploaded_at),
            })
        return {"results": results}

    # -- 유사 검색 -- MiniLM 임베딩 코사인 유사도로 문서 "내용"을 찾는다 --------------
    # 한국어 쿼리 <-> 영어 문서 교차 검색 지원. "모호한 문구를 넣어도 내용과 일치하는
    # 목록을 전부 보여주게 해달라"는 요구에 맞춰, 별도 임계값으로 걸러내지 않고
    # 0보다 큰(즉 방향이 조금이라도 맞는) 유사도는 전부 후보로 삼는다.
    tokens = [t.lower() for t in re.split(r"[\s\W]+", q_stripped) if len(t) >= 2]  # 스니펫 위치 찾기용
    sem_scores: dict = {}
    sem_chunks: dict = {}   # doc_id -> 가장 유사한 청크 텍스트 (스니펫용)
    sem_pages:  dict = {}   # doc_id -> 가장 유사한 청크의 페이지 번호
    async def _npz_fallback():
        """turbovec 인덱스가 없거나, 오류가 났거나, (색인 유실 등으로) 결과가 0건일 때
        문서별로 저장해 둔 .npz 임베딩을 직접 코사인 유사도 계산해 찾는다."""
        if model is None:
            return
        try:
            q_emb_np = (await asyncio.to_thread(
                model.encode, [q_stripped],
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
        except Exception as e:
            print(f"[search] 시맨틱 오류: {e}")

    idx = _get_vec_index()
    model = _get_embed_model()
    if idx is not None and model is not None:
        q_emb = (await asyncio.to_thread(
            model.encode, [q_stripped], normalize_embeddings=True, show_progress_bar=False
        ))[0].astype(np.float32)
        try:
            # [수정 2026-07-04] turbovec search는 2차원 쿼리 배열을 받고
            # (scores, ids) "순서"로 반환한다 -- 기존 코드는 (ids, scores)로
            # 거꾸로 받고 1차원을 넘겨서 시맨틱 경로가 항상 예외->fallback으로 빠졌음.
            scores_2d, ids_2d = idx.search(q_emb.reshape(1, -1), 50)
            vec_scores, vec_ids = scores_2d[0], ids_2d[0]
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
            await _npz_fallback()
        # turbovec 색인이 DB Chunk와 어긋나 있으면(예: 색인 유실) 오류 없이 그냥 0건이
        # 나올 수 있다 — 이 경우도 조용히 넘어가지 않고 npz로 한 번 더 찾아본다.
        if not sem_scores:
            await _npz_fallback()
    else:
        # turbovec 없으면 기존 npz 방식 fallback
        await _npz_fallback()

    docs_by_id = {d.id: d for d in docs}
    candidate_ids = [did for did, s in sem_scores.items() if s > 0 and did in docs_by_id]
    if not candidate_ids:
        return {"results": []}

    # ── 의미 검색 후보 안에서 BM25로 랭킹 품질 보강 ────────────────────────────
    # 후보(candidate_ids)는 이미 의미 유사도만으로 확정됐다 — BM25는 여기서 새
    # 문서를 추가하지 않고, 이미 뽑힌 후보들의 "순서"만 문맥에 맞게 재조정한다.
    # (전체 문서 대상 글로벌 BM25 인덱스가 아니라 이 소규모 후보 집합만 매번
    # 새로 만들므로 캐시가 필요 없고, 예전에 있었던 "형태소 축약으로 무관한
    # 문서가 후보에 잘못 끼어드는" 문제도 구조적으로 재발하지 않는다.)
    bm25_scores: dict = {}
    try:
        indexer = _get_search_indexer()
        corpus_docs = [docs_by_id[did] for did in candidate_ids]
        corpus_texts = []
        for doc in corpus_docs:
            info = _get_doc_text(doc.id)
            corpus_texts.append(" ".join(filter(None, [doc.filename, doc.title or "", doc.memo or "", info["full_text"]])))
        if indexer is not None:
            corpus   = [indexer._tokenize(t) for t in corpus_texts]
            q_tokens = indexer._tokenize_query(q_stripped)
        else:
            corpus   = [[t.lower() for t in re.split(r"[\s\W]+", t) if len(t) >= 2] for t in corpus_texts]
            q_tokens = [t.lower() for t in re.split(r"[\s\W]+", q_stripped) if len(t) >= 2]
        from rank_bm25 import BM25Okapi
        bm25_index = BM25Okapi(corpus)
        raw_scores = bm25_index.get_scores(q_tokens)
        for doc, s in zip(corpus_docs, raw_scores):
            bm25_scores[doc.id] = float(s)
    except Exception as e:
        print(f"[search] 유사 검색 BM25 재랭킹 실패(의미검색 점수만 사용): {e}")

    # RRF로 의미검색 순위(주 신호)와 BM25 순위(보강 신호)를 합친다 — 의미검색에
    # 더 작은 k를 줘서 주 신호로 삼고, BM25는 동점 상황을 갈라주는 보조 역할만 한다.
    K_SEM, K_BM25 = 30, 60
    n = len(candidate_ids)
    sem_rank = {did: i + 1 for i, did in enumerate(sorted(candidate_ids, key=lambda d: sem_scores[d], reverse=True))}
    bm25_rank = {}
    if bm25_scores:
        for i, did in enumerate(sorted(bm25_scores, key=bm25_scores.get, reverse=True)):
            bm25_rank[did] = i + 1
    final_scores = {}
    for did in candidate_ids:
        sr = sem_rank.get(did, n + 1)
        br = bm25_rank.get(did, n + 1)
        final_scores[did] = 1 / (K_SEM + sr) + 1 / (K_BM25 + br)

    results = []
    for did in candidate_ids:
        doc = docs_by_id[did]
        if did in sem_chunks:
            snippet  = _extract_snippet_multi(sem_chunks[did], tokens)
            page_num = sem_pages.get(did, 0)
        else:
            info = _get_doc_text(did)
            full_text = info["full_text"]
            snippet  = (
                _extract_snippet_multi(full_text, tokens) if full_text.strip()
                else (doc.title or doc.filename)
            )
            page_num = _best_page_for_tokens(info["pages"], tokens)

        results.append({
            "doc_id":      doc.id,
            "filename":    doc.filename,
            "category":    doc.category,
            "pages":       doc.page_count,
            "snippet":     snippet,
            "content_preview": snippet,
            "page_num":    page_num,
            "score":       final_scores[did],
            "view_count":  doc.view_count,
            "uploaded_by": doc.uploaded_by,
            "uploaded_at": str(doc.uploaded_at),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    if results and results[0]["score"] > 0:
        top = results[0]["score"]
        for r in results:
            r["score"] = round(r["score"] / top, 4)

    return {"results": results[:50]}


# GET /search — 문서 검색 엔드포인트
# 파라미터:
#   q        : 검색어 (예: "모터 설계 사양")
#   mode     : filename(파일명 검색, 기본값) | semantic(유사 검색)
#   category : 카테고리 필터 (없으면 전체, 있으면 해당 카테고리만)
# 응답의 results는 mode에 따라 파일명 부분일치 결과 또는 임베딩+BM25 재랭킹 결과 하나만
# 담긴다 — 화면에서 "파일명 검색"/"유사 검색" 중 고른 모드 하나만 보여준다.
@app.get("/search")
async def 검색(
    request: Request,
    q: str = Query(..., description="검색어"),
    mode: str = Query("filename", description="검색 모드: filename(파일명 검색, 기본값) | semantic(유사 검색)"),
    category: str = Query(None, description="카테고리 필터 (spec/research/presentation/report/other)"),
    file_type: str = Query(None, description="파일 형식 필터 (pdf/docx/pptx/xlsx/hwp 등)"),
    uploaded_by: str = Query(None, description="작성자(업로더) 이메일 필터"),
    date_from: str = Query(None, description="업로드 날짜 범위 시작 (YYYY-MM-DD)"),
    date_to: str = Query(None, description="업로드 날짜 범위 끝 (YYYY-MM-DD, 해당일 포함)"),
    db: AsyncSession = Depends(get_db),
):
    if mode not in ("filename", "semantic"):
        mode = "filename"

    bucket  = await 실제_검색_실행(q, mode, category, db, uploaded_by=uploaded_by, date_from=date_from, date_to=date_to)
    results = bucket["results"]

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

    # 검색 기록 저장 — 계정에 귀속된 서버측 기록(로그인 꺼져 있으면 "anonymous" 공용)
    log = SearchLog(query=q, result_count=len(results), user_email=_current_user_email(request))
    db.add(log)
    await db.commit()

    return {"query": q, "mode": mode, "category": category, "file_type": file_type,
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


# GET /stats — 검색·업로드 화면용 공개(관리자 아니어도 되는) 통계 서브셋
# [2026-07-24] 위와 같은 이유로 검색·업로드 화면이 /admin/stats를 그대로 갖다 쓰고
# 있었다 — 카테고리 칩 개수·인기 검색어 표시에만 쓰는데 관리자 아니면 403이 났다.
# 실패/OCR검토 건수처럼 운영진 전용 지표는 빼고, 일반 사용자 화면이 실제로 쓰는
# 필드만 돌려준다.
@app.get("/stats")
async def 공개_통계(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count()).select_from(Document))).scalar()

    cat_rows = (await db.execute(
        select(Document.category, func.count().label("cnt"))
        .group_by(Document.category)
    )).all()
    by_category = {row.category: row.cnt for row in cat_rows if row.category}

    top_queries = (await db.execute(
        select(SearchLog.query, func.count().label("cnt"))
        .group_by(SearchLog.query)
        .order_by(func.count().desc())
        .limit(5)
    )).all()

    return {
        "total_documents": total,
        "by_category":    by_category,
        "top_queries":    [{"query": r.query, "count": r.cnt} for r in top_queries],
    }


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
    by_category = {row.category: row.cnt for row in cat_rows if row.category}

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
        # [수정 2026-07-25] 빈 문자열을 조용히 무시하던 것을 명시적 거부로 변경 —
        # 문서는 항상 카테고리가 지정돼 있어야 한다는 업로드 흐름의 전제를 이 엔드포인트도
        # 지켜야 한다(그동안은 프론트 UI만 이를 강제하고 서버는 강제하지 않았음).
        if not category:
            raise HTTPException(status_code=400, detail="카테고리를 비워둘 수 없습니다")
        doc.category = category
    if memo is not None:
        doc.memo = memo.strip()[:1000] or None

    await db.commit()
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


# ── 내 검색 기록 (계정에 귀속된 서버측 기록 — 브라우저 localStorage가 아니라 SearchLog에 저장) ──
# 로그인이 꺼져 있으면(AUTH_ENABLED=False) "anonymous" 단일 공용 기록으로 동작(다른 /me 기능과 동일 원칙).
@app.get("/me/search-history")
async def 내_검색_기록(request: Request, limit: int = Query(10, ge=1, le=50), db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    rows = (await db.execute(
        select(SearchLog.query, func.max(SearchLog.searched_at).label("last_searched_at"))
        .where(SearchLog.user_email == email)
        .group_by(SearchLog.query)
        .order_by(func.max(SearchLog.searched_at).desc())
        .limit(limit)
    )).all()
    return [{"query": r.query, "searched_at": str(r.last_searched_at)} for r in rows]


@app.delete("/me/search-history")
async def 내_검색_기록_삭제(request: Request, db: AsyncSession = Depends(get_db)):
    email = _current_user_email(request)
    await db.execute(sa_delete(SearchLog).where(SearchLog.user_email == email))
    await db.commit()
    return {"message": "검색 기록이 삭제됐습니다"}


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
        # [수정] 여기서 raise exc로 같은 예외를 다시 던지면 이 핸들러가 자기 자신에게
        # 다시 걸려 처리되지 못하고 500으로 떨어진다(실제로 없는 정적 파일 요청이 전부
        # 500이 되는 걸로 확인됨) — 원래 상태 코드를 그대로 응답으로 돌려줘야 한다.
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
