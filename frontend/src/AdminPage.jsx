// AdminPage.jsx — 관리자 화면
// 2026-07-11 목업 반영: 상단 Tabs → 좌측 서브메뉴(문서 목록·OCR 검토·검색 기록·카테고리 관리·대표 문서 지정)
// + 우측 요약 통계 패널(전체 문서 수·실패 문서·OCR 검토 대기·최근 색인 완료·환경) 3단 레이아웃으로 재구성.
// App.jsx가 이제 관리자를 최상위 탭으로 렌더링하므로(Drawer 아님), 이 파일은 독립 페이지로 동작한다.
// 기존 기능(문서 CRUD·OCR 수정·CSV·일괄작업·트렌드 차트·검색기록)은 전부 유지 — 좌측 메뉴 아래로 재배치만 함.
// "대표 문서 지정"은 목업에는 있으나 백엔드에 버전관리/대표문서 기능이 없어 플레이스홀더로만 표시.

import { useState, useEffect, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Table, Button, Tag, message, Popconfirm, Typography, Row, Col,
  Card as ACard, List, Drawer, Collapse, Spin, Alert, Input, Space, Select,
  Progress, Tooltip, Modal, Radio, Empty, Menu, AutoComplete, Popover,
} from 'antd'
import {
  DeleteOutlined, WarningOutlined, FileTextOutlined,
  SearchOutlined, ReloadOutlined, SyncOutlined, EditOutlined,
  DownloadOutlined, FolderOutlined, UnorderedListOutlined, AppstoreOutlined,
  EyeOutlined, UserOutlined, TeamOutlined, SwapOutlined,
} from '@ant-design/icons'
import axios from 'axios'
import useIsNarrow from './useIsNarrow'

const { Title, Text } = Typography

const API = import.meta.env.VITE_API_URL

// ── API 호출 함수들 ───────────────────────────────────────────────────────────
const fetchDocuments = () => axios.get(`${API}/admin/documents`).then(r => r.data)
const fetchStats     = () => axios.get(`${API}/admin/stats`).then(r => r.data)
const fetchTrend     = (period = 7) => axios.get(`${API}/admin/stats/trend`, { params: { period } }).then(r => r.data)
const deleteDocument     = ({ id, reason }) => axios.delete(`${API}/admin/documents/${id}`, { data: { reason } })
const fetchDeletionLogs  = () => axios.get(`${API}/admin/deletion-logs`).then(r => r.data)

const CAT_LABEL = { spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서' }
const CAT_COLOR = { spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green' }

// 카테고리 랜딩(태그 브라우징) 카드 전용 — 체크형 태그의 선택 상태 배경/글자색
const CAT_ACCENT_HEX = { spec: '#4C6FFF', research: '#8456DB', presentation: '#0EA5B0', report: '#1E9E5A' }
const CAT_SOFT_BG    = { spec: '#EEF1FF', research: '#F3EEFC', presentation: '#E6F8F9', report: '#E9F9EF' }
const FILE_TYPE_COLOR = {
  pdf: 'volcano', docx: 'geekblue', pptx: 'orange', ppt: 'orange',
  xlsx: 'green', xls: 'green', hwp: 'purple', hwpx: 'purple',
  txt: 'default', md: 'cyan', png: 'magenta', jpg: 'magenta', jpeg: 'magenta',
}

export default function AdminPage({ onNavigate }) {
  const isNarrow = useIsNarrow()
  const queryClient = useQueryClient()

  const [navKey, setNavKey] = useState('docs')  // docs | ocr | history | category | admins

  // 좌측 서브메뉴가 상단 헤더(AdminRoute.jsx의 #admin-nav-slot)로 이동 — 마운트된 뒤에야
  // 그 DOM 노드가 존재하므로 useEffect에서 한 번 찾아 포털 대상으로 저장한다.
  const [navSlot, setNavSlot] = useState(null)
  useEffect(() => {
    setNavSlot(document.getElementById('admin-nav-slot'))
  }, [])

  // 문서 상세 보기 Drawer 상태
  const [drawerOpen,    setDrawerOpen]    = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailData,    setDetailData]    = useState(null)

  const [memoEdit,    setMemoEdit]    = useState(null)
  const [memoSaving,  setMemoSaving]  = useState(false)
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
  const [ocrModal,  setOcrModal]  = useState(false)
  const [ocrDoc,    setOcrDoc]    = useState(null)
  const [ocrEdits,  setOcrEdits]  = useState({})
  const [ocrSaving, setOcrSaving] = useState(false)

  // 관리자 문서 목록 필터 상태
  const [filterText,     setFilterText]     = useState('')
  const [filterCategory, setFilterCategory] = useState(null)
  const [filterStatus,   setFilterStatus]   = useState(null)
  const [filterFileType, setFilterFileType] = useState(null)
  const [catSearch,       setCatSearch]      = useState('')  // "카테고리로 찾아보기" 카드 전용 제목 검색
  const [catViewMode,     setCatViewMode]    = useState('list')  // 카테고리 탭 뷰어: 'grid'(카드) | 'list'(목록)

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

  const [autoRefresh, setAutoRefresh] = useState(false)
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(() => queryClient.invalidateQueries(), 30_000)
    return () => clearInterval(id)
  }, [autoRefresh, queryClient])

  const refreshAll = () => {
    queryClient.invalidateQueries()
    message.success('새로고침 완료')
  }

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

  const saveOcrEdit = async (pageNum) => {
    if (!ocrDoc) return
    setOcrSaving(true)
    try {
      await axios.patch(`${API}/admin/documents/${ocrDoc.id}/pages/${pageNum}`, {
        text: ocrEdits[pageNum] ?? '',
      })
      message.success(`${pageNum}페이지 저장 완료`)
      setOcrDoc(prev => ({ ...prev, pages: prev.pages.filter(p => p.page_num !== pageNum) }))
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    } catch {
      message.error('저장 중 오류가 발생했습니다')
    } finally {
      setOcrSaving(false)
    }
  }

  const downloadCSV = () => {
    const header = ['ID', '파일명', '파일형식', '원본경로', '카테고리', '상태', '페이지수', 'OCR저신뢰', '업로드시각']
    const rows = filteredDocuments.map(d => [
      d.id, `"${d.filename}"`, d.file_type ?? '', `"${d.original_path ?? ''}"`,
      d.category ? (CAT_LABEL[d.category] ?? d.category) : '미지정', d.status === 'success' ? '성공' : '실패',
      d.page_count, d.has_flagged ? '검토필요' : '정상', `"${d.uploaded_at}"`,
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

  const [permissions,  setPermissions]  = useState([])   // 이 문서의 읽기 권한 화이트리스트(비어있으면 전체 공개)
  const [newPermEmail, setNewPermEmail] = useState('')
  const [permSaving,   setPermSaving]   = useState(false)

  const loadPermissions = async (docId) => {
    try {
      const res = await axios.get(`${API}/admin/documents/${docId}/permissions`)
      setPermissions(res.data)
    } catch {
      setPermissions([])
    }
  }

  const addPermission = async () => {
    const email = newPermEmail.trim().toLowerCase()
    if (!email || !detailData) return
    setPermSaving(true)
    try {
      await axios.post(`${API}/admin/documents/${detailData.id}/permissions`, { email })
      setNewPermEmail('')
      await loadPermissions(detailData.id)
    } catch {
      message.error('권한 추가에 실패했습니다')
    } finally {
      setPermSaving(false)
    }
  }

  const removePermission = async (email) => {
    if (!detailData) return
    try {
      await axios.delete(`${API}/admin/documents/${detailData.id}/permissions`, { params: { email } })
      setPermissions(prev => prev.filter(p => p.email !== email))
    } catch {
      message.error('권한 해제에 실패했습니다')
    }
  }

  const openDetail = async (doc) => {
    setDrawerOpen(true)
    setDetailLoading(true)
    setDetailData(null)
    setPermissions([])
    setNewPermEmail('')
    try {
      const res = await axios.get(`${API}/admin/documents/${doc.id}/detail`)
      setDetailData(res.data)
      loadPermissions(doc.id)
    } catch {
      message.error('상세 데이터를 불러오지 못했습니다')
      setDrawerOpen(false)
    } finally {
      setDetailLoading(false)
    }
  }

  const { data: documents = [], isLoading: loadingDocs } = useQuery({ queryKey: ['documents'], queryFn: fetchDocuments })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: fetchStats })

  // 관리자 계정 관리 — .env 고정 관리자(env_admins, 삭제 불가) + 웹 화면에서 추가한 관리자(extra_admins)
  const { data: adminEmails = { env_admins: [], extra_admins: [] }, isLoading: loadingAdmins } = useQuery({
    queryKey: ['admin-emails'],
    queryFn: () => axios.get(`${API}/admin/admin-emails`).then(r => r.data),
  })
  const [newAdminEmail, setNewAdminEmail] = useState('')
  const addAdminMutation = useMutation({
    mutationFn: (email) => axios.post(`${API}/admin/admin-emails`, { email }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-emails'] })
      setNewAdminEmail('')
      message.success('관리자로 추가됐습니다')
    },
    onError: (e) => message.error(e?.response?.data?.detail || '추가 중 오류가 발생했습니다'),
  })
  const removeAdminMutation = useMutation({
    mutationFn: (email) => axios.delete(`${API}/admin/admin-emails/${encodeURIComponent(email)}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-emails'] })
      message.success('관리자에서 제외됐습니다')
    },
    onError: (e) => message.error(e?.response?.data?.detail || '제외 중 오류가 발생했습니다'),
  })

  // 부서 관리 — 사용자별 부서 지정(문서 부서별 열람 제한의 기준값이 됨)
  const { data: users = [], isLoading: loadingUsers } = useQuery({
    queryKey: ['users'],
    queryFn: () => axios.get(`${API}/admin/users`).then(r => r.data),
  })
  const [deptDraft, setDeptDraft] = useState({})  // email -> 입력 중인 부서명(저장 전)
  const departmentMutation = useMutation({
    mutationFn: ({ email, department }) => axios.patch(`${API}/admin/users/${encodeURIComponent(email)}/department`, { department }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      message.success('부서가 저장됐습니다')
    },
    onError: () => message.error('부서 저장 중 오류가 발생했습니다'),
  })
  const removeUserMutation = useMutation({
    mutationFn: (email) => axios.delete(`${API}/admin/users/${encodeURIComponent(email)}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      message.success('내보냈습니다')
    },
    onError: (e) => message.error(e?.response?.data?.detail || '내보내기 중 오류가 발생했습니다'),
  })
  const knownDepartments = useMemo(
    () => Array.from(new Set(users.map(u => u.department).filter(Boolean))),
    [users]
  )

  // 카테고리 이름 변경 — 그 카테고리를 쓰는 문서 전부를 새 이름으로 일괄 재분류(기존 bulk-category 재사용)
  const [renamingCategory, setRenamingCategory] = useState(null)  // 이름 바꾸는 중인 카테고리(원래 이름)
  const [renameValue,      setRenameValue]      = useState('')
  const renameCategory = () => {
    const next = renameValue.trim().slice(0, 30)
    if (!next || next === renamingCategory) { setRenamingCategory(null); return }
    const ids = documents.filter(d => d.category === renamingCategory).map(d => d.id)
    if (ids.length > 0) bulkCategoryMutation.mutate({ ids, category: next })
    setRenamingCategory(null)
  }

  // 카테고리 삭제 전 문서 이동 — [2026-07-24] 예전엔 카테고리를 삭제하면 그 안의 문서를
  // 전부 자동으로 "보고서"로 재분류해버렸다(관리자가 고를 수 없었음). 이제는 삭제 자체를
  // 문서가 0개인 카테고리에서만 허용하고, 그 전에 관리자가 원하는 카테고리로 직접
  // 옮길 수 있게 이 "이동" 기능을 추가했다.
  const [movingCategory, setMovingCategory] = useState(null)  // 이동 대상으로 고른 원본 카테고리
  const [moveTarget,     setMoveTarget]     = useState(null)  // 옮겨갈 카테고리
  const moveCategory = () => {
    const ids = documents.filter(d => d.category === movingCategory).map(d => d.id)
    if (ids.length > 0 && moveTarget) bulkCategoryMutation.mutate({ ids, category: moveTarget })
    setMovingCategory(null); setMoveTarget(null)
  }

  // 고정 5종 + 실제로 문서에 쓰인(업로드 화면에서 클라이언트가 만든 것 포함) 카테고리 전부
  const allCategoryOptions = useMemo(() => {
    const fixed = Object.keys(CAT_LABEL)
    const observed = new Set([
      ...Object.keys(stats?.by_category ?? {}),
      ...documents.map(d => d.category).filter(Boolean),
    ])
    const extra = [...observed].filter(c => !fixed.includes(c))
    return [...fixed, ...extra].map(value => ({ value, label: CAT_LABEL[value] ?? value }))
  }, [stats, documents])

  const [trendPeriod, setTrendPeriod] = useState(7)
  const { data: trend = [] } = useQuery({ queryKey: ['trend', trendPeriod], queryFn: () => fetchTrend(trendPeriod) })

  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [bulkCategory,    setBulkCategory]    = useState(null)

  const bulkCategoryMutation = useMutation({
    mutationFn: ({ ids, category }) => axios.patch(`${API}/admin/documents/bulk-category`, { ids, category }),
    onSuccess: (_, { ids }) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      setSelectedRowKeys([]); setBulkCategory(null)
      message.success(`${ids.length}개 문서의 카테고리가 변경됐습니다`)
    },
    onError: () => message.error('카테고리 변경 중 오류가 발생했습니다'),
  })

  // 커스텀 카테고리 삭제 — [2026-07-24] 예전엔 문서가 남아있어도 전부 "보고서"로
  // 자동 재분류하며 지웠다. 이제는 문서가 0개인 카테고리만 삭제 대상이다(버튼도 그 경우에만
  // 활성화됨 — 아래는 방어적으로 한 번 더 확인). 카테고리 자체가 별도 테이블이 아니라
  // 문서의 category 문자열로만 존재하므로, 문서가 0개면 서버에 지울 대상이 없다 —
  // 목록에도 다음 새로고침 때 자동으로 안 보이게 된다.
  const deleteCategory = (cat) => {
    const ids = documents.filter(d => d.category === cat).map(d => d.id)
    if (ids.length > 0) return
    queryClient.invalidateQueries({ queryKey: ['documents'] })
    queryClient.invalidateQueries({ queryKey: ['stats'] })
    message.success(`"${CAT_LABEL[cat] ?? cat}" 카테고리가 삭제됐습니다`)
  }

  const bulkDeleteMutation = useMutation({
    mutationFn: ({ ids, reason }) => axios.delete(`${API}/admin/documents/bulk`, { data: { ids, reason } }),
    onSuccess: (_, { ids }) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['deletion-logs'] })
      setSelectedRowKeys([])
      message.success(`${ids.length}개 문서가 삭제됐습니다`)
    },
    onError: () => message.error('일괄 삭제 중 오류가 발생했습니다'),
  })

  // 삭제 사유 입력 모달 — [2026-07-24] 관리자가 문서를 삭제할 때 누가/왜 지웠는지 감사 로그를
  // 남기기 위해 사유 입력을 필수로 만들었다. 단건/일괄 삭제 모두 이 하나의 모달을 공유한다.
  const [deleteTarget, setDeleteTarget] = useState(null)  // { type: 'single', id } | { type: 'bulk', ids }
  const [deleteReason, setDeleteReason] = useState('')
  const confirmDelete = () => {
    const reason = deleteReason.trim()
    if (!reason || !deleteTarget) return
    if (deleteTarget.type === 'single') deleteMutation.mutate({ id: deleteTarget.id, reason })
    else bulkDeleteMutation.mutate({ ids: deleteTarget.ids, reason })
    setDeleteTarget(null); setDeleteReason('')
  }

  const categoryMutation = useMutation({
    mutationFn: ({ id, category }) => axios.patch(`${API}/admin/documents/${id}/category`, { category }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      message.success('카테고리가 변경됐습니다')
    },
    onError: () => message.error('카테고리 변경 중 오류가 발생했습니다'),
  })


  const retryMutation = useMutation({
    mutationFn: (id) => axios.post(`${API}/admin/documents/${id}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      message.success('재시도가 완료됐습니다')
    },
    onError: (err) => message.error(err.response?.data?.detail ?? '재시도 중 오류가 발생했습니다'),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['deletion-logs'] })
      message.success('문서가 삭제됐습니다')
    },
    onError: () => message.error('삭제 중 오류가 발생했습니다'),
  })

  const nowrapHeader = () => ({ style: { whiteSpace: 'nowrap' } })

  const docColumns = [
    { title: 'ID', dataIndex: 'id', width: 46, onHeaderCell: nowrapHeader },
    {
      title: '파일명 / 문서 제목',
      dataIndex: 'filename',
      sorter: (a, b) => a.filename.localeCompare(b.filename),
      render: (name, record) => (
        <span style={{ cursor: 'pointer' }} onClick={() => openDetail(record)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <FileTextOutlined style={{ color: '#1677ff', flexShrink: 0 }} />
            <Tooltip title={name}>
              <Text style={{ color: '#1677ff', maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'bottom' }}>
                {name}
              </Text>
            </Tooltip>
            {record.file_type && <Tag color={FILE_TYPE_COLOR[record.file_type] ?? 'default'} style={{ fontSize: 11, margin: 0 }}>{record.file_type.toUpperCase()}</Tag>}
          </div>
          {record.original_path && (
            <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 1 }} title={record.original_path}>{record.original_path}</Text>
          )}
        </span>
      ),
    },
    {
      title: '카테고리', dataIndex: 'category', width: 110, onHeaderCell: nowrapHeader,
      render: (cat, record) => (
        <Select
          value={cat || undefined} placeholder="미지정" size="small" style={{ width: 92 }}
          onChange={(val) => categoryMutation.mutate({ id: record.id, category: val })}
          options={allCategoryOptions}
        />
      ),
    },
    {
      title: '상태', dataIndex: 'status', width: 68, onHeaderCell: nowrapHeader,
      render: (status, record) => {
        const tag = <Tag color={status === 'success' ? 'green' : 'red'}>{status === 'success' ? '성공' : '실패'}</Tag>
        return status === 'failed' && record.error ? <Tooltip title={record.error}>{tag}</Tooltip> : tag
      },
    },
    { title: '페이지', dataIndex: 'page_count', width: 60, align: 'center', onHeaderCell: nowrapHeader, sorter: (a, b) => a.page_count - b.page_count },
    {
      title: '조회수', dataIndex: 'view_count', width: 64, align: 'center', onHeaderCell: nowrapHeader,
      sorter: (a, b) => (a.view_count ?? 0) - (b.view_count ?? 0),
      render: (v) => <Text type="secondary"><EyeOutlined style={{ marginRight: 4 }} />{v ?? 0}</Text>,
    },
    {
      title: '담당자', dataIndex: 'uploaded_by', width: 100, onHeaderCell: nowrapHeader,
      render: (v, record) => v && v !== 'anonymous'
        ? <Tooltip title={v}><Text ellipsis style={{ maxWidth: 80, display: 'inline-block', verticalAlign: 'bottom' }}><UserOutlined style={{ marginRight: 4 }} />{record.uploaded_by_name ?? v}</Text></Tooltip>
        : <Text type="secondary">—</Text>,
    },
    {
      title: 'OCR', dataIndex: 'has_flagged', width: 60, align: 'center', onHeaderCell: nowrapHeader,
      render: (f, record) => f
        ? <Tooltip title="검토 필요 — 클릭해서 저신뢰 페이지 수정"><Tag color="orange" icon={<WarningOutlined />} style={{ cursor: 'pointer' }} onClick={() => openOcrEdit(record)}>검토</Tag></Tooltip>
        : <Tag color="default">정상</Tag>,
    },
    {
      title: '업로드', dataIndex: 'uploaded_at', width: 100, defaultSortOrder: 'descend',
      sorter: (a, b) => new Date(a.uploaded_at) - new Date(b.uploaded_at),
      render: (d) => <Tooltip title={d}><span style={{ whiteSpace: 'nowrap' }}>{formatDate(d)}</span></Tooltip>,
      onHeaderCell: () => ({ style: { whiteSpace: 'nowrap' } }),
    },
    {
      title: '관리', width: 110, align: 'center', onHeaderCell: nowrapHeader,
      render: (_, record) => (
        <Space size={4}>
          {record.status === 'failed' && (
            <Tooltip title="재시도">
              <Popconfirm title="파싱을 다시 시도할까요?" okText="재시도" cancelText="취소" onConfirm={() => retryMutation.mutate(record.id)}>
                <Button icon={<ReloadOutlined />} size="small" loading={retryMutation.isPending && retryMutation.variables === record.id} />
              </Popconfirm>
            </Tooltip>
          )}
          <Tooltip title="상세보기">
            <Button icon={<EyeOutlined />} size="small" onClick={() => openDetail(record)} />
          </Tooltip>
          <Tooltip title="삭제">
            <Button danger icon={<DeleteOutlined />} size="small" onClick={() => { setDeleteTarget({ type: 'single', id: record.id }); setDeleteReason('') }} />
          </Tooltip>
        </Space>
      ),
    },
  ]

  // 확인 필요 문서 탭: 파싱 자체가 실패(status === 'failed')한 문서 + OCR 저신뢰 페이지가
  // 있어 검토가 필요한 문서(has_flagged)를 함께 모아 보여준다. 같은 docColumns를 재사용해
  // 문서 목록 탭과 동일한 조작(재시도·상세보기·삭제, OCR 열 클릭으로 저신뢰 페이지 수정)을 그대로 쓴다.
  const needsReviewDocuments = documents.filter(doc => doc.status === 'failed' || doc.has_flagged)

  const filteredDocuments = documents.filter(doc => {
    if (filterText     && !doc.filename.toLowerCase().includes(filterText.toLowerCase())) return false
    if (filterCategory && doc.category  !== filterCategory) return false
    if (filterStatus   && doc.status    !== filterStatus)   return false
    if (filterFileType && doc.file_type !== filterFileType) return false
    return true
  })

  const leftMenuItems = [
    { key: 'docs',           icon: <UnorderedListOutlined />, label: '문서 목록' },
    { key: 'failed',         icon: <WarningOutlined />,        label: `확인 필요 문서 (${needsReviewDocuments.length})` },
    { key: 'category',       icon: <FolderOutlined />,        label: '카테고리' },
    { key: 'departments',    icon: <TeamOutlined />,          label: '부서 관리' },
    { key: 'admins',         icon: <UserOutlined />,          label: '관리자 계정' },
    { key: 'deletion-log',   icon: <DeleteOutlined />,        label: '삭제 보고서' },
  ]

  const NAV_TITLE = {
    docs: '문서 목록', failed: '확인 필요 문서',
    category: '카테고리', departments: '부서 관리', admins: '관리자 계정',
    'deletion-log': '삭제 보고서',
  }

  // 삭제 보고서 — 관리자가 문서를 삭제할 때 남긴 사유·삭제자·시각 기록. 탭을 열 때만 조회한다.
  const { data: deletionLogs = [], isLoading: loadingDeletionLogs } = useQuery({
    queryKey: ['deletion-logs'],
    queryFn: fetchDeletionLogs,
    enabled: navKey === 'deletion-log',
  })

  const deletionLogColumns = [
    { title: '파일명',   dataIndex: 'filename',   key: 'filename', ellipsis: true },
    { title: '카테고리', dataIndex: 'category',   key: 'category', width: 110, render: (c) => c ? (CAT_LABEL[c] ?? c) : '-' },
    { title: '삭제한 관리자', dataIndex: 'deleted_by', key: 'deleted_by', width: 200 },
    { title: '사유', dataIndex: 'reason', key: 'reason' },
    { title: '삭제 시각', dataIndex: 'deleted_at', key: 'deleted_at', width: 170, render: (t) => (t || '').slice(0, 16) },
  ]

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: '24px 24px 40px' }}>
      {/* 좌측 서브메뉴였던 것을 상단 헤더 탭으로 이동 — 실제 DOM은 AdminRoute.jsx의
          #admin-nav-slot(로고~메인으로 버튼 사이)에 포털로 렌더링된다 */}
      {navSlot && createPortal(
        <Menu
          mode="horizontal"
          selectedKeys={[navKey]}
          onClick={(e) => setNavKey(e.key)}
          items={leftMenuItems}
          disabledOverflow={!isNarrow}
          style={{ border: 'none', lineHeight: '62px', whiteSpace: isNarrow ? 'normal' : 'nowrap', maxWidth: '100%' }}
        />,
        navSlot
      )}
      <Row gutter={20} wrap={isNarrow}>
        {/* ── 중앙 콘텐츠 ───────────────────────────────────────── */}
        {/* minWidth: 0 — flex 아이템의 기본 최소폭(content의 min-content 크기)을 해제.
            안 넣으면 넓은 테이블(문서 목록) 때문에 이 Col이 줄어들지 못하고 3열 Row 전체가
            줄바꿈되어(사이드바 아래로 콘텐츠가 통째로 떨어짐) 표시됨. */}
        <Col flex="auto" style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22, flexWrap: 'wrap', gap: 16 }}>
            <Title level={3} style={{ margin: 0 }}>관리자 · {NAV_TITLE[navKey]}</Title>

            <Space>
              <Tooltip title="30초마다 자동으로 데이터를 갱신합니다">
                <Button icon={<SyncOutlined spin={autoRefresh} />} type={autoRefresh ? 'primary' : 'default'} onClick={() => setAutoRefresh(v => !v)}>
                  {autoRefresh ? '자동 갱신 ON' : '자동 갱신'}
                </Button>
              </Tooltip>
              <Button icon={<SyncOutlined />} onClick={refreshAll}>새로고침</Button>
            </Space>
          </div>

          {stats && stats.total_documents === 0 && (
            <div style={{ textAlign: 'center', padding: '40px 0' }}>
              <Empty description={<span>아직 업로드된 문서가 없습니다<br /><Text type="secondary" style={{ fontSize: 13 }}>첫 문서를 올리면 여기에 통계가 표시됩니다</Text></span>}>
                <Button type="primary" onClick={() => onNavigate?.('upload')}>문서 업로드 시작하기</Button>
              </Empty>
            </div>
          )}

          {navKey === 'docs' && (
            <>
              <ACard size="small" style={{ marginBottom: 18, padding: '4px 0' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
                  <Space wrap size={16}>
                    <Input placeholder="파일명 검색" prefix={<SearchOutlined style={{ color: '#aaa' }} />} value={filterText} onChange={e => setFilterText(e.target.value)} allowClear style={{ width: isNarrow ? '100%' : 300 }} />
                    <Select placeholder="카테고리" value={filterCategory} onChange={setFilterCategory} allowClear style={{ width: isNarrow ? '100%' : 170 }} options={allCategoryOptions} />
                    <Select placeholder="상태" value={filterStatus} onChange={setFilterStatus} allowClear style={{ width: isNarrow ? '100%' : 140 }} options={[{ value: 'success', label: '성공' }, { value: 'failed', label: '실패' }]} />
                    <Select placeholder="파일 형식" value={filterFileType} onChange={setFilterFileType} allowClear style={{ width: isNarrow ? '100%' : 160 }} options={['pdf','docx','pptx','xlsx','hwp','hwpx','txt','md','png','jpg'].map(ft => ({ value: ft, label: ft.toUpperCase() }))} />
                    {(filterText || filterCategory || filterStatus || filterFileType) && (
                      <Button onClick={() => { setFilterText(''); setFilterCategory(null); setFilterStatus(null); setFilterFileType(null) }}>필터 초기화</Button>
                    )}
                  </Space>
                  <Space size={16}>
                    <Text type="secondary" style={{ fontSize: 12 }}>{filteredDocuments.length}/{documents.length}건</Text>
                    <Button onClick={downloadCSV} disabled={filteredDocuments.length === 0}>CSV 내보내기</Button>
                  </Space>
                </div>
              </ACard>

              {selectedRowKeys.length > 0 && (
                <div style={{ marginBottom: 16, padding: '10px 14px', background: '#e6f4ff', borderRadius: 6, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
                  <Text strong style={{ color: '#1677ff' }}>{selectedRowKeys.length}개 선택됨</Text>
                  <Select placeholder="카테고리 선택" value={bulkCategory} onChange={setBulkCategory} allowClear style={{ width: 130 }} options={allCategoryOptions} />
                  <Popconfirm
                    title={`선택한 ${selectedRowKeys.length}개 문서의 카테고리를 "${CAT_LABEL[bulkCategory]}"(으)로 변경할까요?`}
                    okText="변경" cancelText="취소" disabled={!bulkCategory}
                    onConfirm={() => bulkCategoryMutation.mutate({ ids: selectedRowKeys, category: bulkCategory })}
                  >
                    <Button disabled={!bulkCategory} loading={bulkCategoryMutation.isPending}>카테고리 일괄 변경</Button>
                  </Popconfirm>
                  <div style={{ width: 1, height: 20, background: '#d0e8ff', margin: '0 4px' }} />
                  <Button danger icon={<DeleteOutlined />} loading={bulkDeleteMutation.isPending} onClick={() => { setDeleteTarget({ type: 'bulk', ids: selectedRowKeys }); setDeleteReason('') }}>선택 삭제</Button>
                  <Button onClick={() => { setSelectedRowKeys([]); setBulkCategory(null) }}>선택 해제</Button>
                </div>
              )}

              <Table
                columns={docColumns} dataSource={filteredDocuments} rowKey="id" loading={loadingDocs}
                pagination={{ pageSize: 10 }}
                rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
                scroll={isNarrow ? { x: 'max-content' } : undefined}
              />
            </>
          )}

          {navKey === 'failed' && (
            <>
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="확인이 필요한 문서만 모아둔 목록입니다"
                description="파싱에 실패했거나(상태에 마우스를 올리면 실패 사유가 보입니다), OCR 저신뢰 페이지가 있어 검토가 필요한 문서(OCR 열의 '검토' 태그)가 표시됩니다. 실패 문서는 '재시도' 버튼으로 다시 파싱하거나, 필요 없으면 삭제할 수 있습니다."
              />
              <Table columns={docColumns} dataSource={needsReviewDocuments} rowKey="id" loading={loadingDocs} pagination={{ pageSize: 10 }} />
            </>
          )}

          {navKey === 'deletion-log' && (
            <>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="관리자가 삭제한 문서의 감사 기록입니다"
                description="누가/언제/무엇을/왜 삭제했는지 확인할 수 있습니다. 이 기록은 3개월이 지나면 자동으로 삭제됩니다."
              />
              <Table columns={deletionLogColumns} dataSource={deletionLogs} rowKey="id" loading={loadingDeletionLogs} pagination={{ pageSize: 20 }} />
            </>
          )}

          {navKey === 'category' && stats && (
            <>
              <Row gutter={[20, 20]} style={{ marginBottom: 20 }}>
                <Col span={24}>
                  <ACard
                    title="카테고리"
                    size="small"
                    extra={
                      <Space size={10}>
                        <Input
                          size="small"
                          allowClear
                          placeholder="문서 제목으로 검색…"
                          prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
                          value={catSearch}
                          onChange={(e) => setCatSearch(e.target.value)}
                          style={{ width: 220 }}
                        />
                        <Radio.Group size="small" value={catViewMode} onChange={(e) => setCatViewMode(e.target.value)} optionType="button">
                          <Tooltip title="목록형">
                            <Radio.Button value="list"><UnorderedListOutlined /></Radio.Button>
                          </Tooltip>
                          <Tooltip title="카드형">
                            <Radio.Button value="grid"><AppstoreOutlined /></Radio.Button>
                          </Tooltip>
                        </Radio.Group>
                      </Space>
                    }
                  >
                    <div style={{ marginBottom: 22 }}>
                      <Tag.CheckableTag checked={!filterCategory} onChange={() => setFilterCategory(null)}>
                        전체 {documents.length}
                      </Tag.CheckableTag>
                      {allCategoryOptions.map(({ value: cat, label }) => (
                        <span key={cat} style={{ display: 'inline-flex', alignItems: 'center', marginRight: 4 }}>
                          {renamingCategory === cat ? (
                            <Space.Compact size="small">
                              <Input
                                size="small"
                                autoFocus
                                value={renameValue}
                                onChange={(e) => setRenameValue(e.target.value)}
                                onPressEnter={renameCategory}
                                style={{ width: 110 }}
                                maxLength={30}
                              />
                              <Button size="small" type="primary" onClick={renameCategory}>저장</Button>
                              <Button size="small" onClick={() => setRenamingCategory(null)}>취소</Button>
                            </Space.Compact>
                          ) : (
                            <>
                              <Tag.CheckableTag
                                checked={filterCategory === cat}
                                onChange={(checked) => setFilterCategory(checked ? cat : null)}
                                style={filterCategory === cat
                                  ? {
                                      background: CAT_SOFT_BG[cat] ?? '#f0f0f0',
                                      color: CAT_ACCENT_HEX[cat] ?? '#595959',
                                      borderColor: CAT_ACCENT_HEX[cat] ?? '#d9d9d9',
                                      marginRight: 0,
                                    }
                                  : { marginRight: 0 }}
                              >
                                {label} {stats.by_category?.[cat] ?? 0}
                              </Tag.CheckableTag>
                              {/* 고정 5종(사양서·연구자료·발표자료·보고서·기타)은 수정·삭제 불가 — 커스텀 카테고리만 버튼 노출 */}
                              {!(cat in CAT_LABEL) && (() => {
                                const docCount = stats.by_category?.[cat] ?? 0
                                return (
                                  <>
                                    <Button
                                      type="text" size="small"
                                      icon={<EditOutlined style={{ fontSize: 11 }} />}
                                      style={{ padding: '0 4px', height: 20 }}
                                      onClick={() => { setRenamingCategory(cat); setRenameValue(label) }}
                                    />
                                    <Button
                                      type="text" size="small"
                                      icon={<SwapOutlined style={{ fontSize: 11 }} />}
                                      style={{ padding: '0 4px', height: 20 }}
                                      disabled={docCount === 0}
                                      onClick={() => { setMovingCategory(cat); setMoveTarget(null) }}
                                    />
                                    {docCount > 0 ? (
                                      <Tooltip title="문서가 남아있는 카테고리는 삭제할 수 없습니다 — 먼저 다른 카테고리로 이동해주세요">
                                        <Button
                                          type="text" size="small" danger disabled
                                          icon={<DeleteOutlined style={{ fontSize: 11 }} />}
                                          style={{ padding: '0 4px', height: 20 }}
                                        />
                                      </Tooltip>
                                    ) : (
                                      <Popconfirm
                                        title={`"${label}" 카테고리를 삭제할까요?`}
                                        description="문서가 없는 빈 카테고리입니다."
                                        okText="삭제" cancelText="취소"
                                        onConfirm={() => deleteCategory(cat)}
                                      >
                                        <Button
                                          type="text" size="small" danger
                                          icon={<DeleteOutlined style={{ fontSize: 11 }} />}
                                          style={{ padding: '0 4px', height: 20 }}
                                          loading={bulkCategoryMutation.isPending}
                                        />
                                      </Popconfirm>
                                    )}
                                  </>
                                )
                              })()}
                            </>
                          )}
                        </span>
                      ))}
                    </div>
                    {(() => {
                      const filtered = documents.filter(d =>
                        (!filterCategory || d.category === filterCategory) &&
                        (!catSearch || (d.filename || '').toLowerCase().includes(catSearch.toLowerCase()))
                      )
                      if (filtered.length === 0) {
                        return <Empty description="조건에 맞는 문서가 없습니다" style={{ margin: '32px 0' }} />
                      }
                      if (catViewMode === 'list') {
                        return (
                          <Table
                            columns={docColumns} dataSource={filtered} rowKey="id"
                            pagination={{ pageSize: 10 }}
                            scroll={isNarrow ? { x: 'max-content' } : undefined}
                          />
                        )
                      }
                      return (
                        <Row gutter={[16, 16]}>
                          {filtered.map(doc => (
                            <Col key={doc.id} xs={24} sm={12} md={8} lg={6}>
                              <ACard size="small" hoverable onClick={() => openDetail(doc)} styles={{ body: { padding: 12 } }}>
                                <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }} ellipsis={{ tooltip: doc.filename }}>
                                  {doc.filename}
                                </Text>
                                <Row justify="space-between" align="middle" style={{ marginBottom: 6 }}>
                                  <Col><Tag color={doc.category ? CAT_COLOR[doc.category] : undefined} style={{ marginRight: 0 }}>{doc.category ? (CAT_LABEL[doc.category] ?? doc.category) : '미지정'}</Tag></Col>
                                  <Col><Text type="secondary" style={{ fontSize: 11.5 }}>{(doc.uploaded_at || '').slice(0, 10)}</Text></Col>
                                </Row>
                                <Row justify="space-between" align="middle">
                                  <Col><Text type="secondary" style={{ fontSize: 11.5 }}><EyeOutlined style={{ marginRight: 4 }} />{doc.view_count ?? 0}</Text></Col>
                                  {doc.uploaded_by && doc.uploaded_by !== 'anonymous' && (
                                    <Col><Text type="secondary" style={{ fontSize: 11.5 }} ellipsis={{ tooltip: doc.uploaded_by }}><UserOutlined style={{ marginRight: 4 }} />{doc.uploaded_by_name ?? doc.uploaded_by}</Text></Col>
                                  )}
                                </Row>
                              </ACard>
                            </Col>
                          ))}
                        </Row>
                      )
                    })()}
                  </ACard>
                </Col>
              </Row>

              <Row gutter={[20, 20]} style={{ marginBottom: 20 }}>
                <Col span={24}>
                  {(() => {
                    // 카테고리 관리에서 고른 필터(전체/사양서/...)를 그대로 따라가도록,
                    // 전역 stats.by_file_type이 아니라 documents를 filterCategory로 직접 걸러 집계한다.
                    const docsForFileType = documents.filter(d => !filterCategory || d.category === filterCategory)
                    const fileTypeCounts = docsForFileType.reduce((acc, d) => {
                      const ft = d.file_type || 'unknown'
                      acc[ft] = (acc[ft] ?? 0) + 1
                      return acc
                    }, {})
                    const fileTypeTotal = docsForFileType.length
                    const scopeLabel = filterCategory ? (CAT_LABEL[filterCategory] ?? filterCategory) : '전체'
                    return (
                      <ACard title={`파일 형식별 문서 수 (${scopeLabel})`} size="small">
                        {fileTypeTotal === 0
                          ? <Text type="secondary">해당하는 문서 없음</Text>
                          : Object.entries(fileTypeCounts).sort((a, b) => b[1] - a[1]).map(([ft, cnt]) => {
                            const pct = fileTypeTotal > 0 ? Math.round((cnt / fileTypeTotal) * 100) : 0
                            return (
                              <div key={ft} style={{ marginBottom: 22 }}>
                                <Row justify="space-between" style={{ marginBottom: 8 }}>
                                  <Col><Tag color={FILE_TYPE_COLOR[ft] ?? 'default'} style={{ marginRight: 0 }}>{ft.toUpperCase()}</Tag></Col>
                                  <Col><Text type="secondary" style={{ fontSize: 12 }}>{cnt}건</Text></Col>
                                </Row>
                                <Progress percent={pct} showInfo={false} size="small" />
                              </div>
                            )
                          })}
                      </ACard>
                    )
                  })()}
                </Col>
              </Row>

              <ACard
                title={`최근 ${trendPeriod}일 업로드 현황`} size="small"
                extra={
                  <Radio.Group size="small" value={trendPeriod} onChange={(e) => setTrendPeriod(e.target.value)}>
                    <Radio.Button value={7}>7일</Radio.Button>
                    <Radio.Button value={14}>14일</Radio.Button>
                    <Radio.Button value={30}>30일</Radio.Button>
                  </Radio.Group>
                }
              >
                {trend.length > 0 && (() => {
                  const maxCount = Math.max(...trend.map(d => d.count), 1)
                  const todayStr = trend[trend.length - 1]?.date
                  const CHART_H = 160
                  return (
                    <div style={{ display: 'flex', alignItems: 'flex-end', gap: trendPeriod > 7 ? 3 : 6, height: CHART_H + 24, paddingBottom: 24 }}>
                      {trend.map((d) => {
                        const isToday = d.date === todayStr
                        const barH = d.count > 0 ? Math.max((d.count / maxCount) * CHART_H, 8) : 3
                        return (
                          <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: d.count > 0 ? (isToday ? '#1677ff' : '#595959') : 'transparent' }}>{d.count > 0 ? d.count : '0'}</span>
                            <Tooltip title={`${d.date}: ${d.count}건`}>
                              <div style={{ width: '100%', height: barH, background: d.count > 0 ? (isToday ? '#0958d9' : '#91caff') : '#f0f0f0', borderRadius: '4px 4px 0 0' }} />
                            </Tooltip>
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
              </ACard>
            </>
          )}

          {navKey === 'departments' && (
            <>
              <Alert
                type="info" showIcon style={{ marginBottom: 20 }}
                title="문서는 업로드한 사람의 부서를 그대로 물려받습니다"
                description="여기서 지정한 부서가 문서의 열람 범위 기준이 됩니다 — 같은 부서 소속만 볼 수 있고, 다른 부서라도 문서별 '읽기 권한'에 개별로 추가된 사람은 예외적으로 볼 수 있습니다. 부서를 비워두면 그 사람이 올리는 문서는 부서 제한 없이 공개됩니다."
              />
              <ACard size="small" loading={loadingUsers}>
                <List
                  size="small"
                  dataSource={users}
                  locale={{ emptyText: '아직 로그인한 사용자가 없습니다' }}
                  renderItem={(u) => {
                    const draft = deptDraft[u.email] ?? u.department ?? ''
                    const dirty = draft !== (u.department ?? '')
                    return (
                      <List.Item>
                        {/* 이름 칸은 flex:1(+minWidth:0)로 남는 공간만 먹고 잘리게 하고, 부서 입력·저장·
                            내보내기는 고정폭으로 둔다 — 이름 길이에 따라 뒤쪽 요소들 위치가 흔들리지
                            않고 항상 같은 자리에 정렬되게 하기 위함(2026-07-22) */}
                        <div style={{ display: 'flex', alignItems: 'center', width: '100%', gap: 12 }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <Text strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {u.name || u.email}
                            </Text>
                            {u.name && (
                              <Text type="secondary" style={{ fontSize: 11.5, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {u.email}
                              </Text>
                            )}
                          </div>
                          <Space.Compact style={{ flexShrink: 0 }}>
                            <AutoComplete
                              size="small"
                              style={{ width: 180 }}
                              placeholder="부서 없음(제한 없음)"
                              options={knownDepartments.map(d => ({ value: d }))}
                              value={draft}
                              onChange={(val) => setDeptDraft(prev => ({ ...prev, [u.email]: val }))}
                              filterOption={(input, option) => option.value.toLowerCase().includes(input.toLowerCase())}
                            />
                            <Button
                              size="small" type="primary" disabled={!dirty}
                              loading={departmentMutation.isPending && departmentMutation.variables?.email === u.email}
                              onClick={() => departmentMutation.mutate({ email: u.email, department: draft.trim() || null })}
                            >
                              저장
                            </Button>
                          </Space.Compact>
                          <Popconfirm
                            title={`"${u.email}"를 내보낼까요?`}
                            description="시스템에서 완전히 제거됩니다(부서·이름·관리자 지정 전부 삭제). 다시 로그인하면 부서 미지정 상태로 새로 등록됩니다."
                            okText="내보내기" okButtonProps={{ danger: true }} cancelText="취소"
                            onConfirm={() => removeUserMutation.mutate(u.email)}
                          >
                            <Button
                              size="small" danger
                              icon={<DeleteOutlined />}
                              loading={removeUserMutation.isPending && removeUserMutation.variables === u.email}
                              style={{ flexShrink: 0 }}
                            >
                              내보내기
                            </Button>
                          </Popconfirm>
                        </div>
                      </List.Item>
                    )
                  }}
                />
              </ACard>
            </>
          )}

          {navKey === 'admins' && (
            <>
              <Alert
                type="info" showIcon style={{ marginBottom: 20 }}
                title="구글 로그인 자체를 할 수 있는 사람(테스트 사용자)은 Google Cloud Console에서 관리합니다"
                description="여기서는 '로그인은 되는 사람 중 누가 관리자 권한을 갖는지'만 관리합니다. 관리자로 추가하면 그 이메일로 로그인했을 때 관리자 탭이 보이고 이 화면에 접근할 수 있습니다."
              />

              <ACard title="새 관리자 추가" size="small" style={{ marginBottom: 20 }}>
                <Space.Compact style={{ width: '100%', maxWidth: 420 }}>
                  <Input
                    placeholder="추가할 관리자의 구글 이메일"
                    value={newAdminEmail}
                    onChange={(e) => setNewAdminEmail(e.target.value)}
                    onPressEnter={() => newAdminEmail.trim() && addAdminMutation.mutate(newAdminEmail.trim())}
                  />
                  <Button
                    type="primary"
                    loading={addAdminMutation.isPending}
                    disabled={!newAdminEmail.trim()}
                    onClick={() => addAdminMutation.mutate(newAdminEmail.trim())}
                  >
                    추가
                  </Button>
                </Space.Compact>
              </ACard>

              <ACard title="현재 관리자 목록" size="small" loading={loadingAdmins}>
                {/* 부서 관리 탭과 같은 형태로 통일 — 이름(굵게) 자리에 이메일, 그 아래 부가정보,
                    삭제 버튼은 항상 같은 위치에 고정되도록 flex 행으로 정렬한다(2026-07-22) */}
                <List
                  size="small"
                  dataSource={adminEmails.env_admins.map(email => ({ email, fixed: true }))}
                  renderItem={({ email }) => (
                    <List.Item>
                      <div style={{ display: 'flex', alignItems: 'center', width: '100%', gap: 12 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Text strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {email}
                          </Text>
                          <Text type="secondary" style={{ fontSize: 11.5, display: 'block' }}>고정 관리자</Text>
                        </div>
                      </div>
                    </List.Item>
                  )}
                />
                <List
                  size="small"
                  dataSource={adminEmails.extra_admins}
                  locale={{ emptyText: '웹 화면에서 추가한 관리자가 아직 없습니다' }}
                  renderItem={(row) => (
                    <List.Item>
                      <div style={{ display: 'flex', alignItems: 'center', width: '100%', gap: 12 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Text strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.email}
                          </Text>
                          <Text type="secondary" style={{ fontSize: 11.5, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.added_by ? `${row.added_by} 추가` : ''} {(row.created_at || '').slice(0, 16)}
                          </Text>
                        </div>
                        <Popconfirm
                          title={`"${row.email}"를 관리자에서 제외할까요?`}
                          okText="제외" cancelText="취소"
                          onConfirm={() => removeAdminMutation.mutate(row.email)}
                        >
                          <Button type="text" danger size="small" icon={<DeleteOutlined />} loading={removeAdminMutation.isPending} style={{ flexShrink: 0 }} />
                        </Popconfirm>
                      </div>
                    </List.Item>
                  )}
                />
              </ACard>
            </>
          )}
        </Col>

      </Row>

      {/* ── 카테고리 문서 이동 모달 ──────────────────────────────────────── */}
      <Modal
        title={movingCategory ? `"${CAT_LABEL[movingCategory] ?? movingCategory}" 카테고리의 문서 이동` : '문서 이동'}
        open={!!movingCategory}
        onCancel={() => { setMovingCategory(null); setMoveTarget(null) }}
        onOk={moveCategory}
        okText="이동" cancelText="취소"
        okButtonProps={{ disabled: !moveTarget, loading: bulkCategoryMutation.isPending }}
        destroyOnClose
      >
        <p>
          이 카테고리의 문서 {stats?.by_category?.[movingCategory] ?? 0}개를 옮겨갈 카테고리를 선택하세요.
          이동 후 빈 카테고리가 되면 삭제할 수 있습니다.
        </p>
        <Select
          placeholder="이동할 카테고리 선택"
          value={moveTarget}
          onChange={setMoveTarget}
          style={{ width: '100%' }}
          options={allCategoryOptions.filter(o => o.value !== movingCategory)}
        />
      </Modal>

      {/* ── 삭제 사유 입력 모달 (단건/일괄 공용) ──────────────────────────────── */}
      <Modal
        title={deleteTarget?.type === 'bulk' ? `선택한 ${deleteTarget.ids.length}개 문서 삭제` : '문서 삭제'}
        open={!!deleteTarget}
        onCancel={() => { setDeleteTarget(null); setDeleteReason('') }}
        onOk={confirmDelete}
        okText="삭제" cancelText="취소"
        okButtonProps={{ danger: true, disabled: !deleteReason.trim(), loading: deleteMutation.isPending || bulkDeleteMutation.isPending }}
        destroyOnClose
      >
        <p>삭제 사유를 입력해야 삭제할 수 있습니다 — 나중에 관리자 전용 "삭제 보고서" 화면에서 확인할 수 있습니다.</p>
        <Input.TextArea
          autoFocus
          rows={3}
          maxLength={300}
          showCount
          placeholder="삭제 사유 (필수)"
          value={deleteReason}
          onChange={(e) => setDeleteReason(e.target.value)}
        />
      </Modal>

      {/* ── OCR 수동 수정 모달 ──────────────────────────────────────── */}
      <Modal title={ocrDoc ? `OCR 수동 수정 — ${ocrDoc.filename}` : 'OCR 수동 수정'} open={ocrModal} onCancel={() => setOcrModal(false)} footer={null} width={680} destroyOnClose>
        {!ocrDoc && <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="불러오는 중..." /></div>}
        {ocrDoc && ocrDoc.pages.length === 0 && <Alert type="success" title="모든 페이지가 이미 수정됐습니다." showIcon />}
        {ocrDoc && ocrDoc.pages.map(page => (
          <ACard
            key={page.page_num} size="small" style={{ marginBottom: 16 }}
            title={
              <Space>
                <Tag color="orange" icon={<WarningOutlined />}>{page.page_num}페이지 · OCR 저신뢰</Tag>
                {page.ocr_confidence != null && <Text type="secondary" style={{ fontSize: 11 }}>신뢰도 {Math.round(page.ocr_confidence * 100)}%</Text>}
              </Space>
            }
            extra={<Button type="primary" size="small" loading={ocrSaving} onClick={() => saveOcrEdit(page.page_num)}>저장</Button>}
          >
            <Input.TextArea rows={6} value={ocrEdits[page.page_num] ?? page.text} onChange={(e) => setOcrEdits(prev => ({ ...prev, [page.page_num]: e.target.value }))} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </ACard>
        ))}
      </Modal>

      {/* ── 문서 상세 보기 Drawer ───────────────────────────────────── */}
      <Drawer
        title={detailData ? `${detailData.filename} 상세 보기` : '문서 상세 보기'}
        placement="right" width={580} open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setMemoEdit(null); setTitleEdit(null) }}
        destroyOnClose
        extra={detailData && <Button icon={<DownloadOutlined />} type="primary" href={`${API}/files/${detailData.id}`} download>파일 다운로드</Button>}
      >
        {detailLoading && <div style={{ textAlign: 'center', paddingTop: 60 }}><Spin size="large" tip="불러오는 중..." /></div>}

        {!detailLoading && detailData && (
          <>
            <div style={{ background: '#f0f5ff', border: '1px solid #adc6ff', borderRadius: 6, padding: '10px 14px', marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text type="secondary" style={{ fontSize: 11 }}>문서 제목</Text>
                {titleEdit === null ? (
                  <Button size="small" icon={<EditOutlined />} type="text" onClick={() => setTitleEdit(detailData.title ?? '')}>편집</Button>
                ) : (
                  <Space size={4}><Button size="small" onClick={() => setTitleEdit(null)}>취소</Button><Button size="small" type="primary" loading={titleSaving} onClick={saveTitle}>저장</Button></Space>
                )}
              </div>
              {titleEdit === null ? (
                detailData.title ? <Typography.Title level={5} style={{ margin: 0 }}>{detailData.title}</Typography.Title>
                  : <Text type="secondary" style={{ fontStyle: 'italic' }}>제목 없음 — 편집을 눌러 추가하세요</Text>
              ) : (
                <Input value={titleEdit} onChange={e => setTitleEdit(e.target.value)} onPressEnter={saveTitle} placeholder="문서 제목을 입력하세요" autoFocus />
              )}
            </div>

            <ACard size="small" style={{ marginBottom: 20 }}>
              <Row gutter={[16, 16]}>
                <Col span={12}><Text type="secondary">카테고리</Text><br /><Tag color={detailData.category ? CAT_COLOR[detailData.category] : undefined}>{detailData.category ? (CAT_LABEL[detailData.category] ?? detailData.category) : '미지정'}</Tag></Col>
                <Col span={12}><Text type="secondary">상태</Text><br /><Tag color={detailData.status === 'success' ? 'green' : 'red'}>{detailData.status === 'success' ? '성공' : '실패'}</Tag></Col>
                <Col span={12}><Text type="secondary">총 페이지</Text><br /><Text>{detailData.page_count}페이지</Text></Col>
                <Col span={12}><Text type="secondary">조회수</Text><br /><Text><EyeOutlined style={{ marginRight: 4 }} />{detailData.view_count ?? 0}</Text></Col>
                <Col span={12}>
                  <Text type="secondary">담당자</Text><br />
                  {detailData.uploaded_by && detailData.uploaded_by !== 'anonymous'
                    ? <Text><UserOutlined style={{ marginRight: 4 }} />{detailData.uploaded_by_name ?? detailData.uploaded_by}</Text>
                    : <Text type="secondary">—</Text>}
                </Col>
                <Col span={12}>
                  <Text type="secondary">파일 형식</Text><br />
                  {detailData.file_type ? <Tag color={FILE_TYPE_COLOR[detailData.file_type] ?? 'default'}>{detailData.file_type.toUpperCase()}</Tag> : <Text type="secondary">—</Text>}
                </Col>
                <Col span={24}><Text type="secondary">업로드 시각</Text><br /><Text>{detailData.uploaded_at}</Text></Col>
                {detailData.original_path && (
                  <Col span={24}><Text type="secondary">원본 경로</Text><br /><Text code style={{ fontSize: 12 }}>{detailData.original_path}</Text></Col>
                )}
                <Col span={24} style={{ marginTop: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text type="secondary">메모</Text>
                    {memoEdit === null ? (
                      <Button size="small" icon={<EditOutlined />} type="text" onClick={() => setMemoEdit(detailData.memo ?? '')}>편집</Button>
                    ) : (
                      <Space size={4}><Button size="small" onClick={() => setMemoEdit(null)}>취소</Button><Button size="small" type="primary" loading={memoSaving} onClick={saveMemo}>저장</Button></Space>
                    )}
                  </div>
                  {memoEdit === null ? (
                    detailData.memo ? <Text style={{ whiteSpace: 'pre-wrap' }}>{detailData.memo}</Text> : <Text type="secondary" style={{ fontStyle: 'italic' }}>메모 없음 — 편집을 눌러 추가하세요</Text>
                  ) : (
                    <Input.TextArea rows={3} value={memoEdit} onChange={(e) => setMemoEdit(e.target.value)} placeholder="이 문서에 대한 설명을 입력하세요 (신입이 검색 결과에서 볼 수 있습니다)" autoFocus />
                  )}
                </Col>
              </Row>
              {detailData.has_flagged && <Alert type="warning" showIcon title="OCR 저신뢰 페이지가 포함된 문서입니다. 내용을 직접 확인하세요." style={{ marginTop: 12 }} />}
              {detailData.error && <Alert type="error" showIcon title={detailData.error} style={{ marginTop: 12 }} />}
            </ACard>

            <ACard size="small" title="읽기 권한" style={{ marginBottom: 16 }}>
              {permissions.length === 0 ? (
                <Alert
                  type="info" showIcon style={{ marginBottom: 12 }}
                  title="전체 공개 — 아래에 이메일을 추가하면 그 사람들과 관리자만 볼 수 있게 제한됩니다"
                />
              ) : (
                <>
                  <Alert
                    type="warning" showIcon style={{ marginBottom: 12 }}
                    title="제한됨 — 아래 목록에 있는 사람과 관리자만 검색·다운로드할 수 있습니다"
                  />
                  <Space orientation="vertical" style={{ width: '100%', marginBottom: 12 }} size={6}>
                    {permissions.map(p => (
                      <div key={p.email} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <Text style={{ fontSize: 13 }}>{p.email}</Text>
                        <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removePermission(p.email)} />
                      </div>
                    ))}
                  </Space>
                </>
              )}
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="이메일 추가 (예: user@company.com)"
                  value={newPermEmail}
                  onChange={(e) => setNewPermEmail(e.target.value)}
                  onPressEnter={addPermission}
                />
                <Button type="primary" loading={permSaving} onClick={addPermission}>추가</Button>
              </Space.Compact>
            </ACard>

            {detailData.pages.length === 0 ? (
              <Alert type="info" title="저장된 파싱 데이터가 없습니다. 이 문서는 업그레이드 전에 업로드됐을 수 있습니다." />
            ) : (
              <Collapse
                accordion
                items={detailData.pages.map((page, idx) => {
                  const pageNum = page.page_num ?? page.page ?? (idx + 1)
                  const text    = page.text ?? ''
                  const flagged = page.flagged ?? false
                  return {
                    key: String(idx),
                    label: <span>{pageNum}페이지{flagged && <Tag color="orange" icon={<WarningOutlined />} style={{ marginLeft: 8 }}>OCR 저신뢰</Tag>}</span>,
                    children: (
                      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 13, maxHeight: 360, overflowY: 'auto', background: '#fafafa', padding: 12, borderRadius: 6, margin: 0 }}>
                        {text || <Text type="secondary">(텍스트 없음)</Text>}
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
