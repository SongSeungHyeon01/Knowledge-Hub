// AdminPage.jsx — 관리자 화면
// 문서 목록 조회, 저신뢰 문서 확인, 문서 삭제 기능을 담당합니다

import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Table, Button, Tag, Tabs, message, Popconfirm, Typography, Row, Col, Statistic, Card as ACard, List, Drawer, Collapse, Spin, Alert, Input, Space, Select, Progress, Tooltip, Modal, Radio, Empty } from 'antd'
import { DeleteOutlined, WarningOutlined, FileTextOutlined, HistoryOutlined, FileOutlined, SearchOutlined, ExclamationCircleOutlined, ReloadOutlined, SyncOutlined, EditOutlined, DownloadOutlined } from '@ant-design/icons'
import axios from 'axios'

const { Title } = Typography

// 백엔드 서버 주소 (FastAPI 서버가 켜진 주소)
const API = import.meta.env.VITE_API_URL

// ── API 호출 함수들 ───────────────────────────────────────────────────────────

// 전체 문서 목록 가져오기
const fetchDocuments = () => axios.get(`${API}/admin/documents`).then(r => r.data)

// 저신뢰 문서 목록 가져오기
const fetchFlagged = () => axios.get(`${API}/admin/flagged`).then(r => r.data)

// 검색 기록 가져오기
const fetchHistory = () => axios.get(`${API}/admin/history`).then(r => r.data)

// 통계 가져오기
const fetchStats = () => axios.get(`${API}/admin/stats`).then(r => r.data)

// 업로드 트렌드 가져오기
const fetchTrend = (period = 7) => axios.get(`${API}/admin/stats/trend`, { params: { period } }).then(r => r.data)

// 문서 삭제하기 (id를 넘겨주면 해당 문서를 삭제)
const deleteDocument = (id) => axios.delete(`${API}/admin/documents/${id}`)

// ── 관리자 화면 컴포넌트 ──────────────────────────────────────────────────────

export default function AdminPage({ onNavigate }) {
  // queryClient: 데이터를 다시 불러올 때 사용합니다 (삭제 후 목록 갱신 등)
  const queryClient = useQueryClient()

  // 문서 상세 보기 Drawer 상태
  const [drawerOpen,    setDrawerOpen]    = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailData,    setDetailData]    = useState(null)

  // 메모 편집 상태
  const [memoEdit,    setMemoEdit]    = useState(null)   // null = 보기 모드, string = 편집 중
  const [memoSaving,  setMemoSaving]  = useState(false)

  // 제목 편집 상태
  const [titleEdit,   setTitleEdit]   = useState(null)
  const [titleSaving, setTitleSaving] = useState(false)

  const saveTitle = async () => {
    if (!detailData) return
    setTitleSaving(true)
    try {
      await axios.patch(`${API}/admin/documents/${detailData.id}/title`, { title: titleEdit ?? '' })
      setDetailData(prev => ({ ...prev, title: titleEdit?.trim() || null }))
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      message.success('제목이 저장됐습니다')
      setTitleEdit(null)
    } catch {
      message.error('저장 중 오류가 발생했습니다')
    } finally {
      setTitleSaving(false)
    }
  }

  const saveMemo = async () => {
    if (!detailData) return
    setMemoSaving(true)
    try {
      await axios.patch(`${API}/admin/documents/${detailData.id}/memo`, { memo: memoEdit })
      setDetailData(prev => ({ ...prev, memo: memoEdit || null }))
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      message.success('메모가 저장됐습니다')
      setMemoEdit(null)
    } catch {
      message.error('저장 중 오류가 발생했습니다')
    } finally {
      setMemoSaving(false)
    }
  }

  // OCR 수동 수정 모달 상태
  const [ocrModal,     setOcrModal]     = useState(false)
  const [ocrDoc,       setOcrDoc]       = useState(null)   // { id, filename, pages[] }
  const [ocrEdits,     setOcrEdits]     = useState({})     // { page_num: text }
  const [ocrSaving,    setOcrSaving]    = useState(false)

  // 관리자 문서 목록 필터 상태
  const [filterText,     setFilterText]     = useState('')
  const [filterCategory, setFilterCategory] = useState(null)
  const [filterStatus,   setFilterStatus]   = useState(null)
  const [filterFileType, setFilterFileType] = useState(null)

  // 상대 시간 포맷 (방금 전 / N분 전 / N시간 전 / N일 전 / 날짜)
  const formatDate = (dateStr) => {
    if (!dateStr) return '—'
    const date = new Date(dateStr.replace(' ', 'T'))
    const diff  = Math.floor((Date.now() - date) / 1000)
    if (diff < 60)     return '방금 전'
    if (diff < 3600)   return `${Math.floor(diff / 60)}분 전`
    if (diff < 86400)  return `${Math.floor(diff / 3600)}시간 전`
    if (diff < 604800) return `${Math.floor(diff / 86400)}일 전`
    return `${date.getFullYear()}.${String(date.getMonth()+1).padStart(2,'0')}.${String(date.getDate()).padStart(2,'0')}`
  }

  // 자동 새로고침 (30초 간격)
  const [autoRefresh, setAutoRefresh] = useState(false)
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(() => queryClient.invalidateQueries(), 30_000)
    return () => clearInterval(id)
  }, [autoRefresh, queryClient])

  // 전체 새로고침
  const refreshAll = () => {
    queryClient.invalidateQueries()
    message.success('새로고침 완료')
  }

  // OCR 수정 모달 열기: 상세 정보 로드
  const openOcrEdit = async (doc) => {
    setOcrModal(true)
    setOcrDoc(null)
    setOcrEdits({})
    try {
      const res = await axios.get(`${API}/admin/documents/${doc.id}/detail`)
      const flaggedPages = res.data.pages.filter(p => p.flagged)
      setOcrDoc({ id: doc.id, filename: doc.filename, pages: flaggedPages })
      const initEdits = {}
      flaggedPages.forEach(p => { initEdits[p.page_num] = p.text })
      setOcrEdits(initEdits)
    } catch {
      message.error('데이터를 불러오지 못했습니다')
      setOcrModal(false)
    }
  }

  // OCR 수정 저장
  const saveOcrEdit = async (pageNum) => {
    if (!ocrDoc) return
    setOcrSaving(true)
    try {
      await axios.patch(`${API}/admin/documents/${ocrDoc.id}/pages/${pageNum}`, {
        text: ocrEdits[pageNum] ?? '',
      })
      message.success(`${pageNum}페이지 저장 완료`)
      // 해당 페이지를 목록에서 제거 (플래그 해제됨)
      setOcrDoc(prev => ({ ...prev, pages: prev.pages.filter(p => p.page_num !== pageNum) }))
      queryClient.invalidateQueries({ queryKey: ['flagged'] })
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    } catch {
      message.error('저장 중 오류가 발생했습니다')
    } finally {
      setOcrSaving(false)
    }
  }

  // 문서 목록을 CSV 파일로 다운로드합니다
  const downloadCSV = () => {
    const header = ['ID', '파일명', '파일형식', '원본경로', '카테고리', '상태', '페이지수', 'OCR저신뢰', '업로드시각']
    const CAT_KO = { spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서' }
    const rows = filteredDocuments.map(d => [
      d.id,
      `"${d.filename}"`,
      d.file_type ?? '',
      `"${d.original_path ?? ''}"`,
      CAT_KO[d.category] ?? d.category,
      d.status === 'success' ? '성공' : '실패',
      d.page_count,
      d.has_flagged ? '검토필요' : '정상',
      `"${d.uploaded_at}"`,
    ])
    const csv = '﻿' + [header, ...rows].map(r => r.join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `documents_${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const openDetail = async (doc) => {
    setDrawerOpen(true)
    setDetailLoading(true)
    setDetailData(null)
    try {
      const res = await axios.get(`${API}/admin/documents/${doc.id}/detail`)
      setDetailData(res.data)
    } catch {
      message.error('상세 데이터를 불러오지 못했습니다')
      setDrawerOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }

  // 전체 문서 목록 데이터 가져오기
  const { data: documents = [], isLoading: loadingDocs } = useQuery({
    queryKey: ['documents'],
    queryFn: fetchDocuments,
  })

  // 저신뢰 문서 목록 데이터 가져오기
  const { data: flagged = [], isLoading: loadingFlagged } = useQuery({
    queryKey: ['flagged'],
    queryFn: fetchFlagged,
  })

  // 검색 기록 데이터 가져오기
  const { data: history = [], isLoading: loadingHistory } = useQuery({
    queryKey: ['history'],
    queryFn: fetchHistory,
  })

  // 통계 데이터 가져오기
  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: fetchStats,
  })

  // 트렌드 차트 기간 선택 (7/14/30일) — useQuery보다 반드시 먼저 선언
  const [trendPeriod, setTrendPeriod] = useState(7)

  // 업로드 트렌드 데이터 가져오기
  const { data: trend = [] } = useQuery({
    queryKey: ['trend', trendPeriod],
    queryFn: () => fetchTrend(trendPeriod),
  })

  // 선택된 행 키 (일괄 작업용)
  const [selectedRowKeys,  setSelectedRowKeys]  = useState([])
  const [bulkCategory,     setBulkCategory]     = useState(null)

  // 일괄 카테고리 변경 mutation
  const bulkCategoryMutation = useMutation({
    mutationFn: ({ ids, category }) =>
      axios.patch(`${API}/admin/documents/bulk-category`, { ids, category }),
    onSuccess: (_, { ids, category }) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      setSelectedRowKeys([])
      setBulkCategory(null)
      message.success(`${ids.length}개 문서의 카테고리가 변경됐습니다`)
    },
    onError: () => message.error('카테고리 변경 중 오류가 발생했습니다'),
  })

  // 일괄 삭제 mutation
  const bulkDeleteMutation = useMutation({
    mutationFn: (ids) => axios.delete(`${API}/admin/documents/bulk`, { data: ids }),
    onSuccess: (_, ids) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['flagged'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      setSelectedRowKeys([])
      message.success(`${ids.length}개 문서가 삭제됐습니다`)
    },
    onError: () => message.error('일괄 삭제 중 오류가 발생했습니다'),
  })

  // 카테고리 변경 mutation
  const categoryMutation = useMutation({
    mutationFn: ({ id, category }) =>
      axios.patch(`${API}/admin/documents/${id}/category`, { category }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      message.success('카테고리가 변경됐습니다')
    },
    onError: () => message.error('카테고리 변경 중 오류가 발생했습니다'),
  })

  // 검색 기록 전체 초기화 mutation
  const clearHistoryMutation = useMutation({
    mutationFn: () => axios.delete(`${API}/admin/history`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      message.success('검색 기록이 초기화됐습니다')
    },
    onError: () => message.error('초기화 중 오류가 발생했습니다'),
  })

  // 재시도 mutation — 파싱 실패 문서를 다시 파싱합니다
  const retryMutation = useMutation({
    mutationFn: (id) => axios.post(`${API}/admin/documents/${id}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['flagged'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      message.success('재시도가 완료됐습니다')
    },
    onError: (err) => {
      const detail = err.response?.data?.detail ?? '재시도 중 오류가 발생했습니다'
      message.error(detail)
    },
  })

  // 삭제 버튼 클릭 시 실행되는 함수
  const deleteMutation = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      // 삭제 성공하면 목록을 다시 불러옵니다
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['flagged'] })
      message.success('문서가 삭제됐습니다')
    },
    onError: () => {
      message.error('삭제 중 오류가 발생했습니다')
    },
  })

  // 카테고리 한국어 이름 매핑
  const CATEGORY_LABEL = {
    spec:         { text: '사양서',    color: 'blue'   },
    research:     { text: '연구자료',  color: 'purple' },
    presentation: { text: '발표자료',  color: 'cyan'   },
    report:       { text: '보고서',    color: 'green'  },
  }

  // 파일 형식별 색상 (파일 유형 뱃지에 사용)
  const FILE_TYPE_COLOR = {
    pdf: 'volcano', docx: 'geekblue', pptx: 'orange', ppt: 'orange',
    xlsx: 'green', xls: 'green', hwp: 'purple', hwpx: 'purple',
    txt: 'default', md: 'cyan', png: 'magenta', jpg: 'magenta', jpeg: 'magenta',
  }

  // ── 전체 문서 테이블 컬럼 정의 ────────────────────────────────────────────
  const docColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 60,
    },
    {
      title: '파일명 / 문서 제목',
      dataIndex: 'filename',
      sorter: (a, b) => a.filename.localeCompare(b.filename),
      render: (name, record) => (
        <span style={{ cursor: 'pointer' }} onClick={() => openDetail(record)}>
          {/* 파일명 + 형식 태그 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <FileTextOutlined style={{ color: '#1677ff', flexShrink: 0 }} />
            <Tooltip title={name}>
              <Typography.Text
                style={{ color: '#1677ff', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'bottom' }}
              >
                {name}
              </Typography.Text>
            </Tooltip>
            {record.file_type && (
              <Tag color={FILE_TYPE_COLOR[record.file_type] ?? 'default'} style={{ fontSize: 11, margin: 0 }}>
                {record.file_type.toUpperCase()}
              </Tag>
            )}
          </div>

          {/* 자동 추출 제목 */}
          {record.title && record.title !== name && (
            <Tooltip title="파싱에서 자동 추출된 문서 제목">
              <Typography.Text
                type="secondary"
                style={{ fontSize: 11, display: 'block', marginTop: 2, fontStyle: 'italic' }}
                ellipsis
              >
                📄 {record.title}
              </Typography.Text>
            </Tooltip>
          )}

          {/* 폴더 업로드 원본 경로 */}
          {record.original_path && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 11, display: 'block', marginTop: 1 }}
              title={record.original_path}
            >
              📁 {record.original_path}
            </Typography.Text>
          )}
        </span>
      ),
    },
    {
      title: '카테고리',
      dataIndex: 'category',
      width: 130,
      render: (cat, record) => (
        <Select
          value={cat}
          size="small"
          style={{ width: 110 }}
          onChange={(val) => categoryMutation.mutate({ id: record.id, category: val })}
          options={[
            { value: 'spec',         label: '사양서' },
            { value: 'research',     label: '연구자료' },
            { value: 'presentation', label: '발표자료' },
            { value: 'report',       label: '보고서' },
          ]}
        />
      ),
    },
    {
      title: '상태',
      dataIndex: 'status',
      width: 90,
      render: (status) => (
        <Tag color={status === 'success' ? 'green' : 'red'}>
          {status === 'success' ? '성공' : '실패'}
        </Tag>
      ),
    },
    {
      title: '페이지',
      dataIndex: 'page_count',
      width: 80,
      align: 'center',
      sorter: (a, b) => a.page_count - b.page_count,
    },
    {
      title: 'OCR 저신뢰',
      dataIndex: 'has_flagged',
      width: 110,
      align: 'center',
      render: (flagged) =>
        flagged
          ? <Tag color="orange" icon={<WarningOutlined />}>검토 필요</Tag>
          : <Tag color="default">정상</Tag>,
    },
    {
      title: '업로드 시각',
      dataIndex: 'uploaded_at',
      width: 120,
      defaultSortOrder: 'descend',
      sorter: (a, b) => new Date(a.uploaded_at) - new Date(b.uploaded_at),
      render: (d) => (
        <Tooltip title={d}>
          <span style={{ cursor: 'default' }}>{formatDate(d)}</span>
        </Tooltip>
      ),
    },
    {
      title: '삭제',
      width: 80,
      align: 'center',
      render: (_, record) => (
        // Popconfirm: 실수로 삭제하지 않도록 "정말 삭제할까요?" 확인창을 띄웁니다
        <Popconfirm
          title="정말 삭제할까요?"
          okText="삭제"
          cancelText="취소"
          onConfirm={() => deleteMutation.mutate(record.id)}
        >
          <Button danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
    {
      title: '재시도',
      width: 80,
      align: 'center',
      render: (_, record) =>
        record.status === 'failed' ? (
          <Popconfirm
            title="파싱을 다시 시도할까요?"
            okText="재시도"
            cancelText="취소"
            onConfirm={() => retryMutation.mutate(record.id)}
          >
            <Button
              icon={<ReloadOutlined />}
              size="small"
              loading={retryMutation.isPending && retryMutation.variables === record.id}
            >
              재시도
            </Button>
          </Popconfirm>
        ) : null,
    },
    {
      title: '상세',
      width: 70,
      align: 'center',
      render: (_, record) => (
        <Button size="small" onClick={() => openDetail(record)}>보기</Button>
      ),
    },
  ]

  // ── 저신뢰 문서 테이블 컬럼 정의 ─────────────────────────────────────────
  const flaggedColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '파일명',
      dataIndex: 'filename',
      render: (name) => (
        <span><WarningOutlined style={{ color: 'orange', marginRight: 6 }} />{name}</span>
      ),
    },
    { title: '페이지 수', dataIndex: 'page_count', width: 100, align: 'center' },
    {
      title: '업로드 시각',
      dataIndex: 'uploaded_at',
      render: (d) => <Tooltip title={d}><span>{formatDate(d)}</span></Tooltip>,
    },
    {
      title: 'OCR 수정',
      width: 90,
      align: 'center',
      render: (_, record) => (
        <Button
          icon={<EditOutlined />}
          size="small"
          type="primary"
          ghost
          onClick={() => openOcrEdit(record)}
        >
          수정
        </Button>
      ),
    },
    {
      title: '삭제',
      width: 80,
      align: 'center',
      render: (_, record) => (
        <Popconfirm
          title="정말 삭제할까요?"
          okText="삭제"
          cancelText="취소"
          onConfirm={() => deleteMutation.mutate(record.id)}
        >
          <Button danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ]

  // 검색 로그 테이블 컬럼
  const historyColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '검색어', dataIndex: 'query', render: (q) => <Tag color="blue">{q}</Tag> },
    {
      title: '검색 방식',
      dataIndex: 'alpha',
      width: 160,
      render: (a) => (
        <span>의미 {Math.round(a * 100)}% · 키워드 {Math.round((1 - a) * 100)}%</span>
      ),
    },
    { title: '결과 수', dataIndex: 'result_count', width: 90, align: 'center' },
    {
      title: '검색 시각',
      dataIndex: 'searched_at',
      render: (d) => <Tooltip title={d}><span>{formatDate(d)}</span></Tooltip>,
    },
    {
      title: '재검색',
      width: 80,
      align: 'center',
      render: (_, record) => (
        <Button
          size="small"
          icon={<SearchOutlined />}
          onClick={() => {
            localStorage.setItem('km_launch_query', record.query)
            onNavigate?.('search')
          }}
        >
          검색
        </Button>
      ),
    },
  ]

  const CAT_LABEL = { spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서' }
  const CAT_COLOR = { spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green' }

  // 필터가 적용된 문서 목록 — tabs보다 반드시 먼저 선언해야 합니다
  const filteredDocuments = documents.filter(doc => {
    if (filterText     && !doc.filename.toLowerCase().includes(filterText.toLowerCase())) return false
    if (filterCategory && doc.category  !== filterCategory) return false
    if (filterStatus   && doc.status    !== filterStatus)   return false
    if (filterFileType && doc.file_type !== filterFileType) return false
    return true
  })

  // ── 탭 구성 (전체 문서 / OCR 검토 필요 / 검색 기록) ─────────────────────
  const tabs = [
    {
      key: 'all',
      label: `전체 문서 (${documents.length})`,
      children: (
        <>
          {/* 필터 바 */}
          <ACard size="small" style={{ marginBottom: 12 }}>
            <Space wrap>
              <Input
                placeholder="파일명 검색"
                prefix={<SearchOutlined style={{ color: '#aaa' }} />}
                value={filterText}
                onChange={e => setFilterText(e.target.value)}
                allowClear
                style={{ width: 220 }}
              />
              <Select
                placeholder="카테고리"
                value={filterCategory}
                onChange={setFilterCategory}
                allowClear
                style={{ width: 130 }}
                options={[
                  { value: 'spec',         label: '사양서' },
                  { value: 'research',     label: '연구자료' },
                  { value: 'presentation', label: '발표자료' },
                  { value: 'report',       label: '보고서' },
                ]}
              />
              <Select
                placeholder="상태"
                value={filterStatus}
                onChange={setFilterStatus}
                allowClear
                style={{ width: 100 }}
                options={[
                  { value: 'success', label: '성공' },
                  { value: 'failed',  label: '실패' },
                ]}
              />
              <Select
                placeholder="파일 형식"
                value={filterFileType}
                onChange={setFilterFileType}
                allowClear
                style={{ width: 120 }}
                options={['pdf','docx','pptx','xlsx','hwp','hwpx','txt','md','png','jpg'].map(ft => ({
                  value: ft, label: ft.toUpperCase(),
                }))}
              />
              {(filterText || filterCategory || filterStatus || filterFileType) && (
                <Button
                  size="small"
                  onClick={() => { setFilterText(''); setFilterCategory(null); setFilterStatus(null); setFilterFileType(null) }}
                >
                  필터 초기화
                </Button>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {filteredDocuments.length}/{documents.length}건
              </Typography.Text>
              <Button size="small" onClick={downloadCSV} disabled={filteredDocuments.length === 0}>
                CSV 내보내기
              </Button>
            </Space>
          </ACard>
          {selectedRowKeys.length > 0 && (
            <div style={{
              marginBottom: 8,
              padding: '8px 12px',
              background: '#e6f4ff',
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 8,
            }}>
              <Typography.Text strong style={{ color: '#1677ff' }}>
                {selectedRowKeys.length}개 선택됨
              </Typography.Text>

              {/* 카테고리 일괄 변경 */}
              <Select
                placeholder="카테고리 선택"
                value={bulkCategory}
                onChange={setBulkCategory}
                allowClear
                style={{ width: 130 }}
                options={[
                  { value: 'spec',         label: '사양서' },
                  { value: 'research',     label: '연구자료' },
                  { value: 'presentation', label: '발표자료' },
                  { value: 'report',       label: '보고서' },
                ]}
              />
              <Popconfirm
                title={`선택한 ${selectedRowKeys.length}개 문서의 카테고리를 "${
                  { spec:'사양서', research:'연구자료', presentation:'발표자료', report:'보고서' }[bulkCategory]
                }"(으)로 변경할까요?`}
                okText="변경"
                cancelText="취소"
                disabled={!bulkCategory}
                onConfirm={() => bulkCategoryMutation.mutate({ ids: selectedRowKeys, category: bulkCategory })}
              >
                <Button
                  disabled={!bulkCategory}
                  loading={bulkCategoryMutation.isPending}
                >
                  카테고리 일괄 변경
                </Button>
              </Popconfirm>

              <div style={{ width: 1, height: 20, background: '#d0e8ff', margin: '0 4px' }} />

              {/* 일괄 삭제 */}
              <Popconfirm
                title={`선택한 ${selectedRowKeys.length}개 문서를 모두 삭제할까요?`}
                okText="삭제"
                cancelText="취소"
                onConfirm={() => bulkDeleteMutation.mutate(selectedRowKeys)}
              >
                <Button danger icon={<DeleteOutlined />} loading={bulkDeleteMutation.isPending}>
                  선택 삭제
                </Button>
              </Popconfirm>

              <Button onClick={() => { setSelectedRowKeys([]); setBulkCategory(null) }}>
                선택 해제
              </Button>
            </div>
          )}
          <Table
            columns={docColumns}
            dataSource={filteredDocuments}
            rowKey="id"
            loading={loadingDocs}
            pagination={{ pageSize: 10 }}
            scroll={{ x: 960 }}
            rowSelection={{
              selectedRowKeys,
              onChange: setSelectedRowKeys,
            }}
          />
        </>
      ),
    },
    {
      key: 'flagged',
      label: (
        <span>
          <WarningOutlined style={{ color: 'orange' }} />
          OCR 검토 필요 ({flagged.length})
        </span>
      ),
      children: (
        <Table
          columns={flaggedColumns}
          dataSource={flagged}
          rowKey="id"
          loading={loadingFlagged}
          pagination={{ pageSize: 10 }}
        />
      ),
    },
    {
      key: 'history',
      label: (
        <span>
          <HistoryOutlined />
          검색 기록 ({history.length})
        </span>
      ),
      children: (
        <>
          {history.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <Popconfirm
                title="검색 기록을 모두 삭제할까요?"
                okText="초기화"
                cancelText="취소"
                onConfirm={() => clearHistoryMutation.mutate()}
              >
                <Button danger loading={clearHistoryMutation.isPending}>
                  전체 초기화
                </Button>
              </Popconfirm>
            </div>
          )}
          <Table
            columns={historyColumns}
            dataSource={history}
            rowKey="id"
            loading={loadingHistory}
            pagination={{ pageSize: 20 }}
          />
        </>
      ),
    },
  ]

  return (
    <div style={{ padding: 32, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0 }}>관리자 대시보드</Title>
        <Space>
          <Tooltip title="30초마다 자동으로 데이터를 갱신합니다">
            <Button
              icon={<SyncOutlined spin={autoRefresh} />}
              type={autoRefresh ? 'primary' : 'default'}
              onClick={() => setAutoRefresh(v => !v)}
            >
              {autoRefresh ? '자동 갱신 ON' : '자동 갱신'}
            </Button>
          </Tooltip>
          <Button icon={<SyncOutlined />} onClick={refreshAll}>새로고침</Button>
        </Space>
      </div>

      {/* ── 문서 0건 온보딩 안내 ────────────────────────────────── */}
      {stats && stats.total_documents === 0 && (
        <div style={{ textAlign: 'center', padding: '40px 0', marginBottom: 24 }}>
          <Empty description={
            <span>
              아직 업로드된 문서가 없습니다<br />
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                첫 문서를 올리면 여기에 통계가 표시됩니다
              </Typography.Text>
            </span>
          }>
            <Button type="primary" onClick={() => onNavigate?.('upload')}>
              문서 업로드 시작하기
            </Button>
          </Empty>
        </div>
      )}

      {/* ── 통계 카드 ───────────────────────────────────────────── */}
      {stats && stats.total_documents > 0 && (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            {[
              {
                title: '전체 문서',
                value: stats.total_documents,
                icon: <FileOutlined style={{ fontSize: 22, color: '#1677ff' }} />,
                accent: '#1677ff',
                bg: '#e6f4ff',
              },
              {
                title: 'OCR 검토 필요',
                value: stats.flagged_count,
                icon: <ExclamationCircleOutlined style={{ fontSize: 22, color: stats.flagged_count > 0 ? '#fa8c16' : '#52c41a' }} />,
                accent: stats.flagged_count > 0 ? '#fa8c16' : '#52c41a',
                bg: stats.flagged_count > 0 ? '#fff7e6' : '#f6ffed',
                valueStyle: { color: stats.flagged_count > 0 ? '#fa8c16' : '#52c41a' },
              },
              {
                title: '파싱 실패',
                value: stats.failed_count,
                icon: <WarningOutlined style={{ fontSize: 22, color: stats.failed_count > 0 ? '#ff4d4f' : '#52c41a' }} />,
                accent: stats.failed_count > 0 ? '#ff4d4f' : '#52c41a',
                bg: stats.failed_count > 0 ? '#fff1f0' : '#f6ffed',
                valueStyle: { color: stats.failed_count > 0 ? '#ff4d4f' : '#52c41a' },
              },
              {
                title: '총 검색 횟수',
                value: stats.total_searches,
                icon: <SearchOutlined style={{ fontSize: 22, color: '#722ed1' }} />,
                accent: '#722ed1',
                bg: '#f9f0ff',
              },
            ].map(({ title, value, icon, accent, bg, valueStyle }) => (
              <Col span={6} key={title}>
                <ACard
                  style={{
                    borderTop: `3px solid ${accent}`,
                    borderRadius: 8,
                    background: bg,
                  }}
                  bodyStyle={{ padding: '16px 20px' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Statistic
                      title={<span style={{ fontSize: 13, color: '#666' }}>{title}</span>}
                      value={value}
                      valueStyle={{ fontSize: 28, fontWeight: 700, ...(valueStyle ?? {}) }}
                    />
                    <div style={{
                      width: 48, height: 48,
                      borderRadius: '50%',
                      background: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                      flexShrink: 0,
                    }}>
                      {icon}
                    </div>
                  </div>
                </ACard>
              </Col>
            ))}
          </Row>

          {/* 카테고리별 분포 + 파일 형식 분포 + 인기 검색어 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <ACard title="카테고리별 문서 수" size="small">
                {Object.entries(stats.by_category).length === 0
                  ? <Typography.Text type="secondary">업로드된 문서 없음</Typography.Text>
                  : Object.entries(stats.by_category).map(([cat, cnt]) => {
                    const pct = stats.total_documents > 0
                      ? Math.round((cnt / stats.total_documents) * 100)
                      : 0
                    return (
                      <div key={cat} style={{ marginBottom: 10 }}>
                        <Row justify="space-between" style={{ marginBottom: 2 }}>
                          <Col>
                            <Tag color={CAT_COLOR[cat]} style={{ marginRight: 0 }}>
                              {CAT_LABEL[cat] ?? cat}
                            </Tag>
                          </Col>
                          <Col>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {cnt}건 ({pct}%)
                            </Typography.Text>
                          </Col>
                        </Row>
                        <Progress
                          percent={pct}
                          showInfo={false}
                          strokeColor={
                            { spec: '#1677ff', research: '#722ed1', presentation: '#13c2c2', report: '#52c41a' }[cat]
                          }
                          size="small"
                        />
                      </div>
                    )
                  })
                }
              </ACard>
            </Col>
            <Col span={8}>
              <ACard title="파일 형식별 문서 수" size="small">
                {!stats.by_file_type || Object.entries(stats.by_file_type).length === 0
                  ? <Typography.Text type="secondary">업로드된 문서 없음</Typography.Text>
                  : Object.entries(stats.by_file_type)
                      .sort((a, b) => b[1] - a[1])
                      .map(([ft, cnt]) => {
                        const pct = stats.total_documents > 0
                          ? Math.round((cnt / stats.total_documents) * 100) : 0
                        return (
                          <div key={ft} style={{ marginBottom: 8 }}>
                            <Row justify="space-between" style={{ marginBottom: 2 }}>
                              <Col>
                                <Tag color={FILE_TYPE_COLOR[ft] ?? 'default'} style={{ marginRight: 0 }}>
                                  {ft.toUpperCase()}
                                </Tag>
                              </Col>
                              <Col>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  {cnt}건
                                </Typography.Text>
                              </Col>
                            </Row>
                            <Progress
                              percent={pct}
                              showInfo={false}
                              size="small"
                              strokeColor={FILE_TYPE_COLOR[ft] === 'volcano' ? '#ff4d4f'
                                : FILE_TYPE_COLOR[ft] === 'geekblue' ? '#1677ff'
                                : FILE_TYPE_COLOR[ft] === 'orange'   ? '#fa8c16'
                                : FILE_TYPE_COLOR[ft] === 'green'    ? '#52c41a'
                                : FILE_TYPE_COLOR[ft] === 'purple'   ? '#722ed1'
                                : FILE_TYPE_COLOR[ft] === 'cyan'     ? '#13c2c2'
                                : FILE_TYPE_COLOR[ft] === 'magenta'  ? '#eb2f96'
                                : '#8c8c8c'}
                            />
                          </div>
                        )
                      })
                }
              </ACard>
            </Col>
            <Col span={8}>
              <ACard title="인기 검색어 Top 5" size="small">
                {stats.top_queries.length === 0
                  ? <Typography.Text type="secondary">검색 기록 없음</Typography.Text>
                  : (() => {
                    const maxCnt = Math.max(...stats.top_queries.map(q => q.count), 1)
                    const RANK_COLOR = ['#ff4d4f', '#fa8c16', '#fadb14', '#8c8c8c', '#8c8c8c']
                    return stats.top_queries.map((q, i) => (
                      <div key={i} style={{ marginBottom: i < stats.top_queries.length - 1 ? 10 : 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <span style={{
                            width: 18, height: 18, borderRadius: '50%',
                            background: RANK_COLOR[i],
                            color: i < 3 ? '#fff' : '#fff',
                            fontSize: 10, fontWeight: 700,
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            flexShrink: 0,
                          }}>{i + 1}</span>
                          <Typography.Text strong style={{ flex: 1, fontSize: 13 }} ellipsis>{q.query}</Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>{q.count}회</Typography.Text>
                        </div>
                        <div style={{ height: 4, borderRadius: 2, background: '#f0f0f0' }}>
                          <div style={{
                            height: '100%',
                            width: `${Math.round((q.count / maxCnt) * 100)}%`,
                            background: RANK_COLOR[i],
                            borderRadius: 2,
                            transition: 'width 0.4s ease',
                          }} />
                        </div>
                      </div>
                    ))
                  })()
                }
              </ACard>
            </Col>
          </Row>

          {/* 업로드 트렌드 */}
          {trend.length > 0 && (
            <ACard
              title={`최근 ${trendPeriod}일 업로드 현황`}
              size="small"
              style={{ marginBottom: 24 }}
              extra={
                <Radio.Group
                  size="small"
                  value={trendPeriod}
                  onChange={(e) => setTrendPeriod(e.target.value)}
                >
                  <Radio.Button value={7}>7일</Radio.Button>
                  <Radio.Button value={14}>14일</Radio.Button>
                  <Radio.Button value={30}>30일</Radio.Button>
                </Radio.Group>
              }
            >
              {(() => {
                const maxCount = Math.max(...trend.map(d => d.count), 1)
                const todayStr = trend[trend.length - 1]?.date
                const CHART_H  = 80
                const GRID_LINES = [0.25, 0.5, 0.75, 1.0]
                return (
                  <div style={{ position: 'relative' }}>
                    {/* 격자선 */}
                    {GRID_LINES.map(ratio => (
                      <div key={ratio} style={{
                        position: 'absolute',
                        left: 0, right: 0,
                        bottom: 24 + ratio * CHART_H,
                        borderTop: '1px dashed #f0f0f0',
                        pointerEvents: 'none',
                      }} />
                    ))}
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: trendPeriod > 7 ? 3 : 6, height: CHART_H + 24, paddingBottom: 24 }}>
                      {trend.map((d, idx) => {
                        const isToday = d.date === todayStr
                        const showLabel = isToday || trendPeriod <= 7 || idx % Math.ceil(trendPeriod / 7) === 0
                        const barH    = d.count > 0
                          ? Math.max((d.count / maxCount) * CHART_H, 8)
                          : 3
                        return (
                          <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                            {/* 건수 레이블 */}
                            <span style={{
                              fontSize: 11, fontWeight: 600,
                              color: d.count > 0 ? (isToday ? '#1677ff' : '#595959') : 'transparent',
                              minHeight: 16,
                            }}>
                              {d.count > 0 ? d.count : '0'}
                            </span>
                            {/* 바 */}
                            <Tooltip title={`${d.date}: ${d.count}건`}>
                              <div style={{
                                width: '100%',
                                height: barH,
                                background: d.count > 0
                                  ? (isToday
                                    ? 'linear-gradient(to top, #0958d9, #4096ff)'
                                    : 'linear-gradient(to top, #1677ff88, #1677ffcc)')
                                  : '#f0f0f0',
                                borderRadius: '4px 4px 0 0',
                                transition: 'height 0.4s ease',
                                cursor: 'default',
                                boxShadow: isToday && d.count > 0 ? '0 2px 8px #1677ff44' : 'none',
                              }} />
                            </Tooltip>
                            {/* 날짜 레이블 — 14일/30일 모드에서는 간격을 두고 표시 */}
                            <span style={{
                              fontSize: trendPeriod > 7 ? 10 : 11,
                              color: isToday ? '#1677ff' : '#8c8c8c',
                              fontWeight: isToday ? 600 : 400,
                              whiteSpace: 'nowrap',
                              visibility: showLabel ? 'visible' : 'hidden',
                            }}>
                              {isToday ? '오늘' : d.date}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })()}
            </ACard>
          )}
        </>
      )}

      <Tabs items={tabs} defaultActiveKey="all" />

      {/* ── OCR 수동 수정 모달 ──────────────────────────────────────── */}
      <Modal
        title={ocrDoc ? `OCR 수동 수정 — ${ocrDoc.filename}` : 'OCR 수동 수정'}
        open={ocrModal}
        onCancel={() => setOcrModal(false)}
        footer={null}
        width={680}
        destroyOnClose
      >
        {!ocrDoc && (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip="불러오는 중..." />
          </div>
        )}
        {ocrDoc && ocrDoc.pages.length === 0 && (
          <Alert type="success" message="모든 페이지가 이미 수정됐습니다." showIcon />
        )}
        {ocrDoc && ocrDoc.pages.map(page => (
          <ACard
            key={page.page_num}
            size="small"
            style={{ marginBottom: 16 }}
            title={
              <Space>
                <Tag color="orange" icon={<WarningOutlined />}>
                  {page.page_num}페이지 · OCR 저신뢰
                </Tag>
                {page.ocr_confidence != null && (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    신뢰도 {Math.round(page.ocr_confidence * 100)}%
                  </Typography.Text>
                )}
              </Space>
            }
            extra={
              <Button
                type="primary"
                size="small"
                loading={ocrSaving}
                onClick={() => saveOcrEdit(page.page_num)}
              >
                저장
              </Button>
            }
          >
            <Input.TextArea
              rows={6}
              value={ocrEdits[page.page_num] ?? page.text}
              onChange={(e) => setOcrEdits(prev => ({ ...prev, [page.page_num]: e.target.value }))}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          </ACard>
        ))}
      </Modal>

      {/* ── 문서 상세 보기 Drawer ───────────────────────────────────── */}
      <Drawer
        title={detailData ? `${detailData.filename} 상세 보기` : '문서 상세 보기'}
        placement="right"
        width={580}
        open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setMemoEdit(null); setTitleEdit(null) }}
        destroyOnClose
        extra={
          detailData && (
            <Button
              icon={<DownloadOutlined />}
              type="primary"
              href={`${API}/files/${detailData.id}`}
              download
            >
              파일 다운로드
            </Button>
          )
        }
      >
        {detailLoading && (
          <div style={{ textAlign: 'center', paddingTop: 60 }}>
            <Spin size="large" tip="불러오는 중..." />
          </div>
        )}

        {!detailLoading && detailData && (
          <>
            {/* 문서 제목 — 자동 추출 또는 수동 편집 */}
            <div style={{
              background: '#f0f5ff',
              border: '1px solid #adc6ff',
              borderRadius: 6,
              padding: '8px 12px',
              marginBottom: 12,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>문서 제목</Typography.Text>
                {titleEdit === null ? (
                  <Button size="small" icon={<EditOutlined />} type="text" onClick={() => setTitleEdit(detailData.title ?? '')}>
                    편집
                  </Button>
                ) : (
                  <Space size={4}>
                    <Button size="small" onClick={() => setTitleEdit(null)}>취소</Button>
                    <Button size="small" type="primary" loading={titleSaving} onClick={saveTitle}>저장</Button>
                  </Space>
                )}
              </div>
              {titleEdit === null ? (
                detailData.title
                  ? <Typography.Title level={5} style={{ margin: 0 }}>{detailData.title}</Typography.Title>
                  : <Typography.Text type="secondary" style={{ fontStyle: 'italic' }}>제목 없음 — 편집을 눌러 추가하세요</Typography.Text>
              ) : (
                <Input
                  value={titleEdit}
                  onChange={e => setTitleEdit(e.target.value)}
                  onPressEnter={saveTitle}
                  placeholder="문서 제목을 입력하세요"
                  autoFocus
                />
              )}
            </div>

            {/* 문서 기본 정보 */}
            <ACard size="small" style={{ marginBottom: 16 }}>
              <Row gutter={12}>
                <Col span={12}>
                  <Typography.Text type="secondary">카테고리</Typography.Text><br />
                  <Tag color={CAT_COLOR[detailData.category]}>{CAT_LABEL[detailData.category] ?? detailData.category}</Tag>
                </Col>
                <Col span={12}>
                  <Typography.Text type="secondary">상태</Typography.Text><br />
                  <Tag color={detailData.status === 'success' ? 'green' : 'red'}>
                    {detailData.status === 'success' ? '성공' : '실패'}
                  </Tag>
                </Col>
                <Col span={12} style={{ marginTop: 8 }}>
                  <Typography.Text type="secondary">총 페이지</Typography.Text><br />
                  <Typography.Text>{detailData.page_count}페이지</Typography.Text>
                </Col>
                <Col span={12} style={{ marginTop: 8 }}>
                  <Typography.Text type="secondary">파일 형식</Typography.Text><br />
                  {detailData.file_type
                    ? <Tag color={FILE_TYPE_COLOR[detailData.file_type] ?? 'default'}>{detailData.file_type.toUpperCase()}</Tag>
                    : <Typography.Text type="secondary">—</Typography.Text>
                  }
                </Col>
                <Col span={24} style={{ marginTop: 8 }}>
                  <Typography.Text type="secondary">업로드 시각</Typography.Text><br />
                  <Typography.Text>{detailData.uploaded_at}</Typography.Text>
                </Col>
                {detailData.original_path && (
                  <Col span={24} style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary">원본 경로</Typography.Text><br />
                    <Typography.Text code style={{ fontSize: 12 }}>{detailData.original_path}</Typography.Text>
                  </Col>
                )}
              {/* 메모 */}
              <Col span={24} style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <Typography.Text type="secondary">메모</Typography.Text>
                  {memoEdit === null ? (
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      type="text"
                      onClick={() => setMemoEdit(detailData.memo ?? '')}
                    >
                      편집
                    </Button>
                  ) : (
                    <Space size={4}>
                      <Button size="small" onClick={() => setMemoEdit(null)}>취소</Button>
                      <Button size="small" type="primary" loading={memoSaving} onClick={saveMemo}>저장</Button>
                    </Space>
                  )}
                </div>
                {memoEdit === null ? (
                  detailData.memo
                    ? <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{detailData.memo}</Typography.Text>
                    : <Typography.Text type="secondary" style={{ fontStyle: 'italic' }}>메모 없음 — 편집을 눌러 추가하세요</Typography.Text>
                ) : (
                  <Input.TextArea
                    rows={3}
                    value={memoEdit}
                    onChange={(e) => setMemoEdit(e.target.value)}
                    placeholder="이 문서에 대한 설명을 입력하세요 (신입이 검색 결과에서 볼 수 있습니다)"
                    autoFocus
                  />
                )}
              </Col>
              </Row>

              {detailData.has_flagged && (
                <Alert
                  type="warning"
                  showIcon
                  message="OCR 저신뢰 페이지가 포함된 문서입니다. 내용을 직접 확인하세요."
                  style={{ marginTop: 12 }}
                />
              )}
              {detailData.error && (
                <Alert type="error" showIcon message={detailData.error} style={{ marginTop: 12 }} />
              )}
            </ACard>

            {/* 페이지별 텍스트 */}
            {detailData.pages.length === 0 ? (
              <Alert type="info" message="저장된 파싱 데이터가 없습니다. 이 문서는 업그레이드 전에 업로드됐을 수 있습니다." />
            ) : (
              <Collapse
                accordion
                items={detailData.pages.map((page, idx) => {
                  const pageNum = page.page_num ?? page.page ?? (idx + 1)
                  const text    = page.text ?? ''
                  const flagged = page.flagged ?? false
                  return {
                    key: String(idx),
                    label: (
                      <span>
                        {pageNum}페이지
                        {flagged && (
                          <Tag color="orange" icon={<WarningOutlined />} style={{ marginLeft: 8 }}>
                            OCR 저신뢰
                          </Tag>
                        )}
                      </span>
                    ),
                    children: (
                      <Typography.Paragraph
                        style={{
                          whiteSpace: 'pre-wrap',
                          fontSize: 13,
                          maxHeight: 360,
                          overflowY: 'auto',
                          background: '#fafafa',
                          padding: 12,
                          borderRadius: 6,
                          margin: 0,
                        }}
                      >
                        {text || <Typography.Text type="secondary">(텍스트 없음)</Typography.Text>}
                      </Typography.Paragraph>
                    ),
                  }
                })}
              />
            )}
          </>
        )}
      </Drawer>
    </div>
  )
}
