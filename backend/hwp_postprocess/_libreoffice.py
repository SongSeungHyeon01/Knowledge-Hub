"""LibreOffice headless 변환 헬퍼 (HWP→DOCX, PPTX→PDF)"""
import shutil
import subprocess
import tempfile
from pathlib import Path

_WINDOWS_SOFFICE = r"C:\Program Files\LibreOffice\program\soffice.exe"


def _find_soffice() -> str | None:
    """libreoffice 또는 soffice 실행 파일 경로를 반환. 없으면 None."""
    for cmd in ("libreoffice", "soffice"):
        found = shutil.which(cmd)
        if found:
            return found
    if Path(_WINDOWS_SOFFICE).exists():
        return _WINDOWS_SOFFICE
    return None


def convert(src_path: str, target_fmt: str) -> str:
    """
    LibreOffice headless 로 src_path 를 target_fmt 으로 변환한다.

    Args:
        src_path:   원본 파일 절대 경로
        target_fmt: 변환 포맷 문자열 ("docx", "pdf" 등)

    Returns:
        변환된 파일의 절대 경로 (호출자가 직접 삭제해야 함)

    Raises:
        RuntimeError: LibreOffice 미설치 또는 변환 실패
    """
    soffice = _find_soffice()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice가 설치되어 있지 않습니다. "
            "Dockerfile에 'libreoffice' 패키지가 포함되어 있는지 확인하세요."
        )

    out_dir = tempfile.mkdtemp(prefix="km_lo_")
    lo_profile = Path(out_dir) / "lo_profile"

    try:
        result = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file://{lo_profile}",
                "--headless",
                "--convert-to", target_fmt,
                "--outdir", out_dir,
                src_path,
            ],
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"LibreOffice 변환 타임아웃 (120s): {src_path}")

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"LibreOffice 변환 실패 ({src_path}): {stderr[:300]}")

    stem = Path(src_path).stem
    out_path = Path(out_dir) / f"{stem}.{target_fmt}"
    if not out_path.exists():
        # 컨테이너에 UTF-8 로케일이 없으면 LibreOffice가 한글 등 비ASCII 파일명을
        # 내부적으로 다르게 처리해, soffice는 성공(exit 0)했는데도 예상한 이름의
        # 파일이 없는 경우가 생긴다. 정확한 이름 대신 out_dir 안의 확장자만
        # 일치하는 파일을 찾아 그걸 결과로 쓴다(진짜 변환 실패와는 구분됨 —
        # 그 경우 out_dir가 아예 비어 있음).
        candidates = list(Path(out_dir).glob(f"*.{target_fmt}"))
        if len(candidates) == 1:
            return str(candidates[0])
        # 진단용: exit 0인데도 결과 파일이 하나도 없는 "조용한 실패" 원인을
        # 알아내기 위해 stdout/stderr와 out_dir 실제 내용물을 그대로 남긴다.
        stdout = result.stdout.decode(errors="replace").strip()
        stderr = result.stderr.decode(errors="replace").strip()
        listing = [p.name for p in Path(out_dir).iterdir()]
        raise RuntimeError(
            f"변환 결과 파일을 찾을 수 없습니다: {out_path} | "
            f"out_dir 내용물: {listing} | stdout: {stdout[:300]} | stderr: {stderr[:300]}"
        )

    return str(out_path)
