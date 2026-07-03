# database.py — 데이터베이스 연결 설정 파일
# SQLite 파일을 만들고 FastAPI와 연결하는 역할을 합니다

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# 데이터베이스 파일 경로 (KM 폴더 안에 km.db 파일로 저장됩니다)
DATABASE_URL = "sqlite+aiosqlite:////Users/kimkibin/KM/km.db"

# 데이터베이스 엔진 생성 (실제 DB 파일과 연결하는 객체)
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
