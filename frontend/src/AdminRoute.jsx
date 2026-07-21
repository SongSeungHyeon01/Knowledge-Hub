// AdminRoute.jsx — /admin 전용 독립 진입점
// App.jsx의 탭 시스템과 완전히 분리된 별도 페이지. main.jsx가 pathname을 보고
// "/admin"으로 시작하면 App 대신 이 컴포넌트를 렌더링한다.
// 로그인·관리자 권한 확인 로직은 App.jsx와 동일한 규칙(authEnabled/isAdmin)을 그대로 따른다.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Layout, Spin, Result, Button } from 'antd'
import axios from 'axios'
import useIsNarrow from './useIsNarrow'
import LoginPage from './LoginPage'
import AdminPage from './AdminPage'

const { Header, Content } = Layout
const API = import.meta.env.VITE_API_URL

const goHome = () => { window.location.href = '/' }

export default function AdminRoute() {
  const isNarrow = useIsNarrow()
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
        <div onClick={goHome} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', flexShrink: 0 }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, background: '#f1f5f9',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <svg width="18" height="18" viewBox="0 0 32 32">
              <g fill="none" stroke="#64748b" strokeWidth="1.6" strokeLinejoin="round">
                <rect x="6" y="10" width="10" height="17" rx="1" />
                <rect x="17" y="14" width="9" height="13" rx="1" />
                <rect x="9" y="13" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="9" y="17" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="9" y="21" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="12.5" y="13" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="12.5" y="17" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="12.5" y="21" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="19.5" y="17" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="19.5" y="21" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="22.5" y="17" width="1.6" height="1.6" fill="#64748b" stroke="none" />
                <rect x="22.5" y="21" width="1.6" height="1.6" fill="#64748b" stroke="none" />
              </g>
            </svg>
          </div>
          {!isNarrow && (
            <div style={{ lineHeight: 1.25 }}>
              <div style={{ fontWeight: 800, fontSize: 15, color: '#1a1a1a' }}>Knowledge Hub · 관리자</div>
              <div style={{ fontSize: 11, color: '#8c8c8c' }}>코싸이온(주)</div>
            </div>
          )}
        </div>
        {/* 관리자 화면의 좌측 서브메뉴가 상단 탭으로 옮겨와 여기에 포털로 렌더링됨
            — AdminPage가 실제 메뉴 항목·선택 상태를 들고 있고, 이 슬롯으로 포털만 쏴준다.
            넓을 때는 로고·메인으로 버튼 폭이 서로 달라도 항상 화면 정중앙에 오도록 flex
            흐름에서 빼고 절대 위치로 중앙 정렬한다. 창이 좁아지면(탭이 다 안 들어감) 겹치지
            않게 절대 위치를 끄고 남는 공간 안에서만 정렬 — AdminPage 쪽에서 이 경우
            disabledOverflow를 꺼서 안 들어가는 탭은 "..." 더보기로 자동 접히게 한다. */}
        <div id="admin-nav-slot" style={isNarrow
          ? { flex: 1, display: 'flex', justifyContent: 'center', minWidth: 0, overflow: 'hidden' }
          : { position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}
        />
        <div style={{ flexShrink: 0 }}>
          <Button onClick={goHome}>{isNarrow ? '메인' : '메인으로'}</Button>
        </div>
      </Header>

      <Content style={{ minHeight: 'calc(100vh - 64px)' }}>
        <AdminPage onNavigate={goHome} />
      </Content>
    </Layout>
  )
}
