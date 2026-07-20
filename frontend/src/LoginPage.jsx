// LoginPage.jsx — 로그인 화면 (구글 계정, 회사 도메인 제한)
// AUTH_ENABLED(백엔드에 GOOGLE_CLIENT_ID가 설정된 경우)일 때만 App.jsx가 이 화면을 띄운다.
// Google Identity Services(index.html에 스크립트 추가됨)의 렌더 버튼을 사용 —
// 성공하면 credential(ID 토큰)을 그대로 백엔드로 보내 서버에서 서명·도메인을 검증한다.

import { useEffect, useRef, useState } from 'react'
import { Typography, Alert, Spin } from 'antd'
import axios from 'axios'

const { Title, Text } = Typography

const API = import.meta.env.VITE_API_URL

export default function LoginPage({ googleClientId, onLogin }) {
  const buttonRef = useRef(null)
  const [error, setError] = useState(null)
  const [verifying, setVerifying] = useState(false)

  useEffect(() => {
    let cancelled = false

    const handleCredential = async (response) => {
      setVerifying(true)
      setError(null)
      try {
        const res = await axios.post(`${API}/auth/google`, { credential: response.credential })
        onLogin(res.data)
      } catch (err) {
        setError(err.response?.data?.detail ?? '로그인에 실패했습니다')
      } finally {
        setVerifying(false)
      }
    }

    // index.html의 <script>가 비동기 로드되므로, 준비될 때까지 짧게 재시도
    const tryInit = () => {
      if (cancelled) return
      if (!window.google?.accounts?.id) {
        setTimeout(tryInit, 150)
        return
      }
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: handleCredential,
      })
      if (buttonRef.current) {
        window.google.accounts.id.renderButton(buttonRef.current, {
          type: 'standard', theme: 'outline', size: 'large', text: 'signin_with', width: 280,
        })
      }
    }
    tryInit()

    return () => { cancelled = true }
  }, [googleClientId]) // eslint-disable-line

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', background: '#f7f8fa', padding: 24,
    }}>
      <div style={{
        width: 30, height: 30, borderRadius: 8, background: '#1677ff', marginBottom: 16,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path d="M12 2 L22 7 L12 12 L2 7 Z" fill="#fff" opacity="0.95" />
          <path d="M2 7 L12 12 L12 22 L2 17 Z" fill="#fff" opacity="0.75" />
          <path d="M22 7 L12 12 L12 22 L22 17 Z" fill="#fff" opacity="0.6" />
        </svg>
      </div>
      <Title level={3} style={{ margin: '0 0 4px' }}>Knowledge Hub</Title>
      <Text type="secondary" style={{ marginBottom: 28 }}>회사 계정으로 로그인해 주세요</Text>

      <div style={{
        background: '#fff', border: '1px solid #eef0f2', borderRadius: 12,
        boxShadow: '0 1px 4px rgba(0,0,0,0.06)', padding: '28px 32px',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, minWidth: 320,
      }}>
        {verifying ? <Spin tip="확인 중..." /> : <div ref={buttonRef} />}
        {error && <Alert type="error" showIcon message={error} style={{ width: '100%' }} />}
      </div>
    </div>
  )
}
