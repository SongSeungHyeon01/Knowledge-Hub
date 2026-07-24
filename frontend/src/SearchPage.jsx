// SearchPage.jsx — 검색 화면
// 2026-07-11 목업 반영: 관리자 진입은 이제 App.jsx 최상위 탭이 담당 — 이 화면의
// 톱니바퀴+Drawer는 제거. 카테고리·파일형식 필터는 "고급 검색"(기본 접힘) 안에 위치.
//
// 2026-07-23: 검색 모드를 2단으로 재정의 — "파일명 검색"(기본값, 파일명 문자열에
// 검색어가 그대로 있는 문서만)과 "유사 검색"(문서 내용을 MiniLM 임베딩 유사도로 찾음,
// 모호한 문구도 폭넓게 잡히도록 임계값 없이 다 보여줌). 예전 alpha 슬라이더/3단
// 프리셋(정확히 일치/균형/비슷한 내용)과 "정확한 검색"+"유사 의미 검색" 동시 표시 방식은
// 모두 이 2단 모드 토글로 대체됐다 — /search는 이제 선택된 모드 하나에 대한
// 단일 results 목록만 반환한다.

import { useState, useEffect, useRef } from 'react'
import {
  Input, Button, Card, Tag, Typography,
  Space, Divider, Empty, Spin, Radio, List, Pagination, Tooltip, Alert, Select,
  Row, Col, Modal, message, DatePicker, AutoComplete,
} from 'antd'
import dayjs from 'dayjs'
import {
  SearchOutlined, FileTextOutlined, HistoryOutlined, CloseOutlined,
  FilePdfOutlined, FileWordOutlined, FilePptOutlined, FileExcelOutlined,
  FileImageOutlined, FileMarkdownOutlined, FileOutlined, DownloadOutlined,
  StarOutlined, StarFilled, EyeOutlined, UserOutlined,
  UnorderedListOutlined, AppstoreOutlined,
  FileSearchOutlined, BulbOutlined, ReloadOutlined,
} from '@ant-design/icons'
import axios from 'axios'
import useIsNarrow from './useIsNarrow'

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

// 검색 모드 2단 — "파일명 검색"(기본값, 파일명 문자열 포함 여부)과 "유사 검색"(문서
// 내용을 임베딩 유사도로 찾음). 실제 검색 로직 분기는 백엔드(main.py의 mode 파라미터)가 담당.
const SEARCH_MODES = [
  { key: 'filename', label: '파일명 검색', icon: <FileSearchOutlined /> },
  { key: 'semantic', label: '유사 검색',   icon: <BulbOutlined /> },
]

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
  const isNarrow = useIsNarrow()
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

  // 알림(댓글 등)에서 넘어온 경우 — 검색 결과가 오면 그 문서를 바로 상세 팝업으로 열어준다.
  // ref로 두는 이유: 한 번 쓰고 나면(useEffect에서 openDetail 호출) 다시 안 열리게 지워야 해서.
  const pendingOpenDocIdRef = useRef((() => {
    const id = localStorage.getItem('km_launch_doc_id')
    if (id) localStorage.removeItem('km_launch_doc_id')
    return id ? Number(id) : null
  })())

  const closeTip = () => {
    setTipVisible(false)
    localStorage.setItem('km_tip_closed', 'true')
  }

  const [query,      setQuery]      = useState('')
  const [searchMode, setSearchMode] = useState('filename')  // 'filename'(기본) | 'semantic'
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
  const PAGE_SIZE = 10
  const inputRef = useRef(null)
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

  const openDetail = (doc) => {
    setDetailDoc(doc)
    setNewComment('')
    setComments([])
    setCommentsLoading(true)
    axios.get(`${API}/documents/${doc.doc_id}/comments`)
      .then(res => setComments(res.data))
      .catch(() => {})
      .finally(() => setCommentsLoading(false))
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

  // 마운트 시 자동 포커스 + 커스텀 카테고리 존재 여부 확인 + 북마크 목록
  useEffect(() => {
    inputRef.current?.focus()
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
  // [2026-07-24] 예전엔 8초마다 자동으로 다시 불러왔는데, 검색 결과 화면을 보고 있을 때도
  // 이 둘러보기용 API가 백그라운드에서 계속 호출돼(화면엔 안 쓰이는데도) 낭비였다 —
  // 실제 네트워크 탭에서 확인된 문제. 자동 폴링을 없애고 수동 새로고침 버튼으로 바꿨다.
  const fetchBrowse = (showSpinner) => {
    if (showSpinner) setBrowseLoading(true)
    axios.get(`${API}/search`, {
      params: { q: '', ...(browseCategory ? { category: browseCategory } : {}) },
    })
      .then(res => setBrowseDocs(res.data.results ?? []))
      .catch(() => setBrowseDocs([]))
      .finally(() => { if (showSpinner) setBrowseLoading(false) })
  }

  useEffect(() => {
    setBrowsePage(1)
    fetchBrowse(true)
  }, [browseCategory]) // eslint-disable-line

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

  // 최근 검색어 — 브라우저 localStorage가 아니라 계정에 귀속된 서버측 기록(/me/search-history).
  // 로그인이 꺼져 있으면 "anonymous" 단일 공용 기록으로 동작(다른 /me 기능과 동일 원칙).
  const loadRecent = () => {
    axios.get(`${API}/me/search-history`, { params: { limit: 5 } })
      .then(res => setRecentList(res.data))
      .catch(() => {})
  }
  useEffect(() => { loadRecent() }, [])

  const clearRecentHistory = () => {
    axios.delete(`${API}/me/search-history`)
      .then(() => setRecentList([]))
      .catch(() => message.error('검색 기록 삭제에 실패했습니다'))
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
    try {
      const res = await axios.get(`${API}/search`, {
        params: {
          q, mode: searchMode,
          ...(categoryOverride ? { category: categoryOverride } : {}),
          ...(fileType  ? { file_type: fileType } : {}),
          ...(author    ? { uploaded_by: author } : {}),
          ...(dateRange?.[0] ? { date_from: dateRange[0].format('YYYY-MM-DD') } : {}),
          ...(dateRange?.[1] ? { date_to: dateRange[1].format('YYYY-MM-DD') } : {}),
        },
      })
      setResults(res.data)
      setQuery(q)

      // 알림에서 넘어온 경우 — 검색 결과 중 그 문서를 찾아 바로 상세 팝업으로 연다(한 번만).
      if (pendingOpenDocIdRef.current != null) {
        const target = (res.data.results ?? []).find(r => r.doc_id === pendingOpenDocIdRef.current)
        pendingOpenDocIdRef.current = null
        if (target) openDetail(target)
      }

      if (res.data.total === 0) {
        axios.get(`${API}/admin/stats`)
          .then(s => setTopQueries(s.data.top_queries ?? []))
          .catch(() => {})
      } else {
        setTopQueries([])
      }

      // 검색 자체가 서버에 SearchLog(user_email 포함)로 이미 기록되므로, 여기서는
      // 그 최신 상태를 다시 읽어오기만 한다 — 로컬에서 직접 조작하지 않는다.
      if (q.trim()) loadRecent()
    } catch {
      setResults({ query: q, mode: searchMode, total: 0, results: [], error: '서버 연결 오류' })
    } finally {
      setLoading(false)
    }
  }

  // 검색 모드 변경 시 자동 재검색 (이미 검색을 실행한 상태일 때만)
  useEffect(() => {
    if (!results?.query) return
    handleSearch(results.query)
  }, [searchMode]) // eslint-disable-line

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch()
    if (e.key === 'Escape') setShowRecent(false)
  }

  const handleRecentClick = (q) => {
    setQuery(q)
    handleSearch(q)
  }

  // 카드형 보기 — "정확한 검색"/"유사 의미 검색" 두 섹션이 똑같은 카드 모양을 쓰므로 공용 함수로 뺐다
  const renderGridCard = (r) => (
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
              {r.filename}
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
              <Tag color={r.category ? CATEGORY_COLOR[r.category] : undefined} style={{ marginRight: 0 }}>{r.category ? (CATEGORY_LABEL[r.category] ?? r.category) : '미지정'}</Tag>
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
  )

  // 리스트형 보기
  const renderListCard = (r) => {
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
              <Text strong ellipsis style={{ fontSize: 14 }}>{highlight(r.filename, results?.query)}</Text>
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
          <Tag color={r.category ? CATEGORY_COLOR[r.category] : undefined} style={{ margin: 0 }}>{r.category ? (CATEGORY_LABEL[r.category] ?? r.category) : '미지정'}</Tag>
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
            {highlight(r.snippet, results?.query)}
          </Paragraph>
        </div>
      </Card>
    )
  }

  // 정확한 검색/유사 의미 검색 — 한 섹션(제목 + 카드 목록 + 페이지네이션)을 통째로 그린다
  const renderResultsSection = (sectionResults, label, sectionPage, setSectionPage) => {
    if (!sectionResults || sectionResults.length === 0) return null
    const sliced = sortResults(sectionResults, sortBy).slice((sectionPage - 1) * PAGE_SIZE, sectionPage * PAGE_SIZE)
    return (
      <div style={{ marginBottom: 28 }}>
        {label && (
          <Text strong style={{ fontSize: 13.5 }}>
            {label} <Text type="secondary" style={{ fontWeight: 400, fontSize: 12.5 }}>{sectionResults.length}건</Text>
          </Text>
        )}
        <div style={{ marginTop: label ? 10 : 0 }}>
          {viewMode === 'grid' ? (
            <Row gutter={[16, 16]}>{sliced.map(renderGridCard)}</Row>
          ) : (
            <Space orientation="vertical" style={{ width: '100%' }} size={10}>{sliced.map(renderListCard)}</Space>
          )}
        </div>
        {sectionResults.length > PAGE_SIZE && (
          <div style={{ textAlign: 'center', marginTop: 16 }}>
            <Pagination
              size="small"
              current={sectionPage} pageSize={PAGE_SIZE} total={sectionResults.length}
              onChange={(p) => { setSectionPage(p); window.scrollTo(0, 0) }}
              showSizeChanger={false}
            />
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: '24px 24px 40px' }}>
      {/* flex="200px" 빈 칸: 업로드/관리자 화면의 좌측 사이드바 폭(gutter 포함)만큼 맞춰서
          모든 화면의 제목·본문이 같은 x 위치에서 시작하도록 함 */}
      <Row gutter={20} wrap={isNarrow}>
        <Col flex={isNarrow ? '0 0 0px' : '200px'} />
        <Col flex="auto" style={{ minWidth: 0 }}>
      <Title level={3} style={{ marginBottom: 2 }}>통합 검색</Title>

      {tipVisible && (
        <Alert
          type="info"
          showIcon
          closable
          onClose={closeTip}
          style={{ marginBottom: 20 }}
          title="검색 팁"
          description={
            <ul style={{ margin: '4px 0 0', paddingLeft: 20, lineHeight: 2 }}>
              <li>기본은 <strong>파일명 검색</strong>입니다. 문서 <strong>내용</strong>에서 찾으려면 <strong>고급 검색</strong>에서 <strong>유사 검색</strong>으로 바꿔보세요. 예) <Typography.Text code>모터 설계 사양</Typography.Text></li>
              <li>결과가 마음에 안 들면 <strong>고급 검색</strong>에서 검색 모드·필터를 조절해 보세요.</li>
              <li><kbd style={{ padding: '1px 5px', background: '#f0f0f0', border: '1px solid #d9d9d9', borderRadius: 3, fontSize: 12 }}>/</kbd> 키를 누르면 어디서든 검색창으로 바로 이동합니다.</li>
            </ul>
          }
        />
      )}

      {/* ── 검색창 + 최근 검색어 드롭다운 (스크롤해도 화면 상단에 고정) ── */}
      <div style={{
        position: 'sticky', top: 64, zIndex: 10,
        background: '#fff', marginTop: 16,
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
                  >
                    <Space><HistoryOutlined style={{ color: '#aaa' }} /><Text>{item.query}</Text></Space>
                  </List.Item>
                )}
              />
              <div style={{ textAlign: 'right', padding: '4px 12px 8px' }}>
                <Button type="link" size="small" style={{ padding: 0, fontSize: 12 }} onMouseDown={(e) => { e.preventDefault(); clearRecentHistory() }}>
                  검색 기록 전체 삭제
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 고급 검색 토글 ───────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginBottom: advancedOpen ? 12 : 20 }}>
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
              {renderResultsSection(results.results, null, page, setPage)}
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
            <Tooltip title="새로고침">
              <Button
                size="small" icon={<ReloadOutlined />} loading={browseLoading}
                onClick={() => fetchBrowse(true)}
              />
            </Tooltip>
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
                          {doc.filename}
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
                          <Tag color={doc.category ? CATEGORY_COLOR[doc.category] : undefined} style={{ marginRight: 0 }}>{doc.category ? (CATEGORY_LABEL[doc.category] ?? doc.category) : '미지정'}</Tag>
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
            <Space orientation="vertical" style={{ width: '100%' }} size={10}>
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
                          <Text strong ellipsis style={{ fontSize: 14 }}>{doc.filename}</Text>
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
                      <Tag color={doc.category ? CATEGORY_COLOR[doc.category] : undefined} style={{ margin: 0 }}>{doc.category ? (CATEGORY_LABEL[doc.category] ?? doc.category) : '미지정'}</Tag>
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
        title={detailDoc?.filename}
        open={!!detailDoc}
        onCancel={() => setDetailDoc(null)}
        footer={null}
        width={560}
      >
        {detailDoc && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <Space wrap size={6}>
                <Tag color={detailDoc.category ? CATEGORY_COLOR[detailDoc.category] : undefined} style={{ margin: 0 }}>
                  {detailDoc.category ? (CATEGORY_LABEL[detailDoc.category] ?? detailDoc.category) : '미지정'}
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

            <Divider style={{ margin: '16px 0' }} />

            <Text strong style={{ fontSize: 13 }}>댓글 {comments.length}</Text>
            <div style={{ maxHeight: 240, overflowY: 'auto', margin: '10px 0' }}>
              {commentsLoading ? (
                <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /></div>
              ) : comments.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12.5 }}>아직 댓글이 없습니다</Text>
              ) : (
                <Space orientation="vertical" style={{ width: '100%' }} size={8}>
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
        </Col>
      </Row>
    </div>
  )
}
