// UploadPage.jsx — 문서 업로드 화면
// 드래그&드롭으로 PDF를 올리고 카테고리를 선택합니다

import { useState } from 'react'
import { Upload, Select, Card, Typography, Space, Tag, Progress, List, Avatar, Modal, Button, Tooltip } from 'antd'
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
} from '@ant-design/icons'
import axios from 'axios'

const { Dragger } = Upload
const { Title, Text } = Typography

const API = import.meta.env.VITE_API_URL

const CATEGORY_OPTIONS = [
  { value: 'spec',         label: '사양서 (Spec)' },
  { value: 'research',     label: '연구자료 (Research)' },
  { value: 'presentation', label: '발표자료 (Presentation)' },
  { value: 'report',       label: '보고서 (Report)' },
]

const CATEGORY_COLOR = {
  spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green',
}
const CATEGORY_LABEL = {
  spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서',
}
const CATEGORY_ACCENT = {
  spec: '#1677ff', research: '#722ed1', presentation: '#13c2c2', report: '#52c41a',
}
const CATEGORY_BG = {
  spec: '#e6f4ff', research: '#f9f0ff', presentation: '#e6fffb', report: '#f6ffed',
}

// 파일 확장자별 아이콘
const getFileIcon = (filename) => {
  const ext = filename?.split('.').pop()?.toLowerCase()
  const style = { fontSize: 18 }
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

export default function UploadPage({ onNavigate }) {
  const [category, setCategory] = useState(
    () => localStorage.getItem('km_last_category') ?? 'report'
  )

  const handleCategoryChange = (val) => {
    setCategory(val)
    localStorage.setItem('km_last_category', val)
  }
  // files: 업로드 중이거나 완료된 파일 목록
  // 각 항목: { id, filename, size, category, percent, status, pages, error }
  const [files, setFiles] = useState([])

  // 파일 상태 업데이트 헬퍼 함수
  const updateFile = (id, patch) =>
    setFiles(prev => prev.map(f => f.id === id ? { ...f, ...patch } : f))

  // 파싱 상태 폴링 — 업로드는 접수 즉시 응답하고(status='parsing'), 실제 파싱은
  // 서버 백그라운드에서 진행되므로, docId로 /documents/{id}/status 를 2초마다 물어
  // success/failed 로 바뀌면 목록을 갱신한다. (대용량 문서는 완료까지 오래 걸릴 수 있음)
  const pollStatus = (localId, docId) => {
    const started = Date.now()
    const timer = setInterval(async () => {
      try {
        const r = await axios.get(`${API}/documents/${docId}/status`)
        if (r.data.status !== 'parsing') {
          clearInterval(timer)
          updateFile(localId, {
            status: r.data.status,
            pages:  r.data.page_count ?? 0,
            error:  r.data.error,
          })
        }
      } catch {
        // 일시적 네트워크 오류는 무시하고 다음 주기에 다시 시도
      }
      // 안전장치: 30분이 지나면 폴링을 멈춘다 (문서는 서버에서 계속 처리될 수 있음)
      if (Date.now() - started > 30 * 60 * 1000) clearInterval(timer)
    }, 2000)
  }

  // 개별 항목 제거 (완료된 항목만)
  const removeFile = (id) =>
    setFiles(prev => prev.filter(f => f.id !== id))

  // 지원 파일 형식 목록
  const SUPPORTED_EXT = ['.pdf','.docx','.pptx','.ppt','.xlsx','.xls','.hwp','.hwpx','.txt','.md','.png','.jpg','.jpeg']
  const ACCEPT_ATTR   = SUPPORTED_EXT.join(',')
  const MAX_SIZE_MB   = 200

  // 실제 업로드 실행 함수 (중복 확인 후 호출됩니다)
  const doUpload = async (file) => {
    // 프론트에서도 200MB 초과 즉시 차단
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      const mb = (file.size / (1024 * 1024)).toFixed(1)
      setFiles(prev => [{
        id: `${file.name}-${Date.now()}`,
        filename: file.name, category,
        percent: 100, status: 'failed', pages: 0,
        error: `파일 크기 초과 (${mb}MB). 최대 ${MAX_SIZE_MB}MB까지 허용됩니다`,
      }, ...prev])
      return false
    }

    const id = `${file.name}-${Date.now()}`

    setFiles(prev => [{
      id, filename: file.name, size: file.size, category,
      percent: 0, status: 'uploading', pages: 0, error: null,
    }, ...prev])

    const form = new FormData()
    form.append('file', file)
    form.append('category', category)
    // 폴더 업로드 시 원본 경로 전송 (브라우저가 webkitRelativePath 제공)
    if (file.webkitRelativePath) {
      form.append('original_path', file.webkitRelativePath)
    }

    try {
      const res = await axios.post(`${API}/upload`, form, {
        // onUploadProgress: 파일이 서버로 전송되는 % 를 실시간으로 받아옵니다
        onUploadProgress: (e) => {
          const percent = Math.round((e.loaded / e.total) * 100)
          updateFile(id, { percent })
        },
      })
      const data = res.data
      if (data.status === 'parsing' && data.id != null) {
        // 접수됨 — 전송 100%, 이제 서버 파싱 대기(폴링으로 완료 감지)
        updateFile(id, { percent: 100, status: 'parsing', docId: data.id, error: null })
        pollStatus(id, data.id)
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
    // 중복 파일명 확인 후 경고창 표시
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

  // 업로드 중인 파일이 하나라도 있으면 드롭존 비활성화
  const isUploading = files.some(f => f.status === 'uploading')

  return (
    <div style={{ padding: 32, maxWidth: 800, margin: '0 auto' }}>
      <Title level={2}>문서 업로드</Title>

      {/* ── 카테고리 선택 ─────────────────────────────────────────── */}
      <Card
        style={{
          marginBottom: 24,
          borderTop: `3px solid ${CATEGORY_ACCENT[category]}`,
          borderRadius: 8,
          background: CATEGORY_BG[category],
          transition: 'border-top-color 0.3s, background 0.3s',
        }}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>1. 카테고리 선택</Text>
          <Space align="center">
            <Select
              value={category}
              onChange={handleCategoryChange}
              options={CATEGORY_OPTIONS}
              style={{ width: 240 }}
              size="large"
            />
            <Tag color={CATEGORY_COLOR[category]} style={{ fontSize: 13, padding: '2px 10px' }}>
              {CATEGORY_LABEL[category]}
            </Tag>
          </Space>
        </Space>
      </Card>

      {/* ── 드래그&드롭 업로드 영역 ───────────────────────────────── */}
      <Card
        style={{
          marginBottom: 24,
          borderLeft: `4px solid ${CATEGORY_ACCENT[category]}`,
          borderRadius: 8,
          transition: 'border-left-color 0.3s',
        }}
      >
        <Space style={{ width: '100%', justifyContent: 'space-between' }} align="center">
          <Text strong>2. 파일 업로드</Text>
          {/* 폴더 선택 — webkitdirectory로 디렉토리 구조를 그대로 유지합니다 */}
          <Upload
            directory
            multiple
            showUploadList={false}
            beforeUpload={handleUpload}
            disabled={isUploading}
            accept={ACCEPT_ATTR}
          >
            <Button icon={<FolderOpenOutlined />} disabled={isUploading}>
              폴더 선택
            </Button>
          </Upload>
        </Space>
        <Dragger
          accept={ACCEPT_ATTR}
          multiple
          showUploadList={false}
          beforeUpload={handleUpload}
          disabled={isUploading}
          style={{ marginTop: 12 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined style={{ fontSize: 48, color: isUploading ? '#aaa' : '#1677ff' }} />
          </p>
          <p className="ant-upload-text">
            {isUploading ? '업로드 중...' : '파일을 드래그하거나 클릭해서 선택하세요'}
          </p>
          <p className="ant-upload-hint">
            PDF · DOCX · PPTX · XLSX · HWP · HWPX · TXT · MD · PNG · JPG · 최대 200MB · 여러 파일 동시 업로드
          </p>
        </Dragger>
      </Card>

      {/* ── 파일별 진행바 + 결과 목록 ─────────────────────────────── */}
      {files.length > 0 && (
        <Card
          title={`업로드 이력 (${files.length}건)`}
          extra={
            <Button
              size="small"
              onClick={() => setFiles([])}
              disabled={isUploading}
            >
              이력 초기화
            </Button>
          }
        >
          <List
            dataSource={files}
            renderItem={(f) => (
              <List.Item
                key={f.id}
                style={{
                  borderLeft: `3px solid ${CATEGORY_ACCENT[f.category] ?? '#d9d9d9'}`,
                  paddingLeft: 12,
                  marginBottom: 4,
                  borderRadius: '0 6px 6px 0',
                  background:
                    f.status === 'success' ? '#f6ffed'
                    : f.status === 'failed' ? '#fff1f0'
                    : '#fff',
                  transition: 'background 0.3s',
                }}
                extra={
                  f.status !== 'uploading' && (
                    <Space size={4}>
                      {f.status === 'success' && onNavigate && (
                        <Button
                          type="link"
                          size="small"
                          icon={<SearchOutlined />}
                          onClick={() => {
                            const stem = f.filename.replace(/\.[^.]+$/, '')
                            localStorage.setItem('km_launch_query', stem)
                            onNavigate('search')
                          }}
                          style={{ padding: '0 4px', fontSize: 12 }}
                        >
                          검색
                        </Button>
                      )}
                      <Button
                        type="text"
                        size="small"
                        icon={<CloseOutlined />}
                        onClick={() => removeFile(f.id)}
                        style={{ color: '#bbb' }}
                      />
                    </Space>
                  )
                }
              >
                <List.Item.Meta
                  avatar={
                    <Avatar
                      icon={
                        (f.status === 'uploading' || f.status === 'parsing') ? <LoadingOutlined /> :
                        f.status === 'success'    ? <CheckCircleOutlined /> :
                                                    <CloseCircleOutlined />
                      }
                      style={{
                        backgroundColor:
                          f.status === 'uploading' ? '#1677ff' :
                          f.status === 'parsing'   ? '#fa8c16' :
                          f.status === 'success'   ? '#52c41a' : '#ff4d4f',
                      }}
                    />
                  }
                  title={
                    <Space wrap size={4}>
                      {getFileIcon(f.filename)}
                      <Tooltip title={f.filename}>
                        <Text strong style={{ maxWidth: 260, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>
                          {f.filename}
                        </Text>
                      </Tooltip>
                      <Tag color={CATEGORY_COLOR[f.category]}>
                        {CATEGORY_LABEL[f.category]}
                      </Tag>
                      {f.size > 0 && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {formatSize(f.size)}
                        </Text>
                      )}
                      {f.status === 'success' && (
                        <Text type="secondary" style={{ fontSize: 12 }}>{f.pages}페이지</Text>
                      )}
                      {f.status === 'failed' && (
                        <Text type="danger" style={{ fontSize: 12 }}>{f.error}</Text>
                      )}
                    </Space>
                  }
                  description={
                    f.status === 'uploading' ? (
                      <Progress
                        percent={f.percent}
                        size="small"
                        status="active"
                        style={{ marginBottom: 0, maxWidth: 400 }}
                      />
                    ) : f.status === 'parsing' ? (
                      <span style={{
                        display: 'inline-block',
                        padding: '1px 10px',
                        borderRadius: 10,
                        fontSize: 11,
                        fontWeight: 600,
                        background: '#fff7e6',
                        color: '#d46b08',
                        border: '1px solid #ffd591',
                      }}>
                        <LoadingOutlined style={{ marginRight: 5 }} />
                        파싱 중… (대용량 문서는 시간이 걸릴 수 있습니다)
                      </span>
                    ) : (
                      <span style={{
                        display: 'inline-block',
                        padding: '1px 10px',
                        borderRadius: 10,
                        fontSize: 11,
                        fontWeight: 600,
                        background: f.status === 'success' ? '#f6ffed' : '#fff1f0',
                        color: f.status === 'success' ? '#389e0d' : '#cf1322',
                        border: `1px solid ${f.status === 'success' ? '#b7eb8f' : '#ffa39e'}`,
                      }}>
                        {f.status === 'success' ? '✓ 파싱 완료' : '✗ 파싱 실패'}
                      </span>
                    )
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}
    </div>
  )
}
