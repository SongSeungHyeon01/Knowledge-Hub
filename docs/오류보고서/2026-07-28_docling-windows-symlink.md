# Docling 모델 다운로드 시 Windows 심볼릭 링크 권한 오류

- **발생 시각**: 2026-07-28
- **위치**: `docling.document_converter.DocumentConverter().convert()` 최초 호출 (레이아웃 모델 다운로드 단계, huggingface_hub 경유)
- **상태**: 해결됨 (로컬 개발 환경 한정 — 배포 시엔 해당 없음)

## 증상

로컬(Windows)에서 Docling으로 첫 변환을 시도하면 다음 오류로 실패:

```
OSError: [WinError 1314] 클라이언트가 필요한 권한을 가지고 있지 않습니다:
'..\\..\\blobs\\98434...' -> '...\\models--docling-project--docling-layout-heron\\...\\config.json'
```

## 원인 조사

huggingface_hub의 모델 캐시 시스템은 기본적으로 심볼릭 링크를 써서 중복 파일을
효율적으로 저장한다. Windows에서 `os.symlink()`를 호출하려면 개발자 모드가
켜져 있거나 관리자 권한으로 실행 중이어야 하는데, 둘 다 아니었다 — 일반 사용자
권한 PowerShell/터미널에서 실행 중이었음.

## 근본 원인

✅ 확인됨: Windows의 심볼릭 링크 생성 권한 제약. huggingface_hub가 이걸 자동
감지해서 경고("당신의 머신은 심볼릭 링크를 지원하지 않습니다")는 띄우지만,
실제로는 여전히 `os.symlink()`를 시도하다가 그 자리에서 권한 오류로 죽는다 —
경고만 보고 "자동으로 우회해줄 것"이라고 넘겨짚으면 안 된다.

## 해결 방법

환경변수 `HF_HUB_DISABLE_SYMLINKS=1`을 설정하면 심볼릭 링크 대신 일반 파일
복사로 캐시를 구성한다(디스크 공간을 좀 더 쓰지만 동작은 동일).

```bash
HF_HUB_DISABLE_SYMLINKS=1 python your_script.py
```

로컬에서 Docling/huggingface_hub 관련 스크립트를 돌릴 때는 항상 이 환경변수를
같이 켜야 한다 — `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`과 같은 성격의, 이 Windows
머신에서 상시 필요한 환경변수로 취급할 것.

## 재발 방지

- 배포(Railway, Linux 컨테이너)에는 이 문제가 없다 — Linux는 일반 사용자도
  심볼릭 링크를 만들 수 있어서 `Dockerfile`에는 이 환경변수를 넣지 않았다.
  로컬 Windows 개발 환경에서만 필요하다.
- 새로 Hugging Face Hub 기반 모델(Docling, sentence-transformers 등)을 로컬에서
  처음 실행할 때 이 오류를 다시 만나면, 바로 이 환경변수부터 확인할 것.
