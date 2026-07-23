// App.jsx — 앱 전체 레이아웃 (상단 가로 내비게이션)
// 2026-07-11 목업 반영: 좌측 세로 사이드바 → 상단 가로 내비게이션(업로드·검색·관리자 3개 동급 탭)으로 구조 변경.
// 관리자를 검색 탭 내 Drawer로 숨기던 이전 방식(07/02 회의)은 폐기 — 목업이 관리자를 최상위 탭으로 그림.
// 2026-07-15 로그인 반영: 백엔드에 GOOGLE_CLIENT_ID가 설정된 경우에만 로그인이 활성화된다
// (/auth/config로 확인). 꺼져 있으면 이전과 동일하게 무인증으로 동작 — 로컬 개발 중 Google
// Cloud 설정 전에도 앱이 막히지 않도록 하기 위함. 켜지면 /auth/me로 세션 확인 후
// 없으면 LoginPage, 있으면 기존 3탭 레이아웃 + 헤더에 사용자 정보/로그아웃 버튼을 보여준다.

import { Layout, Menu, Button, FloatButton, Spin, Avatar, Dropdown, Tooltip, Tag, Popover, Badge, List, Empty, Typography } from 'antd'
import {
  UploadOutlined,
  SearchOutlined,
  SettingOutlined,
  UserOutlined,
  LogoutOutlined,
  StarOutlined,
  IdcardOutlined,
  BellOutlined,
  CloseOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import useIsNarrow from './useIsNarrow'
import UploadPage from './UploadPage'
import SearchPage from './SearchPage'
import LoginPage from './LoginPage'
import BookmarksPage from './BookmarksPage'
import MyPage from './MyPage'

const { Header, Content } = Layout

const API = import.meta.env.VITE_API_URL

export default function App() {
  const isNarrow = useIsNarrow()
  const [current,   setCurrent]   = useState('search')
  // 로고 클릭 시 항상 메인(검색) 화면으로 — 이미 검색 탭이어도 검색 결과·필터 등
  // 남아 있는 내부 상태를 지우고 처음 화면으로 되돌리기 위해 key를 바꿔 강제로 다시 마운트한다
  const [homeKey,   setHomeKey]    = useState(0)
  const goHome = () => { setCurrent('search'); setHomeKey(k => k + 1) }
  const queryClient = useQueryClient()

  // 로그인 활성화 여부 — 백엔드에 GOOGLE_CLIENT_ID가 설정돼 있어야 true
  const { data: authConfig, isLoading: authConfigLoading } = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => axios.get(`${API}/auth/config`).then(r => r.data),
  })
  const authEnabled = authConfig?.enabled ?? false

  // 로그인이 켜져 있을 때만 현재 세션 확인 (꺼져 있으면 호출 자체를 안 함)
  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ['auth-me'],
    queryFn: () => axios.get(`${API}/auth/me`).then(r => r.data),
    enabled: authEnabled,
    retry: false,
  })

  const isAdmin = !authEnabled || !!me?.is_admin

  // 알림 — 내 문서에 댓글이 달렸을 때. 안 읽은 개수는 가볍게 주기적으로 폴링하고,
  // 실제 목록(제목·내용 포함)은 벨을 열었을 때만 가져온다.
  const [notifOpen, setNotifOpen] = useState(false)
  const { data: unreadCount } = useQuery({
    queryKey: ['notif-unread'],
    queryFn: () => axios.get(`${API}/notifications/unread-count`).then(r => r.data.count),
    enabled: authEnabled && !!me,
    refetchInterval: 20000,
  })
  const { data: notifications = [] } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => axios.get(`${API}/notifications`).then(r => r.data),
    enabled: authEnabled && !!me && notifOpen,
  })
  const openNotification = async (n) => {
    if (!n.is_read) {
      await axios.post(`${API}/notifications/${n.id}/read`)
      queryClient.invalidateQueries({ queryKey: ['notif-unread'] })
      queryClient.invalidateQueries({ queryKey: ['notifications'] })
    }
    localStorage.setItem('km_launch_query', n.doc_label)
    localStorage.setItem('km_launch_doc_id', String(n.doc_id))
    setCurrent('search')
    setHomeKey(k => k + 1)  // 이미 검색 탭이어도 SearchPage를 새로 마운트해 방금 넣은 검색어를 바로 실행시킨다
    setNotifOpen(false)
  }
  const markAllNotificationsRead = async () => {
    await axios.post(`${API}/notifications/read-all`)
    queryClient.invalidateQueries({ queryKey: ['notif-unread'] })
    queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }
  const deleteNotification = async (id) => {
    await axios.delete(`${API}/notifications/${id}`)
    queryClient.invalidateQueries({ queryKey: ['notif-unread'] })
    queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const handleLogin = () => {
    queryClient.invalidateQueries({ queryKey: ['auth-me'] })
  }
  const handleLogout = async () => {
    await axios.post(`${API}/auth/logout`)
    // invalidateQueries만으로는 부족하다 — /auth/me 재요청이 401로 실패해도 react-query는
    // 기본적으로 직전 성공 데이터(me)를 그대로 들고 있어서 로그아웃 후에도 화면이 안 바뀐다.
    // 캐시를 직접 비워 me를 즉시 null로 만든다.
    queryClient.setQueryData(['auth-me'], null)
    queryClient.invalidateQueries({ queryKey: ['auth-me'] })
  }

  // "내 정보" 탭은 로그인 상태에서만 존재 — 로그아웃되거나 로그인 기능이 꺼지면 검색 탭으로 되돌린다
  useEffect(() => {
    if (current === 'myinfo' && !(authEnabled && me)) setCurrent('search')
  }, [current, authEnabled, me])

  // "/" 단축키: 검색 탭이 아닐 때 누르면 검색 탭으로 이동
  useEffect(() => {
    const handler = (e) => {
      if (
        e.key === '/' &&
        !['INPUT', 'TEXTAREA'].includes(e.target.tagName) &&
        !e.target.isContentEditable &&
        current !== 'search'
      ) {
        e.preventDefault()
        setCurrent('search')
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [current])

  // 검색 탭은 하위 페이지가 자체 좌측 서브메뉴를 갖고 있어서, 상단 내비게이션은 호버
  // 드롭다운 없이 단순 평탭으로만 둔다. 관리자는 이제 이 SPA의 탭이 아니라 완전히 별도
  // 주소(/admin, AdminRoute.jsx)에서 열리는 독립 페이지 — 우측 상단에 링크로만 노출한다.
  const menuItems = [
    { key: 'search', icon: <SearchOutlined />, label: '검색' },
    { key: 'upload', icon: <UploadOutlined />, label: '업로드' },
    { key: 'bookmarks', icon: <StarOutlined />, label: '북마크' },
  ]

  // 로그인 활성화 여부·세션 확인이 끝나기 전까지는 깜빡임 없이 로딩만 표시
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

  return (
    <Layout style={{ minHeight: '100vh', background: '#f7f8fa' }}>
      <Header
        style={{
          background: '#fff',
          borderBottom: '1px solid #eef0f2',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 28px',
          height: 64,
          lineHeight: '64px',
          position: 'sticky',
          top: 0,
          zIndex: 100,
        }}
      >
        {/* 좌측: 브랜드 (클릭 시 항상 메인 화면인 검색 탭으로 이동, 내부 상태도 초기화) */}
        <div
          onClick={goHome}
          style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: isNarrow ? 0 : 220, cursor: 'pointer', flexShrink: 0 }}
        >
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
              <div style={{ fontWeight: 800, fontSize: 15, color: '#1a1a1a' }}>Knowledge Hub</div>
              <div style={{ fontSize: 11, color: '#8c8c8c' }}>코싸이온(주)</div>
            </div>
          )}
        </div>

        {/* 중앙: 탭 메뉴 — 넓을 때는 좌측 로고·우측 계정 영역 폭이 달라도(이메일 길이 등)
            항상 화면 정중앙에 오도록 flex 흐름에서 빼고 절대 위치로 중앙 정렬한다
            (Header가 position: sticky라 이 absolute의 기준이 됨). 창이 좁아지면 로고·계정
            영역과 겹칠 수 있어 절대 위치를 끄고 남는 공간 안에서만 가운데 정렬한다. */}
        <div style={isNarrow
          ? { flex: 1, display: 'flex', justifyContent: 'center', minWidth: 0, overflow: 'hidden' }
          : { position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}>
          <Menu
            mode="horizontal"
            selectedKeys={[current]}
            onClick={(e) => setCurrent(e.key)}
            items={menuItems}
            style={{ border: 'none', minWidth: isNarrow ? 0 : 320, justifyContent: 'center' }}
          />
        </div>

        {/* 우측: 알림 벨 + 관리자 링크(별도 페이지, /admin) + 계정 드롭다운(로그인 활성화 시 — 내 정보·로그아웃) */}
        <div style={{ minWidth: isNarrow ? 0 : 220, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          {authEnabled && me && (
            <Popover
              open={notifOpen}
              onOpenChange={setNotifOpen}
              trigger="click"
              placement="bottomRight"
              content={
                <div style={{ width: 320, maxHeight: 400, overflowY: 'auto' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <Typography.Text strong>알림</Typography.Text>
                    <Button type="link" size="small" style={{ padding: 0 }} onClick={markAllNotificationsRead}>모두 읽음</Button>
                  </div>
                  {notifications.length === 0 ? (
                    <Empty description="알림이 없습니다" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  ) : (
                    <List
                      size="small"
                      dataSource={notifications}
                      renderItem={(n) => (
                        <List.Item
                          style={{ cursor: 'pointer', background: n.is_read ? 'transparent' : '#f0f5ff', padding: '8px 6px', borderRadius: 6 }}
                          onClick={() => openNotification(n)}
                        >
                          <div style={{ display: 'flex', alignItems: 'flex-start', width: '100%', gap: 4 }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: 12.5 }}>
                                <Typography.Text strong>{n.commenter_name}</Typography.Text>
                                <Typography.Text style={{ marginLeft: 4 }}>님이 댓글을 남겼습니다</Typography.Text>
                              </div>
                              {/* 문서명은 길면 한 줄로 잘라 보여준다 — 문장 안에 태그로 끼워넣으면 줄바꿈으로 깨져서 별도 줄로 분리 */}
                              <Tooltip title={n.doc_label} placement="bottomLeft">
                                <div style={{
                                  fontSize: 11.5, color: '#1677ff', marginTop: 3,
                                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                }}>
                                  <FileTextOutlined style={{ marginRight: 4 }} />{n.doc_label}
                                </div>
                              </Tooltip>
                              {/* 목록에선 한 줄로 잘라 보여주고, 잘린 전체 내용은 마우스를 올리면 툴팁으로 바로 확인 */}
                              <Tooltip title={n.comment_content ?? n.message} placement="bottomLeft">
                                <div style={{ fontSize: 12, color: '#595959', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {n.comment_content ?? n.message}
                                </div>
                              </Tooltip>
                              <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 2 }}>{(n.created_at || '').slice(0, 16)}</div>
                            </div>
                            <Tooltip title="알림 삭제">
                              <Button
                                type="text" size="small" icon={<CloseOutlined style={{ fontSize: 11 }} />}
                                style={{ flexShrink: 0 }}
                                onClick={(e) => { e.stopPropagation(); deleteNotification(n.id) }}
                              />
                            </Tooltip>
                          </div>
                        </List.Item>
                      )}
                    />
                  )}
                </div>
              }
            >
              <Badge count={unreadCount ?? 0} size="small">
                <Button shape="circle" icon={<BellOutlined />} />
              </Badge>
            </Popover>
          )}
          {isAdmin && (
            <Button icon={<SettingOutlined />} onClick={() => { window.location.href = '/admin' }}>
              {!isNarrow && '관리자'}
            </Button>
          )}
          {authEnabled && me && (
            <Dropdown
              menu={{
                items: [
                  { key: 'myinfo', icon: <IdcardOutlined />, label: '내 정보', onClick: () => setCurrent('myinfo') },
                  { type: 'divider' },
                  { key: 'logout', icon: <LogoutOutlined />, label: '로그아웃', onClick: handleLogout },
                ],
              }}
              placement="bottomRight"
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', minWidth: 0 }}>
                <Avatar size={28} src={me.picture} icon={!me.picture && <UserOutlined />} />
                {!isNarrow && (
                  <span style={{ fontSize: 13, color: '#595959', lineHeight: 1.4, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{me.name ?? me.email}</span>
                )}
                {isAdmin && !isNarrow && (
                  <Tag color="blue" style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 6px' }}>관리자</Tag>
                )}
              </div>
            </Dropdown>
          )}
        </div>
      </Header>

      <Content style={{ minHeight: 'calc(100vh - 64px)' }}>
        {current === 'upload'    && <UploadPage    key={homeKey} onNavigate={setCurrent} />}
        {current === 'search'    && <SearchPage    key={homeKey} onNavigate={setCurrent} />}
        {current === 'bookmarks' && <BookmarksPage key={homeKey} onNavigate={setCurrent} />}
        {current === 'myinfo' && authEnabled && me && <MyPage me={me} />}
      </Content>

      {/* 좌측 하단: 로그인 상태 표시 — 로그인 기능이 켜져 있을 때만 노출(꺼져 있으면 표시할 상태가 없음) */}
      {authEnabled && me && (
        <div
          style={{
            position: 'fixed', left: 16, bottom: 16, zIndex: 200,
            display: 'flex', alignItems: 'center', gap: 10,
            background: '#fff', border: '1px solid #eef0f2', borderRadius: 10,
            padding: '8px 10px 8px 12px', boxShadow: '0 2px 10px rgba(0,0,0,0.08)',
            minWidth: 200, maxWidth: 260,
          }}
        >
          <div
            onClick={() => setCurrent('myinfo')}
            style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0, cursor: 'pointer' }}
          >
            <Avatar size={30} src={me.picture} icon={!me.picture && <UserOutlined />} />
            <div style={{ minWidth: 0 }}>
              <div style={{
                fontSize: 12.5, fontWeight: 600, color: '#1a1a1a',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {me.name ?? me.email}
              </div>
              <div style={{ fontSize: 11, color: '#52c41a', display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#52c41a' }} />
                로그인됨
              </div>
            </div>
          </div>
          <Tooltip title="로그아웃">
            <Button size="small" type="text" icon={<LogoutOutlined />} onClick={handleLogout} />
          </Tooltip>
        </div>
      )}

      <FloatButton.BackTop visibilityHeight={300} />
    </Layout>
  )
}
