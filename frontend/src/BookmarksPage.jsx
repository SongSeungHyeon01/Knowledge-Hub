// BookmarksPage.jsx — 북마크 화면
// 로그인이 켜져 있으면 사용자별 북마크, 꺼져 있으면 "anonymous" 단일 공용 북마크로 동작(백엔드와 동일 원칙).

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Row, Col, Card, Tag, Button, Empty, Spin, Input, message } from 'antd'
import {
  StarFilled, DownloadOutlined, SearchOutlined,
  FilePdfOutlined, FileWordOutlined, FilePptOutlined, FileExcelOutlined,
  FileTextOutlined, FileOutlined, EyeOutlined,
} from '@ant-design/icons'
import axios from 'axios'

const { Title, Text } = Typography

const API = import.meta.env.VITE_API_URL

const CATEGORY_COLOR = {
  spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green',
}
const CATEGORY_LABEL = {
  spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서',
}
const FILE_TYPE_COLOR = {
  pdf: 'volcano', docx: 'geekblue', pptx: 'orange', ppt: 'orange',
  xlsx: 'green', xls: 'green', hwp: 'purple', hwpx: 'purple',
  txt: 'default', md: 'cyan',
}
const FILE_TYPE_ICON = {
  pdf:  <FilePdfOutlined  style={{ color: '#ff4d4f' }} />,
  docx: <FileWordOutlined style={{ color: '#1677ff' }} />,
  pptx: <FilePptOutlined  style={{ color: '#fa8c16' }} />,
  ppt:  <FilePptOutlined  style={{ color: '#fa8c16' }} />,
  xlsx: <FileExcelOutlined style={{ color: '#52c41a' }} />,
  xls:  <FileExcelOutlined style={{ color: '#52c41a' }} />,
  hwp:  <FileTextOutlined style={{ color: '#722ed1' }} />,
  hwpx: <FileTextOutlined style={{ color: '#722ed1' }} />,
}

export default function BookmarksPage({ onNavigate }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')

  const { data: bookmarks = [], isLoading } = useQuery({
    queryKey: ['bookmarks'],
    queryFn: () => axios.get(`${API}/bookmarks`).then(r => r.data),
  })

  const removeBookmark = async (docId) => {
    try {
      await axios.delete(`${API}/bookmarks/${docId}`)
      queryClient.invalidateQueries({ queryKey: ['bookmarks'] })
      message.success('북마크에서 제거했습니다')
    } catch {
      message.error('북마크 제거에 실패했습니다')
    }
  }

  const filtered = bookmarks.filter(b =>
    !search || (b.title || b.filename || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '28px 32px 40px' }}>
      <Title level={2} style={{ margin: '0 0 4px' }}>북마크</Title>
      <Text type="secondary">저장해둔 문서를 여기서 바로 다시 찾을 수 있습니다.</Text>

      <div style={{ margin: '20px 0' }}>
        <Input
          allowClear
          placeholder="북마크한 문서 제목으로 검색…"
          prefix={<SearchOutlined style={{ color: '#aaa' }} />}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 320 }}
        />
      </div>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 48 }}><Spin size="large" /></div>
      ) : filtered.length === 0 ? (
        <Empty
          description={bookmarks.length === 0 ? '아직 북마크한 문서가 없습니다' : '검색 결과가 없습니다'}
          style={{ margin: '48px 0' }}
        >
          {bookmarks.length === 0 && (
            <Button type="primary" icon={<SearchOutlined />} onClick={() => onNavigate?.('search')}>
              검색하러 가기
            </Button>
          )}
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {filtered.map(doc => (
            <Col key={doc.id} xs={24} sm={12} md={8} lg={6}>
              <Card
                size="small"
                styles={{ body: { padding: 14 } }}
                title={
                  <Text strong style={{ fontSize: 13 }} ellipsis={{ tooltip: doc.title || doc.filename }}>
                    {doc.title || doc.filename}
                  </Text>
                }
                extra={
                  <Button
                    type="text" size="small" icon={<StarFilled style={{ color: '#faad14' }} />}
                    onClick={() => removeBookmark(doc.id)}
                  />
                }
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                  {FILE_TYPE_ICON[doc.file_type] ?? <FileOutlined style={{ color: '#8c8c8c' }} />}
                  <Tag color={CATEGORY_COLOR[doc.category]} style={{ margin: 0 }}>
                    {CATEGORY_LABEL[doc.category] ?? doc.category}
                  </Tag>
                  {doc.file_type && (
                    <Tag color={FILE_TYPE_COLOR[doc.file_type] ?? 'default'} style={{ margin: 0 }}>
                      {doc.file_type.toUpperCase()}
                    </Tag>
                  )}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <EyeOutlined style={{ marginRight: 4 }} />{doc.view_count ?? 0}
                  </Text>
                  <Button
                    size="small" icon={<DownloadOutlined />}
                    href={`${API}/files/${doc.id}`} download type="primary" ghost
                  >
                    다운로드
                  </Button>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  )
}
