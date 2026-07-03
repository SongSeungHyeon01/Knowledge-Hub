// SearchPage.jsx — 검색 화면
// 검색어 입력 + 최근 검색어 + 카테고리 필터 + alpha 슬라이더 + 결과 카드
// 관리자 기능은 우상단 톱니 아이콘 → Drawer로 접근 (07/02 회의: 별도 탭 제거)

import { useState, useEffect, useRef } from 'react'
import {
  Input, Slider, Button, Card, Tag, Typography,
  Space, Divider, Empty, Spin, Row, Col, Radio, List, Pagination, Tooltip, Alert, Select, Drawer
} from 'antd'
import {
  SearchOutlined, FileTextOutlined, HistoryOutlined, CloseOutlined,
  FilePdfOutlined, FileWordOutlined, FilePptOutlined, FileExcelOutlined,
  FileImageOutlined, FileMarkdownOutlined, FileOutlined, DownloadOutlined,
  RobotOutlined, SettingOutlined,
} from '@ant-design/icons'
import axios from 'axios'
import AdminPage from './AdminPage'

const { Title, Text, Paragraph } = Typography

// 검색어를 토큰별로 분리해 각각 노란색으로 강조합니다
const highlight = (text, keyword) => {
  if (!keyword || !text) return text
  const tokens = keyword.trim().split(/\s+/).filter(Boolean)
  if (!tokens.length) return text
  const escaped = tokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  const lowerTokens = new Set(tokens.map(t => t.toLowerCase()))
  return parts.map((part, i) =>
    lowerTokens.has(part.toLowerCase())
      ? <mark key={i} style={{ background: '#fff3a3', padding: '0 1px', borderRadius: 2 }}>{part}</mark>
      : part
  )
}

const API = import.meta.env.VITE_API_URL

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const date = new Date(dateStr.replace(' ', 'T'))
  const diff = Math.floor((Date.now() - date) / 1000)
  if (diff < 60)     return '방금 전'
  if (diff < 3600)   return `${Math.floor(diff / 60)}분 전`
  if (diff < 86400)  return `${Math.floor(diff / 3600)}시간 전`
  if (diff < 604800) return `${Math.floor(diff / 86400)}일 전`
  const y = date.getFullYear(), m = date.getMonth() + 1, d = date.getDate()
  return `${y}.${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')}`
}

const CATEGORY_COLOR = {
  spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green',
}
const CATEGORY_LABEL = {
  spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서',
}

const FILE_TYPE_COLOR = {
  pdf: 'volcano', docx: 'geekblue', pptx: 'orange', ppt: 'orange',
  xlsx: 'green', xls: 'green', hwp: 'purple', hwpx: 'purple',
  txt: 'default', md: 'cyan', png: 'magenta', jpg: 'magenta', jpeg: 'magenta',
}

const FILE_TYPE_ICON = {
  pdf:  <FilePdfOutlined  style={{ color: '#ff4d4f' }} />,
  docx: <FileWordOutlined style={{ color: '#1677ff' }} />,
  doc:  <FileWordOutlined style={{ color: '#1677ff' }} />,
  pptx: <FilePptOutlined  style={{ color: '#fa8c16' }} />,
  ppt:  <FilePptOutlined  style={{ color: '#fa8c16' }} />,
  xlsx: <FileExcelOutlined style={{ color: '#52c41a' }} />,
  xls:  <FileExcelOutlined style={{ color: '#52c41a' }} />,
  hwp:  <FileTextOutlined style={{ color: '#722ed1' }} />,
  hwpx: <FileTextOutlined style={{ color: '#722ed1' }} />,
  png:  <FileImageOutlined style={{ color: '#13c2c2' }} />,
  jpg:  <FileImageOutlined style={{ color: '#13c2c2' }} />,
  jpeg: <FileImageOutlined style={{ color: '#13c2c2' }} />,
  md:   <FileMarkdownOutlined style={{ color: '#722ed1' }} />,
  txt:  <FileTextOutlined style={{ color: '#8c8c8c' }} />,
}

export default function SearchPage({ onNavigate }) {
  const [tipVisible,  setTipVisible]  = useState(
    () => localStorage.getItem('km_tip_closed') !== 'true'
  )
  const [adminOpen,   setAdminOpen]   = useState(false)

  // 관리자 검색 기록에서 넘어온 경우 바로 검색 실행
  const [pendingSearch] = useState(() => {
    const q = localStorage.getItem('km_launch_query')
    if (q) localStorage.removeItem('km_launch_query')
    return q || null
  })

  const closeTip = () => {
    setTipVisible(false)
    localStorage.setItem('km_tip_closed', 'true')
  }

  const [query,      setQuery]      = useState('')
  const [alpha,      setAlpha]      = useState(0.5)
  const [category,   setCategory]   = useState(null)
  const [fileType,   setFileType]   = useState(null)
  const [results,    setResults]    = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [recentList,    setRecentList]    = useState([])
  const [showRecent,    setShowRecent]    = useState(false)
  const [page,          setPage]          = useState(1)
  const [topQueries,    setTopQueries]    = useState([])  // 인기 검색어 (0건 결과 시 표시)
  const [sortBy,        setSortBy]        = useState('score')
  const [aiAnswer,      setAiAnswer]      = useState(null)
  const [aiLoading,     setAiLoading]     = useState(false)
  const [aiAvail,       setAiAvail]       = useState(false)
  const [expandedCards, setExpandedCards] = useState(new Set())
  const PAGE_SIZE = 10
  const inputRef = useRef(null)

  const LS_KEY = 'km_recent_searches'

  // 마운트 시 자동 포커스 + Ollama 가용 여부 확인
  useEffect(() => {
    inputRef.current?.focus()
    axios.get(`${API}/ask/status`)
      .then(res => setAiAvail(res.data.available))
      .catch(() => {})
  }, [])

  // 관리자 검색 기록에서 넘어온 경우 자동 검색
  useEffect(() => {
    if (pendingSearch) {
      setQuery(pendingSearch)
      handleSearch(pendingSearch)
    }
  }, []) // eslint-disable-line

  // alpha 변경 시 자동 재검색 (검색 결과가 있을 때만, 400ms 디바운스)
  useEffect(() => {
    if (!results?.query) return
    const q = results.query
    const timer = setTimeout(() => handleSearch(q), 400)
    return () => clearTimeout(timer)
  }, [alpha]) // eslint-disable-line

  // "/" 단축키: 검색 페이지에 있을 때 어디서든 포커스
  useEffect(() => {
    const handler = (e) => {
      if (
        e.key === '/' &&
        !['INPUT', 'TEXTAREA'].includes(e.target.tagName) &&
        !e.target.isContentEditable
      ) {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  // 마운트 시: 로컬스토리지 우선 로드, 없으면 서버에서 가져옵니다
  useEffect(() => {
    const saved = localStorage.getItem(LS_KEY)
    if (saved) {
      try { setRecentList(JSON.parse(saved)) } catch {}
      return
    }
    axios.get(`${API}/admin/history`)
      .then(res => {
        const seen = new Set()
        const unique = res.data.filter(log => {
          if (seen.has(log.query)) return false
          seen.add(log.query)
          return true
        }).slice(0, 5)
        setRecentList(unique)
        localStorage.setItem(LS_KEY, JSON.stringify(unique))
      })
      .catch(() => {})
  }, [])

  const handleAskAI = async () => {
    if (!results?.results?.length) return
    setAiLoading(true)
    setAiAnswer(null)
    try {
      const snippets = results.results.slice(0, 5).map(r => r.snippet).filter(Boolean)
      const res = await axios.post(`${API}/ask`, { q: results.query, snippets })
      setAiAnswer(res.data)
    } catch {
      setAiAnswer({ answer: 'AI 답변 생성에 실패했습니다. Ollama가 실행 중인지 확인하세요.', model: '' })
    } finally {
      setAiLoading(false)
    }
  }

  const handleSearch = async (q = query) => {
    if (!q.trim()) return
    setShowRecent(false)
    setPage(1)
    setLoading(true)
    setAiAnswer(null)
    setExpandedCards(new Set())
    try {
      const res = await axios.get(`${API}/search`, {
        params: {
          q, alpha,
          ...(category ? { category } : {}),
          ...(fileType  ? { file_type: fileType } : {}),
        },
      })
      setResults(res.data)
      setQuery(q)

      // 결과 0건이면 인기 검색어를 불러와 추천합니다
      if (res.data.total === 0) {
        axios.get(`${API}/admin/stats`)
          .then(s => setTopQueries(s.data.top_queries ?? []))
          .catch(() => {})
      } else {
        setTopQueries([])
      }

      // 검색 후 최근 검색어 목록 갱신 + 로컬스토리지 저장
      setRecentList(prev => {
        const next = [{ query: q }, ...prev.filter(r => r.query !== q)].slice(0, 5)
        localStorage.setItem(LS_KEY, JSON.stringify(next))
        return next
      })
    } catch {
      setResults({ query: q, alpha, total: 0, results: [], error: '서버 연결 오류' })
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch()
    if (e.key === 'Escape') setShowRecent(false)
  }

  // 최근 검색어 클릭 시 바로 검색
  const handleRecentClick = (q) => {
    setQuery(q)
    handleSearch(q)
  }

  // 최근 검색어 하나 삭제 + 로컬스토리지 반영
  const handleDeleteRecent = (e, q) => {
    e.stopPropagation()
    setRecentList(prev => {
      const next = prev.filter(r => r.query !== q)
      localStorage.setItem(LS_KEY, JSON.stringify(next))
      return next
    })
  }

  return (
    <div style={{ padding: 32, maxWidth: 860, margin: '0 auto' }}>
      {/* 헤더: 제목 + 관리자 버튼 */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <Title level={2} style={{ margin: 0 }}>문서 검색</Title>
        <Tooltip title="관리자">
          <Button
            icon={<SettingOutlined />}
            shape="circle"
            type="text"
            style={{ color: '#8c8c8c' }}
            onClick={() => setAdminOpen(true)}
          />
        </Tooltip>
      </div>

      {/* ── 검색 팁 가이드 (처음 방문 시만 표시) ────────────────── */}
      {tipVisible && (
        <Alert
          type="info"
          showIcon
          closable
          onClose={closeTip}
          style={{ marginBottom: 20 }}
          message="검색 팁"
          description={
            <ul style={{ margin: '4px 0 0', paddingLeft: 20, lineHeight: 2 }}>
              <li>파일명이 아닌 <strong>내용</strong>으로 검색합니다. 예) <Typography.Text code>모터 설계 사양</Typography.Text></li>
              <li>여러 단어를 띄어쓰기로 구분하면 더 정확합니다. 예) <Typography.Text code>3상 유도전동기 효율</Typography.Text></li>
              <li>찾는 문서 종류를 알면 <strong>카테고리</strong>와 <strong>파일 형식</strong> 필터를 함께 쓰세요.</li>
              <li>결과가 없으면 검색어를 줄이거나 <strong>키워드 검색</strong> 쪽으로 슬라이더를 옮겨보세요.</li>
              <li><kbd style={{ padding: '1px 5px', background: '#f0f0f0', border: '1px solid #d9d9d9', borderRadius: 3, fontSize: 12 }}>/</kbd> 키를 누르면 어느 화면에서든 검색창으로 바로 이동합니다.</li>
            </ul>
          }
        />
      )}

      {/* ── 검색창 + 최근 검색어 드롭다운 ───────────────────────── */}
      <Card style={{ marginBottom: 24 }}>
        <div style={{ position: 'relative', marginBottom: 24 }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              ref={inputRef}
              size="large"
              placeholder="검색어를 입력하세요 (예: 모터 설계 사양)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => setShowRecent(true)}
              onBlur={() => setTimeout(() => setShowRecent(false), 150)}
              prefix={<SearchOutlined style={{ color: '#aaa' }} />}
            />
            <Button type="primary" size="large" loading={loading} onClick={() => handleSearch()}>
              검색
            </Button>
          </Space.Compact>

          {/* 최근 검색어 드롭다운 — 검색창 포커스 시 표시 */}
          {showRecent && recentList.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0,
              width: 'calc(100% - 90px)',  // 검색 버튼 너비 제외
              background: '#fff',
              border: '1px solid #d9d9d9',
              borderTop: 'none',
              borderRadius: '0 0 8px 8px',
              zIndex: 100,
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            }}>
              <List
                size="small"
                dataSource={recentList}
                renderItem={(item) => (
                  <List.Item
                    style={{ padding: '8px 12px', cursor: 'pointer' }}
                    onMouseDown={() => handleRecentClick(item.query)}
                    actions={[
                      <CloseOutlined
                        key="del"
                        style={{ color: '#aaa', fontSize: 11 }}
                        onMouseDown={(e) => handleDeleteRecent(e, item.query)}
                      />
                    ]}
                  >
                    <Space>
                      <HistoryOutlined style={{ color: '#aaa' }} />
                      <Text>{item.query}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </div>
          )}
        </div>

        {/* ── 카테고리 필터 ────────────────────────────────────── */}
        <div style={{ marginBottom: 16 }}>
          <Text strong>카테고리 필터</Text>
          <div style={{ marginTop: 8 }}>
            <Radio.Group
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              buttonStyle="solid"
            >
              <Radio.Button value={null}>전체</Radio.Button>
              <Radio.Button value="spec">사양서</Radio.Button>
              <Radio.Button value="research">연구자료</Radio.Button>
              <Radio.Button value="presentation">발표자료</Radio.Button>
              <Radio.Button value="report">보고서</Radio.Button>
            </Radio.Group>
          </div>
        </div>

        {/* ── 파일 형식 필터 ───────────────────────────────────── */}
        <div style={{ marginBottom: 16 }}>
          <Text strong>파일 형식</Text>
          <div style={{ marginTop: 8 }}>
            <Radio.Group
              value={fileType}
              onChange={(e) => setFileType(e.target.value)}
              buttonStyle="solid"
            >
              <Radio.Button value={null}>전체</Radio.Button>
              {['pdf','docx','pptx','xlsx','hwp','hwpx','txt','md'].map(ft => (
                <Radio.Button key={ft} value={ft}>{ft.toUpperCase()}</Radio.Button>
              ))}
            </Radio.Group>
          </div>
        </div>

        {/* ── Alpha 슬라이더 ────────────────────────────────────── */}
        <div>
          <Text strong>검색 방식 조절</Text>
          <Row align="middle" gutter={16} style={{ marginTop: 8 }}>
            <Col><Text type="secondary" style={{ whiteSpace: 'nowrap' }}>키워드 검색</Text></Col>
            <Col flex={1}>
              <Slider
                min={0} max={1} step={0.1}
                value={alpha}
                onChange={setAlpha}
                tooltip={{ formatter: (v) => `의미검색 ${Math.round(v * 100)}%` }}
              />
            </Col>
            <Col><Text type="secondary" style={{ whiteSpace: 'nowrap' }}>의미 검색</Text></Col>
            <Col>
              <Tag color="blue">의미 {Math.round(alpha * 100)}% · 키워드 {Math.round((1 - alpha) * 100)}%</Tag>
            </Col>
          </Row>
        </div>
      </Card>

      {/* ── 검색 결과 ────────────────────────────────────────────── */}
      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin size="large" tip="검색 중..." />
        </div>
      )}

      {!loading && results && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <Text type="secondary">
              "{results.query}" 검색 결과 — 총 {results.total}건
              {results.error && <Text type="danger"> · {results.error}</Text>}
            </Text>
            <Space size={8}>
              {results.total > 0 && aiAvail && (
                <Button
                  size="small"
                  icon={<RobotOutlined />}
                  loading={aiLoading}
                  onClick={handleAskAI}
                  style={{ borderColor: '#1677ff', color: '#1677ff' }}
                >
                  AI에게 묻기
                </Button>
              )}
              {results.total > 1 && (
                <Select
                  size="small"
                  value={sortBy}
                  onChange={(v) => { setSortBy(v); setPage(1) }}
                  style={{ width: 100 }}
                  options={[
                    { value: 'score', label: '관련도순' },
                    { value: 'date',  label: '최신순' },
                  ]}
                />
              )}
            </Space>
          </div>
          <Divider style={{ margin: '12px 0' }} />

          {/* AI 답변 카드 */}
          {aiAnswer && (
            <Card
              size="small"
              style={{ marginBottom: 16, background: '#f0f5ff', borderColor: '#91caff' }}
              styles={{ body: { padding: '12px 16px' } }}
              title={
                <Space size={6}>
                  <RobotOutlined style={{ color: '#1677ff' }} />
                  <Text strong style={{ color: '#1677ff' }}>AI 답변</Text>
                  {aiAnswer.model && (
                    <Text type="secondary" style={{ fontSize: 11 }}>· {aiAnswer.model}</Text>
                  )}
                  <Text type="secondary" style={{ fontSize: 11 }}>· 상위 5개 문서 기반</Text>
                </Space>
              }
              extra={
                <Button type="text" size="small" onClick={() => setAiAnswer(null)}>✕</Button>
              }
            >
              <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
                {aiAnswer.answer}
              </Paragraph>
            </Card>
          )}

          {results.total === 0 ? (
            <div style={{ textAlign: 'center', padding: '32px 0' }}>
              <Empty description={`"${results.query}"에 대한 검색 결과가 없습니다`} />
              {topQueries.length > 0 && (
                <div style={{ marginTop: 20 }}>
                  <Text type="secondary">다른 사람들이 많이 검색한 검색어</Text>
                  <div style={{ marginTop: 10 }}>
                    {topQueries.map((q, i) => (
                      <Tag
                        key={i}
                        color="blue"
                        style={{ cursor: 'pointer', margin: '4px' }}
                        onClick={() => handleRecentClick(q.query)}
                      >
                        {q.query}
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                {[...results.results]
                  .sort((a, b) =>
                    sortBy === 'date'
                      ? new Date(b.uploaded_at || 0) - new Date(a.uploaded_at || 0)
                      : b.score - a.score
                  )
                  .slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
                  .map((r, idx) => {
                    const CAT_ACCENT = {
                      spec: '#1677ff', research: '#722ed1',
                      presentation: '#13c2c2', report: '#52c41a',
                    }
                    const accent = CAT_ACCENT[r.category] ?? '#8c8c8c'
                    return (
                      <Card
                        key={r.doc_id}
                        size="small"
                        hoverable
                        style={{
                          borderLeft: `4px solid ${accent}`,
                          borderRadius: 8,
                          boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
                          transition: 'box-shadow 0.2s',
                        }}
                        styles={{ body: { padding: '12px 16px' } }}
                        extra={
                          <Button
                            size="small"
                            icon={<DownloadOutlined />}
                            href={`${API}/files/${r.doc_id}`}
                            download
                            type="primary"
                            ghost
                          >
                            다운로드
                          </Button>
                        }
                      >
                        {/* 제목 행 */}
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                          <span style={{ fontSize: 20, flexShrink: 0, marginTop: 1 }}>
                            {FILE_TYPE_ICON[r.file_type] ?? <FileOutlined style={{ color: '#8c8c8c' }} />}
                          </span>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            {r.title && r.title !== r.filename ? (
                              <>
                                <Text strong style={{ fontSize: 14, lineHeight: 1.4 }}>
                                  {highlight(r.title, results.query)}
                                </Text>
                                <Tooltip title={r.filename}>
                                  <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 1 }}>
                                    {r.filename}
                                  </Text>
                                </Tooltip>
                              </>
                            ) : (
                              <Text strong style={{ fontSize: 14, lineHeight: 1.4 }}>
                                {highlight(r.filename, results.query)}
                              </Text>
                            )}
                          </div>
                          {/* 관련도 점수 뱃지 */}
                          {r.score > 0 && (
                            <Tooltip title={`관련도 ${Math.round(r.score * 100)}%`}>
                              <div style={{
                                flexShrink: 0,
                                background: r.score >= 0.6 ? '#e6f4ff' : r.score >= 0.3 ? '#fff7e6' : '#f5f5f5',
                                color: r.score >= 0.6 ? '#1677ff' : r.score >= 0.3 ? '#fa8c16' : '#8c8c8c',
                                border: `1px solid ${r.score >= 0.6 ? '#91caff' : r.score >= 0.3 ? '#ffd591' : '#d9d9d9'}`,
                                borderRadius: 12,
                                padding: '2px 8px',
                                fontSize: 11,
                                fontWeight: 600,
                                cursor: 'default',
                              }}>
                                {Math.round(r.score * 100)}%
                              </div>
                            </Tooltip>
                          )}
                        </div>

                        {/* 태그 행 */}
                        <Space style={{ marginBottom: 8 }} size={4} wrap>
                          <Tag color={CATEGORY_COLOR[r.category]} style={{ margin: 0 }}>
                            {CATEGORY_LABEL[r.category] ?? r.category}
                          </Tag>
                          {r.file_type && (
                            <Tag color={FILE_TYPE_COLOR[r.file_type] ?? 'default'} style={{ margin: 0 }}>
                              {r.file_type.toUpperCase()}
                            </Tag>
                          )}
                          <Text type="secondary" style={{ fontSize: 12 }}>{r.pages}페이지</Text>
                          {r.page_num > 0 && (
                            <Tooltip title={`${r.page_num}페이지에서 발견`}>
                              <Tag
                                style={{ margin: 0, background: '#f5f5f5', border: '1px solid #d9d9d9', color: '#595959', fontSize: 11 }}
                              >
                                p.{r.page_num}
                              </Tag>
                            </Tooltip>
                          )}
                          {r.uploaded_at && (
                            <Tooltip title={r.uploaded_at}>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                · {formatDate(r.uploaded_at)}
                              </Text>
                            </Tooltip>
                          )}
                        </Space>

                        {/* 관리자 메모 */}
                        {r.memo && (
                          <div style={{
                            background: '#fffbe6',
                            border: '1px solid #ffe58f',
                            borderRadius: 6,
                            padding: '5px 10px',
                            marginBottom: 8,
                            fontSize: 12,
                            color: '#7c6000',
                          }}>
                            📝 {r.memo}
                          </div>
                        )}

                        {/* 스니펫 */}
                        <div style={{
                          background: '#fafafa',
                          border: '1px solid #f0f0f0',
                          borderRadius: 6,
                          padding: '6px 10px',
                        }}>
                          <Paragraph
                            type="secondary"
                            style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }}
                            ellipsis={expandedCards.has(r.doc_id) ? false : { rows: 2 }}
                          >
                            {highlight(r.snippet, results.query)}
                          </Paragraph>
                          {r.snippet?.length > 120 && (
                            <Button
                              type="link"
                              size="small"
                              style={{ padding: 0, height: 'auto', fontSize: 11, color: '#8c8c8c' }}
                              onClick={() => setExpandedCards(prev => {
                                const next = new Set(prev)
                                next.has(r.doc_id) ? next.delete(r.doc_id) : next.add(r.doc_id)
                                return next
                              })}
                            >
                              {expandedCards.has(r.doc_id) ? '접기' : '더 보기'}
                            </Button>
                          )}
                        </div>
                      </Card>
                    )
                  })}
              </Space>
              {results.total > PAGE_SIZE && (
                <div style={{ textAlign: 'center', marginTop: 20 }}>
                  <Pagination
                    current={page}
                    pageSize={PAGE_SIZE}
                    total={results.total}
                    onChange={(p) => { setPage(p); window.scrollTo(0, 0) }}
                    showSizeChanger={false}
                  />
                </div>
              )}
            </>
          )}
        </>
      )}

      {!loading && results === null && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="검색어를 입력하고 검색 버튼을 누르세요"
        />
      )}

      {/* ── 관리자 Drawer (우상단 톱니 → 열림) ──────────────────── */}
      <Drawer
        title="관리자"
        placement="right"
        width={Math.min(window.innerWidth, 960)}
        open={adminOpen}
        onClose={() => setAdminOpen(false)}
        destroyOnClose
        styles={{ body: { padding: 0, background: '#f5f5f5' } }}
      >
        <AdminPage onNavigate={(tab) => { setAdminOpen(false); onNavigate?.(tab) }} />
      </Drawer>
    </div>
  )
}
