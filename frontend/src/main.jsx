// main.jsx — React 앱의 시작점
import React from 'react'
import ReactDOM from 'react-dom/client'
import axios from 'axios'
import { ConfigProvider } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import AdminRoute from './AdminRoute'
import 'antd/dist/reset.css'

// 로그인 세션 쿠키를 (다른 origin인) 백엔드와 주고받으려면 필요
axios.defaults.withCredentials = true

// QueryClient: 서버 데이터 캐싱/갱신을 관리하는 객체
const queryClient = new QueryClient()

// 라우팅 라이브러리 없이 pathname만으로 분기 — "/admin"은 App의 탭 시스템과
// 완전히 분리된 별도 페이지(AdminRoute)로 렌더링한다.
const isAdminRoute = window.location.pathname.startsWith('/admin')

// [2026-07-31] 사내 배포용 "클린 코퍼레이트" 톤 — 네이비 포인트(#1B3A6B) + 중립 그레이.
// 브레인스토밍 목업에서 승인된 색을 전역 토큰으로 반영 — Button/Tag/Radio 등 antd
// 컴포넌트 전체가 이 값을 자동으로 따라간다(화면마다 색을 따로 지정할 필요 없음).
const theme = {
  token: {
    colorPrimary: '#1B3A6B',
    // colorInfo는 일부러 primary(네이비)로 안 맞춘다 — info 계열 Alert/Tag가 네이비를
    // 쓰면 밝은 하늘색 대신 칙칙한 회색톤으로 계산돼(2026-07-31 발견) "어둡다"는 느낌을 줬다.
    // antd 기본 밝은 블루를 그대로 둬서 안내 배너는 가볍게, 강조는 colorPrimary가 담당.
    borderRadius: 8,
  },
  components: {
    // 앱 전체 툴팁을 어두운 기본 배경 대신 흰 배경 + 진한 글씨로 통일
    // (파일 미리보기 툴팁뿐 아니라 작성자·날짜·북마크 등 다른 모든 툴팁에도 일괄 적용)
    Tooltip: {
      colorBgSpotlight: '#ffffff',
      colorTextLightSolid: '#333333',
    },
    // [2026-07-31] 좌측 서브메뉴(업로드 등) 선택 항목이 기본 회색 블록으로 나오던 것을
    // 네이비 톤 옅은 배경으로 교체 — 상단 내비(theme="dark")는 별도 팔레트를 쓰므로
    // 이 값의 영향을 안 받는다.
    Menu: {
      itemSelectedBg: '#E9EEF7',
      itemSelectedColor: '#1B3A6B',
      itemHoverBg: '#F1F4F9',
      itemHoverColor: '#1B3A6B',
      itemActiveBg: '#E9EEF7',
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ConfigProvider theme={theme}>
    <QueryClientProvider client={queryClient}>
      {isAdminRoute ? <AdminRoute /> : <App />}
    </QueryClientProvider>
  </ConfigProvider>
)
