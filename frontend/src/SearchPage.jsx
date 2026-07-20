// SearchPage.jsx — 검색 화면
// 2026-07-11 목업 반영:
//  - 관리자 진입은 이제 App.jsx 최상위 탭이 담당 — 이 화면의 톱니바퀴+Drawer는 제거.
//  - alpha 연속 슬라이더 상시 노출 → "고급 검색" 토글(기본 접힘) 안에 3단 프리셋 버튼
//    (정확히 일치=키워드 위주/균형/비슷한 내용=의미 위주)으로 대체.
//    ⚠ 프리셋의 alpha 매핑은 목업 프롬프트킷 예시(0.9/0.5/0.1)가 아니라 실제 백엔드 주석
//    (main.py: "alpha=0.0 → BM25 전용 / alpha=1.0 → 시맨틱 전용")에 맞춰 반대로 정정함:
//    정확히 일치=0.1, 균형=0.5, 비슷한 내용=0.9.
//  - 카테고리·파일형식 필터는 삭제하지 않고 같은 "고급 검색" 안으로 이동(기존 기능 보존).
//  - alpha·검색 로직·검색 기록·AI 답변 등 기존 기능/엔드포인트 호출은 전부 유지.

import { useState, useEffect, useRef } from 'react'
import {
  Input, Button, Card, Tag, Typography,
  Space, Divider, Empty, Spin, Radio, List, Pagination, Tooltip, Alert, Select, Switch,
  Row, Col, Modal, message, DatePicker, AutoComplete,
} from 'antd'
import dayjs from 'dayjs'
import {
  SearchOutlined, FileTextOutlined, HistoryOutlined, CloseOutlined,
  FilePdfOutlined, FileWordOutlined, FilePptOutlined, FileExcelOutlined,
  FileImageOutlined, FileMarkdownOutlined, FileOutlined, DownloadOutlined,
  RobotOutlined, AimOutlined, ApartmentOutlined, ShareAltOutlined,
  StarOutlined, StarFilled, EyeOutlined, UserOutlined, LockOutlined,
  UnorderedListOutlined, AppstoreOutlined,
} from '@ant-design/icons'
import axios from 'axios'

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

// 검색 결과 정렬 — 관련도순/최신순/작성자순 (그리드뷰·리스트뷰에서 공통으로 재사용)
const sortResults = (results, sortBy) => [...results].sort((a, b) => {
  if (sortBy === 'date')   return new Date(b.uploaded_at || 0) - new Date(a.uploaded_at || 0)
  if (sortBy === 'author') return (a.uploaded_by_name ?? a.uploaded_by ?? '').localeCompare(b.uploaded_by_name ?? b.uploaded_by ?? '')
  return b.score - a.score
})

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

// 검색 모드 3단 프리셋 — alpha=0(BM25 전용) ↔ alpha=1(시맨틱 전용), 실제 백엔드 정의 기준
const SEARCH_MODES = [
  { key: 'exact',    label: '정확히 일치', icon: <AimOutlined />,      alpha: 0.1 },
  { key: 'balanced', label: '균형',        icon: <ApartmentOutlined />, alpha: 0.5 },
  { key: 'similar',  label: '비슷한 내용', icon: <ShareAltOutlined />, alpha: 0.9 },
]

export default function SearchPage({ onNavigate }) {
  const [tipVisible,  setTipVisible]  = useState(
    () => localStorage.getItem('km_tip_closed') !== 'true'
  )
  const [advancedOpen, setAdvancedOpen] = useState(false)  // 고급 검색 — 기본 접힘(목업 기준)

  // 관리자 검색 기록에서 넘어온 경우 바로 검색 실행
  const [pendingSearch] = useState(() => {
    const q = localStorage.getItem('km_launch_query')
    if (q) localStorage.removeItem('km_launch_query')
    return q || null
  })

  // 업로드 화면의 "카테고리" 클릭으로 넘어온 경우 — 검색어 없이 그 카테고리 문서 전체를 바로 보여준다
  const [pendingCategory] = useState(() => {
    const c = localStorage.getItem('km_launch_category')
    if (c) localStorage.removeItem('km_launch_category')
    return c || null
  })

  const closeTip = () => {
    setTipVisible(false)
    localStorage.setItem('km_tip_closed', 'true')
  }

  const [query,      setQuery]      = useState('')
  const [searchMode, setSearchMode] = useState('balanced')
  const alpha = SEARCH_MODES.find(m => m.key === searchMode).alpha
  const [category,   setCategory]   = useState(null)
  const [fileType,   setFileType]   = useState(null)
  const [author,     setAuthor]     = useState(null)   // 작성자(업로더) 이메일 필터
  const [authorInput, setAuthorInput] = useState('')    // 작성자 자동완성 입력창에 표시되는 텍스트(이름)
  const [dateRange,  setDateRange]  = useState(null)    // [dayjs, dayjs] | null — 업로드 날짜 범위 필터
  const [results,    setResults]    = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [recentList,    setRecentList]    = useState([])
  const [showRecent,    setShowRecent]    = useState(false)
  const [page,          setPage]          = useState(1)
  const [topQueries,    setTopQueries]    = useState([])  // 인기 검색어 (0건 결과 시 표시)
  const [sortBy,        setSortBy]        = useState('score')
  const [viewMode,      setViewMode]      = useState('list')  // 'list'(상세) | 'grid'(카드)
  const [aiAnswer,      setAiAnswer]      = useState(null)
  const [aiLoading,     setAiLoading]     = useState(false)
  const [aiAvail,       setAiAvail]       = useState(false)
  // 파일마다 AI 요약이 따로 있어 검색 결과의 "AI에게 묻기"는 자동 생성으로 바꾸고,
  // 대신 켜고 끌 수 있는 스위치를 둔다 (기본값: 켜짐, localStorage에 기억)
  const [aiEnabled,     setAiEnabled]     = useState(
    () => localStorage.getItem('km_ai_answer_enabled') !== 'false'
  )
  const PAGE_SIZE = 10
  const inputRef = useRef(null)

  const LS_KEY = 'km_recent_searches'
  const [extraCategories, setExtraCategories] = useState([])  // 고정 5종 외에 실제로 쓰인 커스텀 카테고리
  const [uploaderList,    setUploaderList]    = useState([])  // 작성자 필터 드롭다운용 — 실제로 업로드한 적 있는 사람만
  const [bookmarkedIds,   setBookmarkedIds]   = useState(new Set())

  // ── 검색 전 기본 화면: "카테고리로 찾아보기" ─────────────────────────
  // 아직 검색을 실행하지 않은 상태(results===null)에서 빈 안내 문구 대신 보여주는
  // 문서 둘러보기 그리드. 실제 검색(handleSearch)과는 완전히 분리된 가벼운 상태로 둔다 —
  // AI 답변·최근 검색어 저장 등 실제 검색의 부수효과를 여기서는 트리거하지 않기 위함.
  const [stats,          setStats]          = useState(null)
  const [browseCategory, setBrowseCategory] = useState(null)
  const [browseDocs,     setBrowseDocs]     = useState([])
  const [browseLoading,  setBrowseLoading]  = useState(false)
  const [browsePage,     setBrowsePage]     = useState(1)  // 문서가 늘어나도 10개 단위로 끊어서 표시

  // 문서 카드를 클릭하면 바로 다운로드되던 것 대신 상세 팝업을 띄우고, 그 안에서 댓글을 주고받는다
  const [detailDoc,       setDetailDoc]       = useState(null)  // 상세 팝업 대상 문서(null이면 닫힘)
  const [comments,        setComments]        = useState([])
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [newComment,      setNewComment]      = useState('')
  const [commentPosting,  setCommentPosting]  = useState(false)

  // AI 요약(qwen2.5:7b) — 팝업 열릴 때 자동으로 요청, 댓글 영역 위에 표시
  const [summary,        setSummary]        = useState(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError,   setSummaryError]   = useState(null)

  const openDetail = (doc) => {
    setDetailDoc(doc)
    setNewComment('')
    setComments([])
    setCommentsLoading(true)
    axios.get(`${API}/documents/${doc.doc_id}/comments`)
      .then(res => setComments(res.data))
      .catch(() => {})
      .finally(() => setCommentsLoading(false))

    setSummary(null)
    setSummaryError(null)
    // "AI 답변" 스위치는 검색 결과뿐 아니라 이 문서별 AI 요약도 함께 켜고 끈다 — 꺼져 있으면 요청 자체를 보내지 않는다
    if (!aiEnabled) return
    setSummaryLoading(true)
    axios.get(`${API}/documents/${doc.doc_id}/summary`)
      .then(res => setSummary(res.data.summary))
      .catch((e) => setSummaryError(e?.response?.data?.detail || 'AI 요약을 만들지 못했습니다'))
      .finally(() => setSummaryLoading(false))
  }

  const postComment = async () => {
    if (!newComment.trim() || !detailDoc) return
    setCommentPosting(true)
    try {
      const res = await axios.post(`${API}/documents/${detailDoc.doc_id}/comments`, { content: newComment.trim() })
      setComments(prev => [...prev, res.data])
      setNewComment('')
    } catch {
      message.error('댓글 작성에 실패했습니다')
    } finally {
      setCommentPosting(false)
    }
  }

  const deleteComment = async (commentId) => {
    if (!detailDoc) return
    try {
      await axios.delete(`${API}/documents/${detailDoc.doc_id}/comments/${commentId}`)
      setComments(prev => prev.filter(c => c.id !== commentId))
    } catch {
      message.error('삭제 권한이 없거나 오류가 발생했습니다')
    }
  }

  const refreshBookmarks = () => {
    axios.get(`${API}/bookmarks`)
      .then(res => setBookmarkedIds(new Set(res.data.map(b => b.id))))
      .catch(() => {})
  }

  const toggleBookmark = async (docId) => {
    try {
      if (bookmarkedIds.has(docId)) {
        await axios.delete(`${API}/bookmarks/${docId}`)
      } else {
        await axios.post(`${API}/bookmarks/${docId}`)
      }
      refreshBookmarks()
    } catch {
      // 조용히 무시 — 북마크는 부가 기능이라 실패해도 검색 흐름을 막지 않음
    }
  }

  // 마운트 시 자동 포커스 + Ollama 가용 여부 확인 + 커스텀 카테고리 존재 여부 확인 + 북마크 목록
  useEffect(() => {
    inputRef.current?.focus()
    axios.get(`${API}/ask/status`)
      .then(res => setAiAvail(res.data.available))
      .catch(() => {})
    refreshBookmarks()
    axios.get(`${API}/admin/stats`)
      .then(res => {
        setStats(res.data)
        const observed = Object.keys(res.data.by_category ?? {})
        setExtraCategories(observed.filter(c => !(c in CATEGORY_LABEL)))
      })
      .catch(() => {})
    axios.get(`${API}/uploaders`)
      .then(res => setUploaderList(res.data))
      .catch(() => {})
  }, [])

  // 카테고리 칩을 고르면 그 카테고리만, "전체"면 전부 다시 불러온다 (검색어 없이 둘러보기 모드)
  // 업로드한 문서가 파싱을 마치고 "완료"로 바뀌는 시점은 사용자가 이 화면에 이미 머무르고
  // 있는 동안일 수 있어서, 8초마다 조용히 다시 불러와 새로고침 없이도 반영되게 한다
  // (loading 스피너는 최초 1회만 보여주고, 이후 자동 갱신은 화면 깜빡임 없이 조용히 처리).
  useEffect(() => {
    let cancelled = false
    setBrowsePage(1)
    const fetchBrowse = (showSpinner) => {
      if (showSpinner) setBrowseLoading(true)
      axios.get(`${API}/search`, {
        params: { q: '', alpha: 0.5, ...(browseCategory ? { category: browseCategory } : {}) },
      })
        .then(res => { if (!cancelled) setBrowseDocs(res.data.results ?? []) })
        .catch(() => { if (!cancelled) setBrowseDocs([]) })
        .finally(() => { if (!cancelled && showSpinner) setBrowseLoading(false) })
    }
    fetchBrowse(true)
    const timer = setInterval(() => fetchBrowse(false), 8000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [browseCategory])

  // 관리자 검색 기록에서 넘어온 경우 자동 검색
  useEffect(() => {
    if (pendingSearch) {
      setQuery(pendingSearch)
      handleSearch(pendingSearch)
    }
  }, []) // eslint-disable-line

  // 업로드 화면 "카테고리" 클릭으로 넘어온 경우 — 검색어 없이 해당 카테고리 문서 전체를 바로 표시
  useEffect(() => {
    if (pendingCategory) {
      setCategory(pendingCategory)
      setAdvancedOpen(true)
      handleSearch('', pendingCategory)
    }
  }, []) // eslint-disable-line

  // 검색 모드(alpha) 변경 시 자동 재검색 (검색 결과가 있을 때만)
  useEffect(() => {
    if (!results?.query) return
    const q = results.query
    const timer = setTimeout(() => handleSearch(q), 200)
    return () => clearTimeout(timer)
  }, [searchMode]) // eslint-disable-line

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

  // resultsData를 인자로 받는 이유: setResults 직후엔 results state가 아직 갱신 전이라
  // (React 배치 업데이트), handleSearch에서 자동 호출할 때 방금 받은 응답을 직접 넘겨야 한다.
  const runAskAI = async (resultsData) => {
    if (!resultsData?.results?.length) return
    setAiLoading(true)
    setAiAnswer(null)
    try {
      const snippets = resultsData.results.slice(0, 5).map(r => r.snippet).filter(Boolean)
      const res = await axios.post(`${API}/ask`, { q: resultsData.query, snippets })
      setAiAnswer(res.data)
    } catch {
      setAiAnswer({ answer: 'AI 요약 생성에 실패했습니다. Ollama가 실행 중인지 확인하세요.', model: '' })
    } finally {
      setAiLoading(false)
    }
  }
  const handleToggleAi = (checked) => {
    setAiEnabled(checked)
    localStorage.setItem('km_ai_answer_enabled', checked ? 'true' : 'false')
    if (checked) {
      if (results?.query && results.total > 0 && !aiAnswer) runAskAI(results)
      if (detailDoc && !summary && !summaryLoading) {
        setSummaryError(null)
        setSummaryLoading(true)
        axios.get(`${API}/documents/${detailDoc.doc_id}/summary`)
          .then(res => setSummary(res.data.summary))
          .catch((e) => setSummaryError(e?.response?.data?.detail || 'AI 요약을 만들지 못했습니다'))
          .finally(() => setSummaryLoading(false))
      }
    } else {
      setAiAnswer(null)
    }
  }

  // categoryOverride: UploadPage의 "카테고리" 클릭처럼, category state를 막 setCategory한
  // 직후 곧바로 검색을 트리거해야 할 때 쓴다 — setCategory 직후엔 state가 아직 안 바뀐 상태라
  // (React 배치 업데이트) category를 그대로 읽으면 이전 값으로 검색돼 버린다.
  const handleSearch = async (q = query, categoryOverride = category) => {
    // 검색어가 없어도 카테고리·파일 형식·작성자·날짜 필터가 있으면 "필터로 둘러보기"로 진행
    const hasDateFilter = dateRange?.[0] || dateRange?.[1]
    if (!q.trim() && !categoryOverride && !fileType && !author && !hasDateFilter) return
    setShowRecent(false)
    setPage(1)
    setLoading(true)
    setAiAnswer(null)
    try {
      const res = await axios.get(`${API}/search`, {
        params: {
          q, alpha,
          ...(categoryOverride ? { category: categoryOverride } : {}),
          ...(fileType  ? { file_type: fileType } : {}),
          ...(author    ? { uploaded_by: author } : {}),
          ...(dateRange?.[0] ? { date_from: dateRange[0].format('YYYY-MM-DD') } : {}),
          ...(dateRange?.[1] ? { date_to: dateRange[1].format('YYYY-MM-DD') } : {}),
        },
      })
      setResults(res.data)
      setQuery(q)

      if (res.data.total === 0) {
        axios.get(`${API}/admin/stats`)
          .then(s => setTopQueries(s.data.top_queries ?? []))
          .catch(() => {})
      } else {
        setTopQueries([])
      }

      // AI 답변이 검색의 "기본 결과" — 실제 질문(필터만으로 둘러보기가 아닌 경우)에는
      // 자동으로 생성한다. 결과가 없거나 Ollama가 꺼져 있거나, 스위치로 꺼둔 경우엔 스킵.
      if (aiEnabled && q.trim() && res.data.total > 0 && aiAvail) {
        runAskAI(res.data)
      }

      if (q.trim()) {
        setRecentList(prev => {
          const next = [{ query: q }, ...prev.filter(r => r.query !== q)].slice(0, 5)
          localStorage.setItem(LS_KEY, JSON.stringify(next))
          return next
        })
      }
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

  const handleRecentClick = (q) => {
    setQuery(q)
    handleSearch(q)
  }

  const handleDeleteRecent = (e, q) => {
    e.stopPropagation()
    setRecentList(prev => {
      const next = prev.filter(r => r.query !== q)
      localStorage.setItem(LS_KEY, JSON.stringify(next))
      return next
    })
  }

  return (
    <div style={{ padding: '28px 32px 40px', maxWidth: 860, margin: '0 auto' }}>
      <Title level={2} style={{ margin: '0 0 4px' }}>통합 검색</Title>

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
              <li>결과가 마음에 안 들면 <strong>고급 검색</strong>에서 검색 모드·필터를 조절해 보세요.</li>
              <li><kbd style={{ padding: '1px 5px', background: '#f0f0f0', border: '1px solid #d9d9d9', borderRadius: 3, fontSize: 12 }}>/</kbd> 키를 누르면 어디서든 검색창으로 바로 이동합니다.</li>
            </ul>
          }
        />
      )}

      {/* ── 검색창 + 최근 검색어 드롭다운 (스크롤해도 화면 상단에 고정) ── */}
      <div style={{
        position: 'sticky', top: 64, zIndex: 10,
        background: '#fff', margin: '0 -32px', padding: '12px 32px 10px',
        borderBottom: '1px solid #f0f0f0',
      }}>
        <div style={{ position: 'relative' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              ref={inputRef}
              size="large"
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

          {showRecent && recentList.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0,
              width: 'calc(100% - 90px)',
              background: '#fff', border: '1px solid #d9d9d9', borderTop: 'none',
              borderRadius: '0 0 8px 8px', zIndex: 100, boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
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
                    <Space><HistoryOutlined style={{ color: '#aaa' }} /><Text>{item.query}</Text></Space>
                  </List.Item>
                )}
              />
            </div>
          )}
        </div>
      </div>

      {/* ── AI 답변 켜기/끄기 + 고급 검색 토글 ───────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: advancedOpen ? 12 : 20 }}>
        <Space size={6}>
          <Switch size="small" checked={aiEnabled} onChange={handleToggleAi} />
          <Text style={{ fontSize: 12.5 }}>AI 요약</Text>
        </Space>
        <Button type="link" size="small" onClick={() => setAdvancedOpen(v => !v)} style={{ padding: 0 }}>
          고급 검색 {advancedOpen ? '▲' : '▾'}
        </Button>
      </div>

      {advancedOpen && (
        <Card size="small" style={{ marginBottom: 20 }}>
          <div style={{ marginBottom: 14 }}>
            <Text strong style={{ fontSize: 12.5 }}>검색 모드</Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group
                value={searchMode}
                onChange={(e) => setSearchMode(e.target.value)}
                buttonStyle="solid"
              >
                {SEARCH_MODES.map(m => (
                  <Radio.Button key={m.key} value={m.key}>
                    <Space size={4}>{m.icon}{m.label}</Space>
                  </Radio.Button>
                ))}
              </Radio.Group>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <Text strong style={{ fontSize: 12.5 }}>카테고리</Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group value={category} onChange={(e) => setCategory(e.target.value)} buttonStyle="solid">
                <Radio.Button value={null}>전체</Radio.Button>
                <Radio.Button value="spec">사양서</Radio.Button>
                <Radio.Button value="research">연구자료</Radio.Button>
                <Radio.Button value="presentation">발표자료</Radio.Button>
                <Radio.Button value="report">보고서</Radio.Button>
                {extraCategories.map(c => (
                  <Radio.Button key={c} value={c}>{c}</Radio.Button>
                ))}
              </Radio.Group>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <Text strong style={{ fontSize: 12.5 }}>파일 형식</Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group value={fileType} onChange={(e) => setFileType(e.target.value)} buttonStyle="solid">
                <Radio.Button value={null}>전체</Radio.Button>
                {['pdf','docx','pptx','xlsx','hwp','hwpx','txt','md'].map(ft => (
                  <Radio.Button key={ft} value={ft}>{ft.toUpperCase()}</Radio.Button>
                ))}
              </Radio.Group>
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <Text strong style={{ fontSize: 12.5 }}>작성자</Text>
            <div style={{ marginTop: 8 }}>
              {/* 인원이 늘어날수록 Select 드롭다운이 통째로 펼쳐지면 지저분해지므로,
                  입력한 만큼만 후보가 좁혀지는 자동완성으로 대체 */}
              <AutoComplete
                allowClear
                style={{ width: 220 }}
                placeholder="이름 또는 이메일 검색"
                value={authorInput}
                options={uploaderList
                  .filter(u => !authorInput
                    || u.name.toLowerCase().includes(authorInput.toLowerCase())
                    || u.email.toLowerCase().includes(authorInput.toLowerCase()))
                  .map(u => ({ value: u.name }))}
                onChange={(v) => {
                  setAuthorInput(v)
                  const match = uploaderList.find(u => u.name === v)
                  setAuthor(match ? match.email : null)
                }}
              />
            </div>
          </div>

          <div>
            <Text strong style={{ fontSize: 12.5 }}>업로드 날짜</Text>
            <div style={{ marginTop: 8 }}>
              <Space size={8}>
                <DatePicker
                  placeholder="시작일"
                  value={dateRange?.[0] ?? null}
                  onChange={(v) => {
                    const end = dateRange?.[1] ?? null
                    setDateRange((v || end) ? [v, end] : null)
                  }}
                />
                <Text type="secondary">~</Text>
                <DatePicker
                  placeholder="종료일"
                  value={dateRange?.[1] ?? null}
                  onChange={(v) => {
                    const start = dateRange?.[0] ?? null
                    setDateRange((start || v) ? [start, v] : null)
                  }}
                />
              </Space>
            </div>
          </div>
        </Card>
      )}

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
              {results.query ? `"${results.query}" 검색 결과` : '필터 적용 결과'} — 총 {results.total}건
              {results.error && <Text type="danger"> · {results.error}</Text>}
            </Text>
            <Space size={8}>
              {results.total > 1 && (
                <Select
                  size="small" value={sortBy}
                  onChange={(v) => { setSortBy(v); setPage(1) }}
                  style={{ width: 110 }}
                  options={[
                    { value: 'score', label: '관련도순' },
                    { value: 'date', label: '최신순' },
                    { value: 'author', label: '작성자순' },
                  ]}
                />
              )}
              {results.total > 0 && (
                <Radio.Group
                  size="small" value={viewMode}
                  onChange={(e) => setViewMode(e.target.value)}
                  optionType="button"
                >
                  <Tooltip title="리스트형">
                    <Radio.Button value="list"><UnorderedListOutlined /></Radio.Button>
                  </Tooltip>
                  <Tooltip title="카드형">
                    <Radio.Button value="grid"><AppstoreOutlined /></Radio.Button>
                  </Tooltip>
                </Radio.Group>
              )}
            </Space>
          </div>
          <Divider style={{ margin: '12px 0' }} />

          {/* AI 답변이 검색의 기본 결과 — 생성 중엔 스켈레톤, 꺼져 있으면 안내를 이 자리에 바로 보여준다.
              스위치로 꺼둔 경우엔 이 블록 전체를 아예 표시하지 않는다. */}
          {aiEnabled && (
            <>
              {aiLoading && !aiAnswer && (
                <Card size="small" style={{ marginBottom: 16, background: '#f0f5ff', borderColor: '#91caff' }} styles={{ body: { padding: '12px 16px' } }}>
                  <Space size={6}><RobotOutlined style={{ color: '#1677ff' }} spin /><Text type="secondary">AI가 문서를 바탕으로 요약을 만들고 있어요...</Text></Space>
                </Card>
              )}

              {!aiAvail && results.total > 0 && !!results.query && (
                <div style={{
                  marginBottom: 16, padding: '12px 16px', borderRadius: 8,
                  background: '#fafafa', border: '1px dashed #e0e0e0', color: '#bfbfbf',
                  display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5,
                }}>
                  <LockOutlined />
                  <div>
                    <div>AI 요약 (Ollama 미가동)</div>
                    <div style={{ fontSize: 11.5 }}>로컬 Ollama 서버가 켜져 있으면 검색어에 대한 답을 문서 기반으로 바로 요약해 드립니다.</div>
                  </div>
                </div>
              )}

              {aiAnswer && (
                <Card
                  size="small"
                  style={{ marginBottom: 16, background: '#f0f5ff', borderColor: '#91caff' }}
                  styles={{ body: { padding: '12px 16px' } }}
                  title={
                    <Space size={6}>
                      <RobotOutlined style={{ color: '#1677ff' }} />
                      <Text strong style={{ color: '#1677ff' }}>AI 요약</Text>
                      {aiAnswer.model && <Text type="secondary" style={{ fontSize: 11 }}>· {aiAnswer.model}</Text>}
                      <Text type="secondary" style={{ fontSize: 11 }}>· 상위 5개 문서 기반</Text>
                    </Space>
                  }
                  extra={<Button type="text" size="small" onClick={() => setAiAnswer(null)}>✕</Button>}
                >
                  <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{aiAnswer.answer}</Paragraph>
                </Card>
              )}
            </>
          )}

          {results.total === 0 ? (
            <div style={{ textAlign: 'center', padding: '32px 0' }}>
              <Empty description={results.query ? `"${results.query}"에 대한 검색 결과가 없습니다` : '조건에 맞는 문서가 없습니다'} />
              {topQueries.length > 0 && (
                <div style={{ marginTop: 20 }}>
                  <Text type="secondary">다른 사람들이 많이 검색한 검색어</Text>
                  <div style={{ marginTop: 10 }}>
                    {topQueries.map((q, i) => (
                      <Tag key={i} color="blue" style={{ cursor: 'pointer', margin: '4px' }} onClick={() => handleRecentClick(q.query)}>
                        {q.query}
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              {viewMode === 'grid' ? (
                <Row gutter={[16, 16]}>
                  {sortResults(results.results, sortBy)
                    .slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
                    .map((r) => (
                      <Col key={r.doc_id} xs={24} sm={12} md={8}>
                        <Card
                          size="small" hoverable
                          style={{ borderRadius: 8 }}
                          styles={{ body: { padding: 12 } }}
                          onClick={() => openDetail(r)}
                        >
                          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
                            <Tooltip title={r.content_preview || '미리보기를 불러올 수 없습니다'} placement="topLeft" mouseEnterDelay={0.3}>
                              <Text strong style={{ fontSize: 13.5, lineHeight: 1.4 }} ellipsis>
                                {r.title || r.filename}
                              </Text>
                            </Tooltip>
                            <Space size={4} style={{ flexShrink: 0 }}>
                              {r.file_type && (
                                <Tag color={FILE_TYPE_COLOR[r.file_type] ?? 'default'} style={{ margin: 0 }}>
                                  {r.file_type.toUpperCase()}
                                </Tag>
                              )}
                              <Tooltip title="북마크">
                                <Button
                                  size="small" type="text"
                                  style={{ padding: 0, width: 20, height: 20 }}
                                  icon={bookmarkedIds.has(r.doc_id)
                                    ? <StarFilled style={{ color: '#faad14' }} />
                                    : <StarOutlined style={{ color: '#bfbfbf' }} />}
                                  onClick={(e) => { e.stopPropagation(); toggleBookmark(r.doc_id) }}
                                />
                              </Tooltip>
                            </Space>
                          </div>
                          <Row justify="space-between" align="middle">
                            <Col>
                              <Space size={4}>
                                <Tag color={CATEGORY_COLOR[r.category]} style={{ marginRight: 0 }}>{CATEGORY_LABEL[r.category] ?? r.category}</Tag>
                                {r.score > 0 && (
                                  <Tooltip title={`관련도 ${Math.round(r.score * 100)}%`}>
                                    <Tag style={{ margin: 0 }}>{Math.round(r.score * 100)}%</Tag>
                                  </Tooltip>
                                )}
                                {r.memo && (
                                  <Tooltip title="클릭해서 특이사항 확인">
                                    <Tag style={{ margin: 0, background: '#fffbe6', border: '1px solid #ffe58f', color: '#7c6000' }}>특이사항</Tag>
                                  </Tooltip>
                                )}
                              </Space>
                            </Col>
                            <Col><Text type="secondary" style={{ fontSize: 11.5 }}>{formatDate(r.uploaded_at)}</Text></Col>
                          </Row>
                        </Card>
                      </Col>
                    ))}
                </Row>
              ) : (
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                {sortResults(results.results, sortBy)
                  .slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
                  .map((r) => {
                    const CAT_ACCENT = { spec: '#1677ff', research: '#722ed1', presentation: '#13c2c2', report: '#52c41a' }
                    const accent = CAT_ACCENT[r.category] ?? '#8c8c8c'
                    return (
                      <Card
                        key={r.doc_id}
                        size="small"
                        hoverable
                        onClick={() => openDetail(r)}
                        style={{ borderLeft: `4px solid ${accent}`, borderRadius: 8, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
                        styles={{ body: { padding: '12px 16px' } }}
                        title={
                          <Tooltip title={r.content_preview || '미리보기를 불러올 수 없습니다'} placement="topLeft" mouseEnterDelay={0.3}>
                            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                              <span style={{ fontSize: 18, flexShrink: 0 }}>
                                {FILE_TYPE_ICON[r.file_type] ?? <FileOutlined style={{ color: '#8c8c8c' }} />}
                              </span>
                              {r.title && r.title !== r.filename ? (
                                <>
                                  <Text strong ellipsis style={{ fontSize: 14, maxWidth: '55%' }}>{highlight(r.title, results.query)}</Text>
                                  <Text type="secondary" ellipsis style={{ fontSize: 11 }}>{r.filename}</Text>
                                </>
                              ) : (
                                <Text strong ellipsis style={{ fontSize: 14 }}>{highlight(r.filename, results.query)}</Text>
                              )}
                            </div>
                          </Tooltip>
                        }
                        extra={
                          <Space size={4}>
                            <Tooltip title={'북마크'}>
                              <Button
                                size="small" type="text"
                                icon={bookmarkedIds.has(r.doc_id)
                                  ? <StarFilled style={{ color: '#faad14' }} />
                                  : <StarOutlined />}
                                onClick={(e) => { e.stopPropagation(); toggleBookmark(r.doc_id) }}
                              />
                            </Tooltip>
                            <Button
                              size="small" icon={<DownloadOutlined />} href={`${API}/files/${r.doc_id}`} download
                              type="primary" ghost
                              onClick={(e) => e.stopPropagation()}
                            >
                              다운로드
                            </Button>
                          </Space>
                        }
                      >
                        <Space style={{ marginBottom: 12 }} size={6} wrap>
                          <Tag color={CATEGORY_COLOR[r.category]} style={{ margin: 0 }}>{CATEGORY_LABEL[r.category] ?? r.category}</Tag>
                          {r.file_type && <Tag color={FILE_TYPE_COLOR[r.file_type] ?? 'default'} style={{ margin: 0 }}>{r.file_type.toUpperCase()}</Tag>}
                          {r.score > 0 && (
                            <Tooltip title={`관련도 ${Math.round(r.score * 100)}%`}>
                              <Tag style={{ margin: 0 }}>{Math.round(r.score * 100)}%</Tag>
                            </Tooltip>
                          )}
                          {r.page_num > 0 && (
                            <Tooltip title={`${r.page_num}페이지에서 발견`}>
                              <Tag style={{ margin: 0, background: '#f5f5f5', border: '1px solid #d9d9d9', color: '#595959', fontSize: 11 }}>
                                p.{r.page_num}
                              </Tag>
                            </Tooltip>
                          )}
                        </Space>

                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 4, flexWrap: 'wrap', gap: 8, lineHeight: 1.4 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{r.pages}페이지</Text>
                          <div style={{ textAlign: 'right', lineHeight: 1.4 }}>
                            {r.uploaded_by && r.uploaded_by !== 'anonymous' && (
                              <div>
                                <Tooltip title={r.uploaded_by}>
                                  <Text type="secondary" style={{ fontSize: 12 }}><UserOutlined style={{ marginRight: 4 }} />{r.uploaded_by_name ?? r.uploaded_by}</Text>
                                </Tooltip>
                              </div>
                            )}
                            <Space size={12}>
                              <Text type="secondary" style={{ fontSize: 12 }}><EyeOutlined style={{ marginRight: 4 }} />{r.view_count ?? 0}</Text>
                              {r.uploaded_at && (
                                <Tooltip title={r.uploaded_at}>
                                  <Text type="secondary" style={{ fontSize: 12 }}>{formatDate(r.uploaded_at)}</Text>
                                </Tooltip>
                              )}
                            </Space>
                          </div>
                        </div>

                        {r.memo && (
                          <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, padding: '5px 10px', marginBottom: 8, fontSize: 12, color: '#7c6000' }}>
                            <Text strong style={{ fontSize: 11, color: '#7c6000' }}>특이사항  </Text>{r.memo}
                          </div>
                        )}

                        <div style={{ background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, padding: '6px 10px' }}>
                          <Paragraph type="secondary" style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6 }} ellipsis={{ rows: 2 }}>
                            {highlight(r.snippet, results.query)}
                          </Paragraph>
                        </div>
                      </Card>
                    )
                  })}
              </Space>
              )}
              {results.total > PAGE_SIZE && (
                <div style={{ textAlign: 'center', marginTop: 20 }}>
                  <Pagination current={page} pageSize={PAGE_SIZE} total={results.total} onChange={(p) => { setPage(p); window.scrollTo(0, 0) }} showSizeChanger={false} />
                </div>
              )}
            </>
          )}

        </>
      )}

      {!loading && results === null && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, marginBottom: 14 }}>
            <Text type="secondary" style={{ fontSize: 12.5 }}>
              {browseDocs.length}건 표시 중
              {browseCategory && (
                <> · <a onClick={() => setBrowseCategory(null)}>전체 카테고리</a></>
              )}
            </Text>
            <Radio.Group
              size="small" value={viewMode}
              onChange={(e) => setViewMode(e.target.value)}
              optionType="button"
            >
              <Tooltip title="리스트형">
                <Radio.Button value="list"><UnorderedListOutlined /></Radio.Button>
              </Tooltip>
              <Tooltip title="카드형">
                <Radio.Button value="grid"><AppstoreOutlined /></Radio.Button>
              </Tooltip>
            </Radio.Group>
          </div>

          <Space size={[8, 8]} wrap style={{ marginBottom: 20 }}>
            <Tag.CheckableTag checked={!browseCategory} onChange={() => setBrowseCategory(null)}>
              전체 {stats?.total_documents ?? 0}
            </Tag.CheckableTag>
            {Object.keys(CATEGORY_LABEL).map(cat => (
              <Tag.CheckableTag
                key={cat}
                checked={browseCategory === cat}
                onChange={(checked) => setBrowseCategory(checked ? cat : null)}
              >
                {CATEGORY_LABEL[cat]} {stats?.by_category?.[cat] ?? 0}
              </Tag.CheckableTag>
            ))}
            {extraCategories.map(cat => (
              <Tag.CheckableTag
                key={cat}
                checked={browseCategory === cat}
                onChange={(checked) => setBrowseCategory(checked ? cat : null)}
              >
                {cat} {stats?.by_category?.[cat] ?? 0}
              </Tag.CheckableTag>
            ))}
          </Space>

          {browseLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
          ) : browseDocs.length === 0 ? (
            <Empty description="조건에 맞는 문서가 없습니다" style={{ margin: '32px 0' }} />
          ) : viewMode === 'grid' ? (
            <Row gutter={[16, 16]}>
              {browseDocs.slice((browsePage - 1) * PAGE_SIZE, browsePage * PAGE_SIZE).map(doc => (
                <Col key={doc.doc_id} xs={24} sm={12} md={8}>
                  <Card
                    size="small" hoverable
                    style={{ borderRadius: 8 }}
                    styles={{ body: { padding: 12 } }}
                    onClick={() => openDetail(doc)}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
                      <Tooltip title={doc.content_preview || '미리보기를 불러올 수 없습니다'} placement="topLeft" mouseEnterDelay={0.3}>
                        <Text strong style={{ fontSize: 13.5, lineHeight: 1.4 }} ellipsis>
                          {doc.snippet}
                        </Text>
                      </Tooltip>
                      <Space size={4} style={{ flexShrink: 0 }}>
                        {doc.file_type && (
                          <Tag color={FILE_TYPE_COLOR[doc.file_type] ?? 'default'} style={{ margin: 0 }}>
                            {doc.file_type.toUpperCase()}
                          </Tag>
                        )}
                        <Tooltip title="북마크">
                          <Button
                            size="small" type="text"
                            style={{ padding: 0, width: 20, height: 20 }}
                            icon={bookmarkedIds.has(doc.doc_id)
                              ? <StarFilled style={{ color: '#faad14' }} />
                              : <StarOutlined style={{ color: '#bfbfbf' }} />}
                            onClick={(e) => { e.stopPropagation(); toggleBookmark(doc.doc_id) }}
                          />
                        </Tooltip>
                      </Space>
                    </div>
                    <Row justify="space-between" align="middle">
                      <Col>
                        <Space size={4}>
                          <Tag color={CATEGORY_COLOR[doc.category]} style={{ marginRight: 0 }}>{CATEGORY_LABEL[doc.category] ?? doc.category}</Tag>
                          {doc.memo && (
                            <Tooltip title="클릭해서 특이사항 확인">
                              <Tag style={{ margin: 0, background: '#fffbe6', border: '1px solid #ffe58f', color: '#7c6000' }}>특이사항</Tag>
                            </Tooltip>
                          )}
                        </Space>
                      </Col>
                      <Col><Text type="secondary" style={{ fontSize: 11.5 }}>{formatDate(doc.uploaded_at)}</Text></Col>
                    </Row>
                  </Card>
                </Col>
              ))}
            </Row>
          ) : (
            <Space direction="vertical" style={{ width: '100%' }} size={10}>
              {browseDocs.slice((browsePage - 1) * PAGE_SIZE, browsePage * PAGE_SIZE).map(doc => {
                const CAT_ACCENT = { spec: '#1677ff', research: '#722ed1', presentation: '#13c2c2', report: '#52c41a' }
                const accent = CAT_ACCENT[doc.category] ?? '#8c8c8c'
                return (
                  <Card
                    key={doc.doc_id}
                    size="small"
                    hoverable
                    onClick={() => openDetail(doc)}
                    style={{ borderLeft: `4px solid ${accent}`, borderRadius: 8, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
                    styles={{ body: { padding: '12px 16px' } }}
                    title={
                      <Tooltip title={doc.content_preview || '미리보기를 불러올 수 없습니다'} placement="topLeft" mouseEnterDelay={0.3}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                          <span style={{ fontSize: 18, flexShrink: 0 }}>
                            {FILE_TYPE_ICON[doc.file_type] ?? <FileOutlined style={{ color: '#8c8c8c' }} />}
                          </span>
                          {doc.title && doc.title !== doc.filename ? (
                            <>
                              <Text strong ellipsis style={{ fontSize: 14, maxWidth: '55%' }}>{doc.title}</Text>
                              <Text type="secondary" ellipsis style={{ fontSize: 11 }}>{doc.filename}</Text>
                            </>
                          ) : (
                            <Text strong ellipsis style={{ fontSize: 14 }}>{doc.filename}</Text>
                          )}
                        </div>
                      </Tooltip>
                    }
                    extra={
                      <Space size={4}>
                        <Tooltip title={'북마크'}>
                          <Button
                            size="small" type="text"
                            icon={bookmarkedIds.has(doc.doc_id)
                              ? <StarFilled style={{ color: '#faad14' }} />
                              : <StarOutlined />}
                            onClick={(e) => { e.stopPropagation(); toggleBookmark(doc.doc_id) }}
                          />
                        </Tooltip>
                        <Button
                          size="small" icon={<DownloadOutlined />} href={`${API}/files/${doc.doc_id}`} download
                          type="primary" ghost
                          onClick={(e) => e.stopPropagation()}
                        >
                          다운로드
                        </Button>
                      </Space>
                    }
                  >
                    <Space style={{ marginBottom: 12 }} size={6} wrap>
                      <Tag color={CATEGORY_COLOR[doc.category]} style={{ margin: 0 }}>{CATEGORY_LABEL[doc.category] ?? doc.category}</Tag>
                      {doc.file_type && <Tag color={FILE_TYPE_COLOR[doc.file_type] ?? 'default'} style={{ margin: 0 }}>{doc.file_type.toUpperCase()}</Tag>}
                    </Space>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 4, flexWrap: 'wrap', gap: 8, lineHeight: 1.4 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{doc.pages}페이지</Text>
                      <div style={{ textAlign: 'right', lineHeight: 1.4 }}>
                        {doc.uploaded_by && doc.uploaded_by !== 'anonymous' && (
                          <div>
                            <Tooltip title={doc.uploaded_by}>
                              <Text type="secondary" style={{ fontSize: 12 }}><UserOutlined style={{ marginRight: 4 }} />{doc.uploaded_by_name ?? doc.uploaded_by}</Text>
                            </Tooltip>
                          </div>
                        )}
                        <Space size={12}>
                          <Text type="secondary" style={{ fontSize: 12 }}><EyeOutlined style={{ marginRight: 4 }} />{doc.view_count ?? 0}</Text>
                          {doc.uploaded_at && (
                            <Tooltip title={doc.uploaded_at}>
                              <Text type="secondary" style={{ fontSize: 12 }}>{formatDate(doc.uploaded_at)}</Text>
                            </Tooltip>
                          )}
                        </Space>
                      </div>
                    </div>

                    {doc.memo && (
                      <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, padding: '5px 10px', fontSize: 12, color: '#7c6000' }}>
                        <Text strong style={{ fontSize: 11, color: '#7c6000' }}>특이사항  </Text>{doc.memo}
                      </div>
                    )}
                  </Card>
                )
              })}
            </Space>
          )}
          {browseDocs.length > PAGE_SIZE && (
            <div style={{ textAlign: 'center', marginTop: 20 }}>
              <Pagination
                current={browsePage} pageSize={PAGE_SIZE} total={browseDocs.length}
                onChange={(p) => { setBrowsePage(p); window.scrollTo(0, 0) }} showSizeChanger={false}
              />
            </div>
          )}
        </div>
      )}

      <Modal
        title={detailDoc?.snippet}
        open={!!detailDoc}
        onCancel={() => setDetailDoc(null)}
        footer={null}
        width={560}
      >
        {detailDoc && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <Space wrap size={6}>
                <Tag color={CATEGORY_COLOR[detailDoc.category]} style={{ margin: 0 }}>
                  {CATEGORY_LABEL[detailDoc.category] ?? detailDoc.category}
                </Tag>
                {detailDoc.file_type && (
                  <Tag color={FILE_TYPE_COLOR[detailDoc.file_type] ?? 'default'} style={{ margin: 0 }}>
                    {detailDoc.file_type.toUpperCase()}
                  </Tag>
                )}
                {detailDoc.uploaded_at && (
                  <Text type="secondary" style={{ fontSize: 12 }}>{formatDate(detailDoc.uploaded_at)}</Text>
                )}
              </Space>

              <Space>
                <Button icon={<DownloadOutlined />} type="primary" ghost href={`${API}/files/${detailDoc.doc_id}`} download>
                  다운로드
                </Button>
                <Tooltip title="북마크">
                  <Button
                    icon={bookmarkedIds.has(detailDoc.doc_id) ? <StarFilled style={{ color: '#faad14' }} /> : <StarOutlined />}
                    onClick={() => toggleBookmark(detailDoc.doc_id)}
                  />
                </Tooltip>
              </Space>
            </div>

            {detailDoc.memo && (
              <div style={{ background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, padding: '10px 14px', marginBottom: 14 }}>
                <Text strong style={{ fontSize: 12, color: '#7c6000', display: 'block', marginBottom: 4 }}>특이사항</Text>
                <Text style={{ fontSize: 13.5, color: '#7c6000', whiteSpace: 'pre-wrap' }}>{detailDoc.memo}</Text>
              </div>
            )}

            {aiEnabled && (
              <>
                <Divider style={{ margin: '16px 0' }} />

                <div style={{ marginBottom: 16 }}>
                  <Space size={6} style={{ marginBottom: 8 }}>
                    <RobotOutlined style={{ color: '#1677ff' }} />
                    <Text strong style={{ fontSize: 13, color: '#1677ff' }}>AI 요약</Text>
                  </Space>
                  {summaryLoading ? (
                    <div style={{ background: '#f0f5ff', border: '1px solid #91caff', borderRadius: 8, padding: '10px 14px' }}>
                      <Space size={6}><Spin size="small" /><Text type="secondary" style={{ fontSize: 12.5 }}>AI가 문서를 요약하고 있어요...</Text></Space>
                    </div>
                  ) : summary ? (
                    <div style={{ background: '#f0f5ff', border: '1px solid #91caff', borderRadius: 8, padding: '10px 14px' }}>
                      <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7 }}>{summary}</Paragraph>
                    </div>
                  ) : (
                    <div style={{ background: '#fafafa', border: '1px dashed #e0e0e0', borderRadius: 8, padding: '10px 14px', color: '#bfbfbf', fontSize: 12.5 }}>
                      {summaryError || '요약을 표시할 수 없습니다'}
                    </div>
                  )}
                </div>
              </>
            )}

            <Divider style={{ margin: '16px 0' }} />

            <Text strong style={{ fontSize: 13 }}>댓글 {comments.length}</Text>
            <div style={{ maxHeight: 240, overflowY: 'auto', margin: '10px 0' }}>
              {commentsLoading ? (
                <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /></div>
              ) : comments.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12.5 }}>아직 댓글이 없습니다</Text>
              ) : (
                <Space direction="vertical" style={{ width: '100%' }} size={8}>
                  {comments.map((c) => (
                    <div key={c.id} style={{ background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, padding: '8px 10px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                        <Text strong style={{ fontSize: 12 }}>{c.user_name}</Text>
                        <Space size={8}>
                          <Text type="secondary" style={{ fontSize: 11 }}>{formatDate(c.created_at)}</Text>
                          <Button
                            type="text" size="small" style={{ padding: 0, height: 'auto' }}
                            icon={<CloseOutlined style={{ fontSize: 10, color: '#bfbfbf' }} />}
                            onClick={() => deleteComment(c.id)}
                          />
                        </Space>
                      </div>
                      <Text style={{ fontSize: 12.5 }}>{c.content}</Text>
                    </div>
                  ))}
                </Space>
              )}
            </div>

            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="댓글을 입력하세요"
                value={newComment}
                onChange={(e) => setNewComment(e.target.value)}
                onPressEnter={postComment}
                maxLength={1000}
              />
              <Button type="primary" loading={commentPosting} onClick={postComment}>등록</Button>
            </Space.Compact>
          </>
        )}
      </Modal>
    </div>
  )
}
