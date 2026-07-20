// AdminRoute.jsx — /admin 전용 독립 진입점
// App.jsx의 탭 시스템과 완전히 분리된 별도 페이지. main.jsx가 pathname을 보고
// "/admin"으로 시작하면 App 대신 이 컴포넌트를 렌더링한다.
// 로그인·관리자 권한 확인 로직은 App.jsx와 동일한 규칙(authEnabled/isAdmin)을 그대로 따른다.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Layout, Spin, Result, Button } from 'antd'
import axios from 'axios'
import LoginPage from './LoginPage'
import AdminPage from './AdminPage'

const { Header, Content } = Layout
const API = import.meta.env.VITE_API_URL

const goHome = () => { window.location.href = '/' }

export default function AdminRoute() {
  const queryClient = useQueryClient()

  const { data: authConfig, isLoading: authConfigLoading } = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => axios.get(`${API}/auth/config`).then(r => r.data),
  })
  const authEnabled = authConfig?.enabled ?? false

  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ['auth-me'],
    queryFn: () => axios.get(`${API}/auth/me`).then(r => r.data),
    enabled: authEnabled,
    retry: false,
  })

  const isAdmin = !authEnabled || !!me?.is_admin

  const handleLogin = () => {
    queryClient.invalidateQueries({ queryKey: ['auth-me'] })
  }

  if (authConfigLoading || (authEnabled && meLoading)) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (authEnabled && !me) {
    return <LoginPage googleClientId={authConfig.google_client_id} onLogin={handleLogin} />
  }

  if (!isAdmin) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Result
          status="403"
          title="접근 권한이 없습니다"
          subTitle="관리자 권한이 있는 계정으로 로그인해야 볼 수 있는 페이지입니다."
          extra={<Button type="primary" onClick={goHome}>메인으로 돌아가기</Button>}
        />
      </div>
    )
  }

  return (
    <Layout style={{ minHeight: '100vh', background: '#f7f8fa' }}>
      <Header
        style={{
          background: '#fff', borderBottom: '1px solid #eef0f2', display: 'flex',
          alignItems: 'center', justifyContent: 'space-between', padding: '0 28px', height: 64, lineHeight: '64px',
          position: 'sticky', top: 0, zIndex: 100,
        }}
      >
        <div onClick={goHome} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, background: '#1677ff',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path d="M12 2 L22 7 L12 12 L2 7 Z" fill="#fff" opacity="0.95" />
              <path d="M2 7 L12 12 L12 22 L2 17 Z" fill="#fff" opacity="0.75" />
              <path d="M22 7 L12 12 L12 22 L22 17 Z" fill="#fff" opacity="0.6" />
            </svg>
          </div>
          <div style={{ fontWeight: 800, fontSize: 15, color: '#1a1a1a' }}>Knowledge Hub · 관리자</div>
        </div>
        {/* 관리자 화면의 좌측 서브메뉴가 상단 탭으로 옮겨와 여기에 포털로 렌더링됨
            — AdminPage가 실제 메뉴 항목·선택 상태를 들고 있고, 이 슬롯으로 포털만 쏴준다.
            로고·메인으로 버튼 폭이 서로 달라도 항상 화면 정중앙에 오도록 flex 흐름에서 빼고
            절대 위치로 중앙 정렬한다 (Header가 position: sticky라 이 absolute의 기준이 됨). */}
        <div id="admin-nav-slot" style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)' }} />
        <div>
          <Button onClick={goHome}>메인으로</Button>
        </div>
      </Header>

      <Content style={{ minHeight: 'calc(100vh - 64px)' }}>
        <AdminPage onNavigate={goHome} />
      </Content>
    </Layout>
  )
}
