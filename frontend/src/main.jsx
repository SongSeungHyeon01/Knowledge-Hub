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

// 앱 전체 툴팁을 어두운 기본 배경 대신 흰 배경 + 진한 글씨로 통일
// (파일 미리보기 툴팁뿐 아니라 작성자·날짜·북마크 등 다른 모든 툴팁에도 일괄 적용)
const theme = {
  components: {
    Tooltip: {
      colorBgSpotlight: '#ffffff',
      colorTextLightSolid: '#333333',
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
