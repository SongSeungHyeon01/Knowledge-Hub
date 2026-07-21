// UploadPage.jsx — 문서 업로드 화면
// 2026-07-17: 우측 정보 패널(업로드 팁·오늘 현황·최근 인덱싱) 제거 — 좌측 서브메뉴 + 중앙 업로드 폼 2단 레이아웃.
// 업로드·배치폴링·중복확인 로직(doUpload/handleUpload/runBatchPoll)은 기존 그대로 유지.

import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Upload, Table, Typography, Space, Tag, Progress, Modal, Button, Tooltip,
  Alert, Menu, Row, Col, Card, List, Empty, Input, message,
} from 'antd'
import {
  InboxOutlined,
  FolderOpenOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  FileTextOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  FilePptOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  CloseOutlined,
  ExclamationCircleOutlined,
  SearchOutlined,
  CloudUploadOutlined,
  HistoryOutlined,
  PlusOutlined,
  EditOutlined,
} from '@ant-design/icons'
import axios from 'axios'
import useIsNarrow from './useIsNarrow'

const { Dragger } = Upload
const { Title, Text, Paragraph } = Typography

const API = import.meta.env.VITE_API_URL

const CATEGORIES = ['spec', 'research', 'presentation', 'report']

const CATEGORY_COLOR = {
  spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green',
}
const CATEGORY_LABEL = {
  spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서',
}

// 파일 확장자별 아이콘
const getFileIcon = (filename) => {
  const ext = filename?.split('.').pop()?.toLowerCase()
  const style = { fontSize: 17 }
  switch (ext) {
    case 'pdf':  return <FilePdfOutlined  style={{ ...style, color: '#ff4d4f' }} />
    case 'docx':
    case 'doc':  return <FileWordOutlined style={{ ...style, color: '#1677ff' }} />
    case 'pptx':
    case 'ppt':  return <FilePptOutlined  style={{ ...style, color: '#fa8c16' }} />
    case 'xlsx':
    case 'xls':  return <FileExcelOutlined style={{ ...style, color: '#52c41a' }} />
    case 'hwp':
    case 'hwpx': return <FileTextOutlined  style={{ ...style, color: '#722ed1' }} />
    case 'png':
    case 'jpg':
    case 'jpeg': return <FileImageOutlined style={{ ...style, color: '#13c2c2' }} />
    case 'md':   return <FileMarkdownOutlined style={{ ...style, color: '#722ed1' }} />
    case 'txt':  return <FileTextOutlined style={{ ...style, color: '#8c8c8c' }} />
    default:     return <FileOutlined style={{ ...style, color: '#722ed1' }} />
  }
}

// 파일 크기 포맷
const formatSize = (bytes) => {
  if (!bytes) return ''
  if (bytes < 1024)        return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

const STATUS_TAG = {
  uploading: <Tag icon={<LoadingOutlined />} color="processing">전송 중</Tag>,
  parsing:   <Tag icon={<LoadingOutlined />} color="warning">처리 중</Tag>,
  success:   <Tag icon={<CheckCircleOutlined />} color="success">완료</Tag>,
  failed:    <Tag icon={<CloseCircleOutlined />} color="error">실패</Tag>,
}

export default function UploadPage({ onNavigate }) {
  const isNarrow = useIsNarrow()
  const [navKey, setNavKey] = useState('quick')

  // 카테고리·특이사항 — 배치 업로드 시 파일마다 다른 값이 필요할 수 있어(업로드 전 공용
  // 입력란 하나로는 모든 파일에 같은 값이 붙어버림), 업로드 폼이 아니라 업로드가 접수된
  // 직후 파일마다 순서대로 물어본다. /me/documents/{id} PATCH(본인 문서 자가 수정용)를 재사용.
  const [promptQueue,    setPromptQueue]    = useState([])  // 아직 안 물어본 파일들(대기열)
  const [promptFile,     setPromptFile]     = useState(null)  // 지금 물어보는 중인 파일(null이면 닫힘)
  const [promptCategory, setPromptCategory] = useState('')
  const [promptMemo,     setPromptMemo]     = useState('')
  const [promptSaving,   setPromptSaving]   = useState(false)

  // 클라이언트가 방금 만든 카테고리 — 문서에 아직 반영 전(업로드 완료 전)이라
  // stats.by_category에 안 잡힌 값을 이번 세션에서 바로 다시 고를 수 있게 메모리에만 둔다.
  // localStorage에 영구 저장하지 않는 이유: 관리자가 카테고리 관리 탭에서 이름을 바꾸거나
  // 삭제해도 여기 캐시가 안 지워지면 그 브라우저에서 이미 없어진 카테고리가 계속 선택지로
  // 남아 화면마다 다른 카테고리 목록이 보이는 문제가 생긴다 — 새로고침하면 stats.by_category
  // (서버 실제 값)로만 다시 채워지게 세션 한정으로만 유지한다.
  const [customCategories, setCustomCategories] = useState([])
  const [newCatOpen, setNewCatOpen] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [hoveredCategory, setHoveredCategory] = useState(null)  // 좌측 카테고리 목록 마우스오버 표시용

  // files: 이번 브라우저 세션에서 업로드 중이거나 완료된 파일 목록
  // 각 항목: { id, filename, size, category, percent, status, pages, error }
  const [files, setFiles] = useState([])

  const updateFile = (id, patch) =>
    setFiles(prev => prev.map(f => f.id === id ? { ...f, ...patch } : f))

  // ── 우측 패널·"최근 업로드" 탭용 실제 데이터 — GET /admin/documents, GET /admin/stats ──
  // (관리자 전용 엔드포인트지만 현재 MVP엔 로그인·권한 구분이 없어 업로드 화면에서도 그대로 사용)
  const { data: allDocs = [] } = useQuery({
    queryKey: ['upload-page-documents'],
    queryFn: () => axios.get(`${API}/admin/documents`).then(r => r.data),
    refetchInterval: 8000,
  })
  const { data: stats } = useQuery({
    queryKey: ['upload-page-stats'],
    queryFn: () => axios.get(`${API}/admin/stats`).then(r => r.data),
    refetchInterval: 8000,
  })

  // 고정 5종 + (이 브라우저에서 만든 것 ∪ 실제 서버에 존재하는 것) 커스텀 카테고리
  const allCategories = useMemo(() => {
    const observed = Object.keys(stats?.by_category ?? {})
    const extra = [...customCategories, ...observed].filter(c => !CATEGORIES.includes(c))
    return [...CATEGORIES, ...Array.from(new Set(extra))]
  }, [customCategories, stats])

  const handleCreateCategory = () => {
    const name = newCatName.trim().slice(0, 30)
    if (!name) return
    if (!allCategories.some(c => c.toLowerCase() === name.toLowerCase())) {
      setCustomCategories(prev => [...prev, name])
    }
    setPromptCategory(name)
    setNewCatName('')
    setNewCatOpen(false)
  }

  // 업로드가 접수된 파일을 카테고리·특이사항 대기열에 넣는다 — 지금 아무것도 안 물어보는
  // 중이면 아래 useEffect가 바로 꺼내서 물어보고, 이미 물어보는 중이면 그게 끝난 뒤 이어서 물어본다.
  const enqueuePrompt = (f) =>
    setPromptQueue(prev => [...prev, f])

  useEffect(() => {
    if (promptFile || promptQueue.length === 0) return
    const [next, ...rest] = promptQueue
    setPromptQueue(rest)
    setPromptFile(next)
    setPromptCategory(next.category)
    setPromptMemo(next.memo || '')
  }, [promptQueue, promptFile])

  // 결과 테이블에서 이미 값이 있는 카테고리·특이사항을 다시 고칠 때도 같은 모달을 재사용
  const openPrompt = (f) => {
    setPromptFile(f)
    setPromptCategory(f.category)
    setPromptMemo(f.memo || '')
  }

  const savePrompt = async () => {
    if (!promptFile) return
    setPromptSaving(true)
    try {
      await axios.patch(`${API}/me/documents/${promptFile.docId}`, {
        category: promptCategory, memo: promptMemo,
      })
      updateFile(promptFile.id, { category: promptCategory, memo: promptMemo.trim() || null })
      setPromptFile(null)
    } catch {
      message.error('저장에 실패했습니다')
    } finally {
      setPromptSaving(false)
    }
  }

  // 파싱 상태 폴링 — 업로드는 접수 즉시 응답하고(status='parsing'), 실제 파싱은
  // 서버 백그라운드에서 진행되므로, 완료(success/failed)를 폴링으로 감지한다.
  //
  // [배치화 2026-07-06] 예전에는 파일마다 setInterval을 하나씩 만들어 각자
  // /documents/{id}/status 를 2초마다 물었다. 동시 업로드가 많으면(예: 더미 49개)
  // 폴링 요청이 그만큼 배로 늘어 SQLite 커넥션 풀을 순간적으로 고갈시켰다
  // (실측 8,698회 요청 → QueuePool TimeoutError). 이제 "파싱 중인 docId 목록"을
  // pendingRef 하나로 모아 전역 타이머 1개가 /documents/statuses?ids=... 를
  // 한 번에 조회한다 — 동시 업로드 문서 수와 무관하게 폴링 요청은 2초에 1회뿐이다.
  const pendingRef = useRef(new Map())   // docId -> { localId, startedAt }
  const timerRef   = useRef(null)

  const runBatchPoll = async () => {
    const pending = pendingRef.current
    if (pending.size === 0) {
      clearInterval(timerRef.current)
      timerRef.current = null
      return
    }

    const now = Date.now()
    // 안전장치: 30분 넘게 parsing 상태로 남아있는 문서는 더 이상 폴링하지 않는다
    // (문서는 서버에서 계속 처리될 수 있으나, 화면 표시는 현재 상태를 그대로 둔다)
    for (const [docId, info] of pending) {
      if (now - info.startedAt > 30 * 60 * 1000) pending.delete(docId)
    }
    if (pending.size === 0) {
      clearInterval(timerRef.current)
      timerRef.current = null
      return
    }

    const ids = [...pending.keys()].join(',')
    try {
      const r = await axios.get(`${API}/documents/statuses`, { params: { ids } })
      const byId = new Map(r.data.map(d => [d.id, d]))
      for (const docId of [...pending.keys()]) {
        const info = pending.get(docId)
        const doc  = byId.get(docId)
        if (!doc) {
          pending.delete(docId)
          continue
        }
        if (doc.status === 'parsing') continue

        // 파싱 결과(성공/실패)는 알게 되는 즉시 테이블에 반영하고, 바로 카테고리·특이사항을 물어본다.
        updateFile(info.localId, {
          status: doc.status,
          pages:  doc.page_count ?? 0,
          error:  doc.error,
          category: doc.category,
        })
        enqueuePrompt({
          id: info.localId, docId, filename: info.filename,
          category: doc.category, memo: null,
        })
        pending.delete(docId)
      }
    } catch {
      // 일시적 네트워크 오류는 무시하고 다음 주기에 다시 시도 (pending 유지)
    }
  }

  // 폴링 대상에 문서를 추가하고, 전역 타이머가 없으면 시작한다
  const addToPolling = (localId, docId, filename) => {
    pendingRef.current.set(docId, { localId, filename, startedAt: Date.now() })
    if (timerRef.current == null) {
      timerRef.current = setInterval(runBatchPoll, 2000)
    }
  }

  // 개별 항목 제거 (완료된 항목만)
  const removeFile = (id) =>
    setFiles(prev => prev.filter(f => f.id !== id))

  const SUPPORTED_EXT = ['.pdf','.docx','.pptx','.ppt','.xlsx','.xls','.hwp','.hwpx','.txt','.md','.png','.jpg','.jpeg']
  const ACCEPT_ATTR   = SUPPORTED_EXT.join(',')
  const MAX_SIZE_MB   = 500  // 2026-07-11: 백엔드 MAX_UPLOAD_SIZE(500MB)와 통일 — 이전엔 200MB로 따로 남아있었음

  const doUpload = async (file) => {
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      const mb = (file.size / (1024 * 1024)).toFixed(1)
      setFiles(prev => [{
        id: `${file.name}-${Date.now()}`,
        filename: file.name, category: '',
        percent: 100, status: 'failed', pages: 0,
        error: `파일 크기 초과 (${mb}MB). 최대 ${MAX_SIZE_MB}MB까지 허용됩니다`,
      }, ...prev])
      return false
    }

    const id = `${file.name}-${Date.now()}`

    setFiles(prev => [{
      id, filename: file.name, size: file.size, category: '',
      percent: 0, status: 'uploading', pages: 0, error: null,
    }, ...prev])

    const form = new FormData()
    form.append('file', file)
    // 카테고리는 여기서 아무 기본값도 보내지 않는다 — 파싱이 끝난 뒤 "카테고리·특이사항
    // 설정" 팝업에서 사용자가 직접 고르기 전까지는 미지정 상태로 남아 있어야 한다.
    form.append('category', '')
    if (file.webkitRelativePath) {
      form.append('original_path', file.webkitRelativePath)
    }

    try {
      const res = await axios.post(`${API}/upload`, form, {
        onUploadProgress: (e) => {
          const percent = Math.round((e.loaded / e.total) * 100)
          updateFile(id, { percent })
        },
      })
      const data = res.data
      if (data.status === 'parsing' && data.id != null) {
        // 접수됨 — 전송 100%, 이제 서버 파싱 대기(폴링으로 완료 감지)
        updateFile(id, { percent: 100, status: 'parsing', docId: data.id, error: null })
        // 카테고리·특이사항 확인 팝업은 여기서 바로 띄우지 않는다 — 파싱이 끝나야
        // 문서가 확정되므로, runBatchPoll이 파싱 완료(성공/실패)를 확인한 뒤 띄운다.
        addToPolling(id, data.id, file.name)
      } else {
        // 검사 단계 즉시 실패(지원 안 함·크기 초과 등) 또는 그 외 응답
        updateFile(id, {
          percent: 100,
          status:  data.status ?? 'failed',
          pages:   data.pages?.length ?? 0,
          error:   data.error,
        })
      }
    } catch {
      updateFile(id, { percent: 100, status: 'failed', error: '서버 연결 오류' })
    }

    return false
  }

  const handleUpload = async (file) => {
    try {
      const res = await axios.get(`${API}/upload/check`, { params: { filename: file.name } })
      if (res.data.exists) {
        Modal.confirm({
          title: '이미 업로드된 파일입니다',
          icon: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
          content: (
            <div>
              <p><strong>{file.name}</strong></p>
              <p style={{ color: '#888' }}>
                이전 업로드: {res.data.uploaded_at}<br />
                상태: {res.data.status === 'success' ? '성공' : '실패'}
              </p>
              <p>그래도 덮어쓰기 하시겠습니까?</p>
            </div>
          ),
          okText: '덮어쓰기',
          cancelText: '취소',
          onOk: () => doUpload(file),
        })
        return false
      }
    } catch {
      // 네트워크 오류 시 중복 체크 없이 그냥 업로드 진행
    }
    doUpload(file)
    return false
  }

  const isUploading = files.some(f => f.status === 'uploading')

  const columns = [
    {
      title: '파일명',
      dataIndex: 'filename',
      render: (name, f) => (
        <Space size={6}>
          {getFileIcon(name)}
          <Tooltip title={name}>
            <Text style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>
              {name}
            </Text>
          </Tooltip>
        </Space>
      ),
    },
    {
      title: '카테고리',
      dataIndex: 'category',
      width: 110,
      render: (c, f) => (
        <Tag
          color={c ? CATEGORY_COLOR[c] : undefined}
          style={{ cursor: f.docId ? 'pointer' : 'default', ...(c ? {} : { borderStyle: 'dashed', color: '#bfbfbf' }) }}
          onClick={() => f.docId && openPrompt(f)}
        >
          {c ? (CATEGORY_LABEL[c] ?? c) : '미지정'}
        </Tag>
      ),
    },
    {
      title: '크기',
      dataIndex: 'size',
      width: 90,
      render: (s) => <Text type="secondary">{formatSize(s)}</Text>,
    },
    {
      title: '상태',
      dataIndex: 'status',
      width: 210,
      render: (status, f) => {
        if (status === 'uploading') {
          return <Progress percent={f.percent} size="small" status="active" style={{ maxWidth: 160 }} />
        }
        if (status === 'failed') {
          return (
            <Space direction="vertical" size={0}>
              {STATUS_TAG.failed}
              <Text type="danger" style={{ fontSize: 11.5 }}>{f.error}</Text>
            </Space>
          )
        }
        return (
          <Space size={6}>
            {STATUS_TAG[status]}
            {status === 'success' && <Text type="secondary" style={{ fontSize: 12 }}>{f.pages}p</Text>}
          </Space>
        )
      },
    },
    {
      title: '특이사항',
      width: 190,
      render: (_, f) => {
        if (!f.docId) return <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        return (
          <Button
            type="link" size="small"
            style={{ padding: 0, height: 'auto', maxWidth: 170, textAlign: 'left' }}
            onClick={() => openPrompt(f)}
          >
            <Text
              style={{ fontSize: 12.5, color: f.memo ? undefined : '#bfbfbf' }}
              ellipsis={{ tooltip: f.memo }}
            >
              {f.memo || <><EditOutlined style={{ marginRight: 4 }} />메모 추가</>}
            </Text>
          </Button>
        )
      },
    },
    {
      title: '',
      width: 90,
      render: (_, f) => (
        <Space size={4}>
          {f.status === 'success' && onNavigate && (
            <Button
              type="link" size="small" icon={<SearchOutlined />}
              onClick={() => {
                localStorage.setItem('km_launch_query', f.filename.replace(/\.[^.]+$/, ''))
                onNavigate('search')
              }}
            />
          )}
          {f.status !== 'uploading' && f.status !== 'parsing' && (
            <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => removeFile(f.id)} />
          )}
        </Space>
      ),
    },
  ]

  const leftMenuItems = [
    { key: 'quick',  icon: <CloudUploadOutlined />, label: '업로드' },
    { key: 'recent', icon: <HistoryOutlined />,     label: '최근 업로드' },
  ]

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: '24px 24px 40px' }}>
      <Row gutter={20} wrap={isNarrow}>
        {/* ── 좌측 서브메뉴 — 창이 좁아지면(노트북 반접이 이하) 위로 쌓임 ───── */}
        {/* marginTop: 제목·안내문·경고 배너 2개 밑, 업로드 드래그 보드 윗줄과 맞춤(넓을 때만) */}
        <Col
          flex={isNarrow ? '0 0 100%' : '200px'}
          style={isNarrow
            ? { marginBottom: 16 }
            : { position: 'sticky', top: 80, alignSelf: 'flex-start', marginTop: 220 }}
        >
          <Menu
            mode="inline"
            selectedKeys={[navKey]}
            onClick={(e) => setNavKey(e.key)}
            items={leftMenuItems}
            style={{ border: '1px solid #eef0f2', borderRadius: 8 }}
          />
          <Card size="small" style={{ marginTop: 16 }} title="카테고리">
            <List
              size="small"
              dataSource={allCategories}
              renderItem={(c) => (
                <List.Item
                  onClick={() => {
                    localStorage.setItem('km_launch_category', c)
                    onNavigate?.('search')
                  }}
                  onMouseEnter={() => setHoveredCategory(c)}
                  onMouseLeave={() => setHoveredCategory(null)}
                  style={{
                    padding: '6px 8px',
                    margin: '0 -8px',
                    border: 'none',
                    borderRadius: 6,
                    display: 'flex',
                    justifyContent: 'space-between',
                    cursor: 'pointer',
                    background: hoveredCategory === c ? '#f0f5ff' : 'transparent',
                    transition: 'background 0.15s',
                  }}
                >
                  <Text style={{ fontSize: 13 }}>{CATEGORY_LABEL[c] ?? c}</Text>
                  <Text strong>{stats?.by_category?.[c] ?? 0}</Text>
                </List.Item>
              )}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', paddingTop: 8, marginTop: 4, borderTop: '1px solid #f0f0f0' }}>
              <Text strong style={{ fontSize: 13 }}>합계</Text>
              <Text strong style={{ color: '#1677ff' }}>{stats?.total_documents ?? 0}</Text>
            </div>
          </Card>
        </Col>

        {/* ── 중앙: 업로드 폼 ───────────────────────────────────── */}
        {/* minWidth: 0 — 넓은 테이블 때문에 3열 Row가 줄바꿈되는 것을 방지(AdminPage와 동일한 이유) */}
        <Col flex="auto" style={{ minWidth: 0 }}>
          {navKey === 'quick' && (
            <>
              <div>
                <Title level={3} style={{ marginBottom: 2 }}>문서 업로드</Title>
                <Paragraph type="secondary" style={{ marginBottom: 16 }}>
                  문서를 업로드하면 자동으로 인덱싱 및 메타데이터 추출이 진행됩니다.
                </Paragraph>
              </div>

              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="안정적인 처리를 위해 한 번에 3~4개씩 나눠서 업로드해 주세요"
              />
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="카테고리·특이사항은 업로드가 접수되면 파일마다 바로 물어봅니다"
              />

              <Modal
                title="새 카테고리 만들기"
                open={newCatOpen}
                onCancel={() => { setNewCatOpen(false); setNewCatName('') }}
                onOk={handleCreateCategory}
                okText="만들고 선택"
                okButtonProps={{ disabled: !newCatName.trim() }}
              >
                <Input
                  placeholder="예) 회의록, 온보딩 자료"
                  value={newCatName}
                  onChange={(e) => setNewCatName(e.target.value)}
                  onPressEnter={handleCreateCategory}
                  maxLength={30}
                  autoFocus
                />
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                  이 이름으로 바로 업로드에 쓸 수 있고, 검색·관리자 화면의 카테고리 필터에도 자동으로 나타납니다.
                </Text>
              </Modal>

              <Dragger
                accept={ACCEPT_ATTR}
                multiple
                showUploadList={false}
                beforeUpload={handleUpload}
                disabled={isUploading}
                style={{ marginBottom: 20, background: '#fafcff' }}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined style={{ fontSize: 44, color: isUploading ? '#aaa' : '#1677ff' }} />
                </p>
                <p className="ant-upload-text">
                  {isUploading ? '업로드 중...' : '파일을 드래그하거나 클릭해서 업로드'}
                </p>
                <p className="ant-upload-hint">
                  지원 형식: PDF · HWP · HWPX · DOCX · PPT · PPTX · XLSX (파일당 최대 {MAX_SIZE_MB}MB)
                  <br />.xls(구버전 엑셀)는 처리 실패가 확인돼 지원 목록에서 제외
                </p>
                <Upload
                  directory multiple showUploadList={false}
                  beforeUpload={handleUpload} disabled={isUploading} accept={ACCEPT_ATTR}
                >
                  <Button icon={<FolderOpenOutlined />} disabled={isUploading} onClick={e => e.stopPropagation()} style={{ marginTop: 10 }}>
                    폴더째 업로드
                  </Button>
                </Upload>
              </Dragger>

              <Table
                columns={columns}
                dataSource={files}
                rowKey="id"
                size="middle"
                pagination={{ pageSize: 8 }}
                locale={{ emptyText: <Empty description="아직 업로드한 파일이 없습니다" /> }}
              />

              <Modal
                title="카테고리·특이사항 설정"
                open={!!promptFile}
                onCancel={() => setPromptFile(null)}
                onOk={savePrompt}
                okText="저장"
                confirmLoading={promptSaving}
              >
                <Text type="secondary" style={{ fontSize: 12.5, display: 'block', marginBottom: 14 }}>
                  {promptFile?.filename}
                </Text>

                <div style={{ marginBottom: 14 }}>
                  <Text strong style={{ fontSize: 12.5 }}>카테고리</Text>
                  <div style={{ marginTop: 8 }}>
                    <Space wrap size={[8, 8]}>
                      {allCategories.map(c => (
                        <Button
                          key={c} size="small"
                          type={promptCategory === c ? 'primary' : 'default'}
                          onClick={() => setPromptCategory(c)}
                        >
                          {CATEGORY_LABEL[c] ?? c}
                        </Button>
                      ))}
                      <Button size="small" icon={<PlusOutlined />} onClick={() => setNewCatOpen(true)}>
                        새 카테고리
                      </Button>
                    </Space>
                  </div>
                </div>

                <div>
                  <Text strong style={{ fontSize: 12.5 }}>특이사항 (선택)</Text>
                  <Input.TextArea
                    style={{ marginTop: 8 }}
                    rows={3}
                    maxLength={1000}
                    placeholder="이 문서에 대해 알아두면 좋을 내용을 적어주세요 (예: 구버전 초안, 승인 대기 중 등) — 검색 결과에 함께 표시됩니다"
                    value={promptMemo}
                    onChange={(e) => setPromptMemo(e.target.value)}
                  />
                </div>
              </Modal>
            </>
          )}

          {navKey === 'recent' && (
            <>
              <Title level={3}>최근 업로드</Title>
              <Table
                dataSource={[...allDocs].sort((a, b) => new Date(b.uploaded_at) - new Date(a.uploaded_at))}
                rowKey="id"
                size="middle"
                pagination={{ pageSize: 12 }}
                columns={[
                  { title: '파일명', dataIndex: 'filename', render: (n) => <Space size={6}>{getFileIcon(n)}<Text>{n}</Text></Space> },
                  { title: '카테고리', dataIndex: 'category', width: 110, render: (c) => <Tag color={c ? CATEGORY_COLOR[c] : undefined}>{c ? (CATEGORY_LABEL[c] ?? c) : '미지정'}</Tag> },
                  { title: '상태', dataIndex: 'status', width: 110, render: (s) => STATUS_TAG[s] ?? <Tag>{s}</Tag> },
                  { title: '업로드 시각', dataIndex: 'uploaded_at', width: 160 },
                ]}
              />
            </>
          )}

        </Col>
      </Row>
    </div>
  )
}
