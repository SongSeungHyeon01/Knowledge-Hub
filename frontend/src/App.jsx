// App.jsx — 앱 전체 레이아웃 (접히는 사이드바)

import { Layout, Menu } from 'antd'
import {
  UploadOutlined,
  DashboardOutlined,
  SearchOutlined,
  BookOutlined,
} from '@ant-design/icons'
import { useState, useEffect } from 'react'
import UploadPage from './UploadPage'
import AdminPage from './AdminPage'
import SearchPage from './SearchPage'

const { Sider, Content } = Layout

export default function App() {
  const [current,   setCurrent]   = useState('upload')
  const [collapsed, setCollapsed] = useState(false)

  // "/" 단축키: 다른 탭에서 누르면 검색 탭으로 이동 (검색 탭에서는 SearchPage가 직접 처리)
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

  const menuItems = [
    { key: 'upload', icon: <UploadOutlined />,    label: '문서 업로드' },
    { key: 'search', icon: <SearchOutlined />,    label: '문서 검색' },
    { key: 'admin',  icon: <DashboardOutlined />, label: '관리자' },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="dark"
        style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'hidden' }}
      >
        {/* 브랜드 로고 */}
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'flex-start',
          padding: collapsed ? 0 : '0 20px',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          gap: 10,
        }}>
          <BookOutlined style={{ fontSize: 20, color: '#4096ff', flexShrink: 0 }} />
          {!collapsed && (
            <span style={{
              color: 'white',
              fontWeight: 700,
              fontSize: 15,
              whiteSpace: 'nowrap',
              letterSpacing: '-0.3px',
            }}>
              사내 지식관리
            </span>
          )}
        </div>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[current]}
          onClick={(e) => setCurrent(e.key)}
          items={menuItems}
          style={{ marginTop: 8 }}
        />
      </Sider>

      <Layout>
        <Content style={{ background: '#f5f5f5', minHeight: '100vh' }}>
          {current === 'upload' && <UploadPage onNavigate={setCurrent} />}
          {current === 'search' && <SearchPage />}
          {current === 'admin'  && <AdminPage onNavigate={setCurrent} />}
        </Content>
      </Layout>
    </Layout>
  )
}
