# database.py — 데이터베이스 연결 설정 파일
# DATABASE_URL env var 로 PostgreSQL(asyncpg) 연결. 없으면 SQLite fallback.

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# DATABASE_URL 환경변수가 있으면 PostgreSQL, 없으면 SQLite fallback
_raw_url = os.environ.get("DATABASE_URL", "")
if _raw_url:
    # DATABASE_URL 스킴 정규화 — 플랫폼마다 다른 postgres 스킴을 asyncpg 드라이버로 통일한다.
    # 이미 드라이버가 명시된 URL(postgresql+asyncpg:// 등)은 손대지 않는다.
    # 더 긴 문자열(postgresql://)을 먼저 검사해 postgres://와 혼동되지 않게 한다.
    if _raw_url.startswith("postgresql+"):
        pass  # 이미 드라이버 지정됨 — 그대로 사용
    elif _raw_url.startswith("postgresql://"):
        # Railway 등 표준 스킴을 asyncpg 드라이버 URL로 치환
        _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif _raw_url.startswith("postgres://"):
        # Heroku 등 구식 스킴을 asyncpg 드라이버 URL로 치환
        _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    DATABASE_URL = _raw_url
    _is_postgres = True
else:
    # SQLite fallback — km.db 파일을 database.py 와 같은 디렉토리에 저장
    _base = os.path.dirname(os.path.abspath(__file__))
    DATABASE_URL = f"sqlite+aiosqlite:///{os.path.join(_base, 'km.db')}"
    _is_postgres = False

# 데이터베이스 엔진 생성
if _is_postgres:
    engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
else:
    engine = create_async_engine(DATABASE_URL, echo=False)

# 세션 = DB에 명령을 보내는 통로
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# 모든 테이블 클래스가 상속받을 기본 클래스
class Base(DeclarativeBase):
    pass

# FastAPI 엔드포인트에서 DB 세션을 사용할 때 쓰는 함수
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
