# models.py — 데이터베이스 테이블 정의 파일
# 여기서 정의한 클래스 하나 = 데이터베이스 테이블 하나

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
from sqlalchemy.sql import func
from database import Base

# Document 테이블: 업로드된 문서 정보를 저장합니다
class Document(Base):
    __tablename__ = "documents"  # 실제 DB에서 쓸 테이블 이름

    id            = Column(Integer, primary_key=True, index=True)  # 고유 번호 (자동 증가)
    filename      = Column(String, nullable=False)                  # 파일 이름
    title         = Column(String, nullable=True)                   # 자동 추출 문서 제목 (파싱 첫 줄)
    saved_path    = Column(String, nullable=False)                  # 저장 경로
    file_type     = Column(String, nullable=True)                   # 파일 형식 (pdf/docx/pptx/xlsx/hwp/hwpx/txt/md/png/jpg)
    original_path = Column(String, nullable=True)                   # 원본 디렉토리 경로 (폴더 업로드 시 보존)
    memo          = Column(Text, nullable=True)                        # 관리자 메모 (문서 설명)
    category      = Column(String, nullable=False, default="report") # 문서 분류 (spec/research/presentation/report)
    status        = Column(String, nullable=False)                  # "success" 또는 "failed"
    error         = Column(Text, nullable=True)                     # 에러 메시지 (없으면 null)
    page_count    = Column(Integer, default=0)                      # 총 페이지 수
    has_flagged   = Column(Boolean, default=False)                  # OCR 저신뢰 페이지 있으면 True
    uploaded_at   = Column(DateTime, server_default=func.now())     # 업로드 시각 (자동 기록)


# SearchLog 테이블: 검색 기록을 저장합니다
class SearchLog(Base):
    __tablename__ = "search_logs"

    id           = Column(Integer, primary_key=True, index=True)  # 고유 번호
    query        = Column(String, nullable=False)                  # 검색어
    alpha        = Column(Float, nullable=False)                   # 의미검색 비중 (0~1)
    result_count = Column(Integer, default=0)                      # 검색 결과 수
    searched_at  = Column(DateTime, server_default=func.now())     # 검색 시각 (자동 기록)
