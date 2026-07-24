# models.py — 데이터베이스 테이블 정의 파일
# 여기서 정의한 클래스 하나 = 데이터베이스 테이블 하나

from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Float, Text, LargeBinary, ForeignKey, UniqueConstraint
from database import Base

# [수정 2026-07-24] 지금까지 모든 *_at 컬럼이 server_default=func.now()를 썼는데, 이건
# DB 서버(SQLite/PostgreSQL)의 UTC 기준 현재 시각을 시간대 표시 없이 그대로 저장한다.
# 프론트에서는 이 값을 그대로(또는 new Date()로) 보여주는 곳이 많아, 실제 한국 시각보다
# 9시간 늦게 표시되는 문제가 있었다. 매 INSERT/UPDATE마다 애플리케이션 쪽에서 이미
# KST(UTC+9)로 보정된 값을 만들어 저장하도록 바꿔, 이후로는 별도 변환 없이 그대로
# 화면에 보여줘도 실제 한국 시각과 일치하게 한다.
def _kst_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)

# Document 테이블: 업로드된 문서 정보를 저장합니다
class Document(Base):
    __tablename__ = "documents"  # 실제 DB에서 쓸 테이블 이름

    id            = Column(Integer, primary_key=True, index=True)  # 고유 번호 (자동 증가)
    filename      = Column(String, nullable=False)                  # 파일 이름
    title         = Column(String, nullable=True)                   # 자동 추출 문서 제목 (파싱 첫 줄)
    saved_path    = Column(String, nullable=False)                  # 저장 경로
    file_type     = Column(String, nullable=True)                   # 파일 형식 (pdf/docx/pptx/xlsx/hwp/hwpx/txt/md/png/jpg)
    original_path = Column(String, nullable=True)                   # 원본 디렉토리 경로 (폴더 업로드 시 보존)
    memo          = Column(Text, nullable=True)                     # 관리자 메모 (문서 설명)
    category      = Column(String, nullable=False, default="report") # 문서 분류 (spec/research/presentation/report/other)
    category_ai_suggested = Column(Boolean, nullable=False, default=False)  # 파싱 완료 후 AI(Ollama)가 이 카테고리를 자동 분류했는지
    category_ai_checked   = Column(Boolean, nullable=False, default=False)  # AI 분류 "시도"가 끝났는지(성공/실패 무관) — 프론트가 분류 완료를 기다릴 때 씀
    status        = Column(String, nullable=False)                  # "success" 또는 "failed"
    error         = Column(Text, nullable=True)                     # 에러 메시지 (없으면 null)
    page_count    = Column(Integer, default=0)                      # 총 페이지 수
    has_flagged   = Column(Boolean, default=False)                  # OCR 저신뢰 페이지 있으면 True
    sha256        = Column(String(64), nullable=True, index=True)   # SHA-256 해시 (중복 체크용)
    view_count    = Column(Integer, nullable=False, default=0)      # 상세보기 조회수
    uploaded_by   = Column(String, nullable=True)                   # 업로드한 사람 이메일(로그인 꺼져 있으면 "anonymous")
    department    = Column(String, nullable=True)                   # 업로드자의 부서로 자동 지정 — 부서별 열람 제한 기준값(비어있으면 제한 없음)
    uploaded_at   = Column(DateTime, default=_kst_now)     # 업로드 시각 (자동 기록, 이후 불변)
    # [수정 2026-07-06] 주기적 스윕이 "parsing이 얼마나 오래됐는지" 판정할 기준 컬럼.
    # uploaded_at은 최초 업로드 시각으로 고정이라 재시도(retry) 시에는 갱신되지 않는다 —
    # 그걸 기준으로 삼으면 방금 재시도를 시작한 문서를 "오래됐다"고 오판해 강제로
    # failed 처리해버릴 수 있다. onupdate=func.now()로 해두면 ORM으로 이 행을 수정하고
    # commit할 때마다(업로드/재시도/파싱결과반영 등 모든 지점에서) 자동으로 갱신된다.
    updated_at    = Column(DateTime, default=_kst_now, onupdate=_kst_now)


# Chunk 테이블: 문서를 청크로 분할하여 저장합니다 (turbovec 벡터 ID와 1:1 대응)
#
# [수정 2026-07-04] id 타입을 with_variant로 분기하는 이유:
#   PostgreSQL → BIGSERIAL (설계도 C5 규칙: 이 id가 곧 turbovec 벡터 ID(uint64))
#   SQLite(로컬 개발) → INTEGER. SQLite는 BIGINT PK에 자동증가를 지원하지 않아
#   기존 코드로는 로컬에서 청크 INSERT가 전부 "NOT NULL constraint failed"로 죽었음.
_BigIntPK = BigInteger().with_variant(Integer, "sqlite")

class Chunk(Base):
    __tablename__ = "chunks"
    # [수정 2026-07-06] SQLite는 이 옵션 없이는 삭제된 rowid를 재사용할 수 있어(테이블
    # 최대값+1만 보장), turbovec에 남은 "죽은" 벡터 id와 새 청크 id가 충돌해 검색 결과가
    # 오염될 위험이 있다. PostgreSQL의 BIGSERIAL/SERIAL(시퀀스)은 삭제된 값을 재사용하지
    # 않으므로, SQLite에도 같은 "재사용 없음" 특성을 강제해 환경별 동작 차이를 없앤다.
    __table_args__ = {"sqlite_autoincrement": True}

    id        = Column(_BigIntPK, primary_key=True, autoincrement=True)   # turbovec 벡터 ID와 1:1
    doc_id    = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    text      = Column(Text, nullable=False)                              # 청크 텍스트
    page_num  = Column(Integer, default=0)                                # 원본 페이지 번호
    chunk_idx = Column(Integer, default=0)                                # 문서 내 청크 순서
    embedding = Column(LargeBinary, nullable=True)                        # float32 bytes, 재시작 시 turbovec 재구성


# User 테이블: 구글 로그인한 사람의 이메일→이름·프로필 사진을 기억해 둔다.
# Document.uploaded_by는 이메일만 저장하므로, "업로드한 사람 이름"을 보여주려면
# 이 테이블에서 이메일로 이름을 찾아와야 한다. /auth/google 로그인 성공 시마다 upsert된다.
class User(Base):
    __tablename__ = "users"

    email      = Column(String, primary_key=True)
    name       = Column(String, nullable=True)
    picture    = Column(String, nullable=True)
    department = Column(String, nullable=True)   # 관리자가 지정 — 부서별 문서 접근 제한의 기준값
    updated_at = Column(DateTime, default=_kst_now, onupdate=_kst_now)


# AdminEmail 테이블: .env의 ADMIN_EMAILS(고정, 삭제 불가)에 더해 웹 화면에서
# 직접 추가·삭제할 수 있는 관리자 이메일 목록. 앱 시작 시 메모리로 로드해두고
# 추가/삭제할 때마다 메모리 캐시도 함께 갱신한다(매 요청마다 DB 조회하지 않기 위함).
class AdminEmail(Base):
    __tablename__ = "admin_emails"

    email      = Column(String, primary_key=True)
    added_by   = Column(String, nullable=True)
    created_at = Column(DateTime, default=_kst_now)


# RevokedSession 테이블: 로그아웃(또는 강제 종료)된 세션의 블랙리스트.
# 세션 토큰(km_session)에 서명해 넣은 무작위 session_id(sid)만 저장한다 — 토큰 원본이나
# 그 해시를 키로 쓰지 않는 이유는, 같은 토큰이라도 바이트 표현이 달라지면 해시가 어긋나
# 블랙리스트를 우회할 수 있기 때문(OWASP JWT 치트시트 권고). sid는 서명 대상 안에 있어
# 위변조가 불가능하다. revoked_at은 SESSION_MAX_AGE보다 오래된 행을 주기적으로 청소하는 데 쓴다.
class RevokedSession(Base):
    __tablename__ = "revoked_sessions"

    session_id = Column(String, primary_key=True)
    revoked_at = Column(DateTime, default=_kst_now)


# SearchLog 테이블: 검색 기록을 저장합니다
class SearchLog(Base):
    __tablename__ = "search_logs"

    id           = Column(Integer, primary_key=True, index=True)  # 고유 번호
    query        = Column(String, nullable=False)                  # 검색어
    alpha        = Column(Float, nullable=False)                   # 의미검색 비중 (0~1)
    result_count = Column(Integer, default=0)                      # 검색 결과 수
    user_email   = Column(String, nullable=True, index=True)       # 검색한 계정(로그인 꺼져있으면 "anonymous")
    searched_at  = Column(DateTime, default=_kst_now)     # 검색 시각 (자동 기록)


# Bookmark 테이블: 사용자별 문서 북마크 (로그인 꺼져 있으면 "anonymous" 단일 사용자로 동작)
class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (
        UniqueConstraint("doc_id", "user_email", name="uq_bookmark_doc_user"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    doc_id     = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=_kst_now)


# Comment 테이블: 문서 상세 팝업에서 남기는 댓글 (로그인 꺼져 있으면 "anonymous" 공용 작성자)
class Comment(Base):
    __tablename__ = "comments"

    id         = Column(Integer, primary_key=True, index=True)
    doc_id     = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = Column(String, nullable=False, index=True)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_kst_now)


# Notification 테이블: 내 문서에 댓글이 달렸을 때 업로더에게 보여주는 인앱 알림.
# 댓글 작성자가 문서 업로더 본인이면 알림을 만들지 않는다(자기 댓글에 자기 알림 방지).
class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, index=True)
    recipient  = Column(String, nullable=False, index=True)   # 알림 받을 사람(문서 업로더) 이메일
    doc_id     = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    message    = Column(Text, nullable=False)
    is_read    = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_kst_now)


# DocumentPermission 테이블: 문서별 읽기 권한 화이트리스트.
# 문서에 이 테이블 행이 하나도 없으면 "전체 공개"(기존 동작 그대로) — 관리자가 최소 한 명을
# 지정하는 순간부터 그 문서는 지정된 사람 + 관리자만 읽을 수 있는 "제한 문서"가 된다.
class DocumentPermission(Base):
    __tablename__ = "document_permissions"
    __table_args__ = (
        UniqueConstraint("doc_id", "user_email", name="uq_permission_doc_user"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    doc_id     = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=_kst_now)


# DeletionLog 테이블: 관리자가 문서를 삭제할 때 누가/언제/무엇을/왜 지웠는지 남기는 감사 로그.
# documents 행 자체는 삭제되고 나면 사라지므로 doc_id를 FK로 걸지 않고, 삭제 시점의
# 파일명·카테고리를 그대로 스냅샷해 둔다(나중에 documents를 조인해도 찾을 수 없기 때문).
# 3개월이 지난 행은 _scheduled_deletion_log_sweep_task가 주기적으로 청소한다(무한 누적 방지).
class DeletionLog(Base):
    __tablename__ = "deletion_logs"

    id         = Column(Integer, primary_key=True, index=True)
    doc_id     = Column(Integer, nullable=False, index=True)
    filename   = Column(String, nullable=False)
    category   = Column(String, nullable=True)
    deleted_by = Column(String, nullable=False, index=True)   # 삭제한 관리자 이메일
    reason     = Column(Text, nullable=False)                 # 삭제 사유 (필수 입력)
    deleted_at = Column(DateTime, default=_kst_now, index=True)
