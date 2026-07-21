// useIsNarrow.js — 창 폭이 breakpoint(기본 1100px, 노트북 반접이 정도) 미만인지 감지한다.
// 좌/우 고정폭 사이드바 레이아웃(Row wrap={false} + Col flex="200px" 등)을 그 아래 폭에서
// 세로로 쌓이게 바꾸는 데 쓴다 — 실제 모바일 대응이 아니라 창을 줄였을 때 깨지는 것 방지용.
import { useState, useEffect } from 'react'

export default function useIsNarrow(breakpoint = 1100) {
  const [isNarrow, setIsNarrow] = useState(() => window.innerWidth < breakpoint)

  useEffect(() => {
    const onResize = () => setIsNarrow(window.innerWidth < breakpoint)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [breakpoint])

  return isNarrow
}
