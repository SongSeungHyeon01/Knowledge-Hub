// MyPage.jsx — 내 정보(마이페이지)
// 로그인한 사용자 본인의 프로필 + 본인이 업로드한 문서를 직접 수정·삭제하는 화면.
// 관리자 전용 /admin/* 이 아니라 소유자 전용 /me/documents(GET·PATCH·DELETE)를 사용 —
// 백엔드에서 uploaded_by가 본인이 아니면(관리자가 아닌 한) 403으로 막는다.

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Avatar, Table, Tag, Button, Space, Modal, Input, Select, Popconfirm, message, Empty } from 'antd'
import {
  UserOutlined, EditOutlined, DeleteOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, EyeOutlined,
} from '@ant-design/icons'
import axios from 'axios'

const { Title, Text } = Typography

const API = import.meta.env.VITE_API_URL

const CATEGORY_LABEL = { spec: '사양서', research: '연구자료', presentation: '발표자료', report: '보고서' }
const CATEGORY_COLOR = { spec: 'blue', research: 'purple', presentation: 'cyan', report: 'green' }
const STATUS_TAG = {
  parsing: <Tag icon={<LoadingOutlined />} color="warning">처리 중</Tag>,
  success: <Tag icon={<CheckCircleOutlined />} color="success">완료</Tag>,
  failed:  <Tag icon={<CloseCircleOutlined />} color="error">실패</Tag>,
}

export default function MyPage({ me }) {
  const queryClient = useQueryClient()
  const [editDoc,      setEditDoc]      = useState(null)  // 수정 모달 대상 문서 (null이면 닫힘)
  const [editTitle,    setEditTitle]    = useState('')
  const [editCategory, setEditCategory] = useState(null)
  const [editMemo,      setEditMemo]    = useState('')
  const [saving, setSaving] = useState(false)

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ['my-documents'],
    queryFn: () => axios.get(`${API}/me/documents`).then(r => r.data),
  })

  const allCategories = [
    'spec', 'research', 'presentation', 'report',
    ...new Set(docs.map(d => d.category).filter(c => c && !(c in CATEGORY_LABEL))),
  ]

  const openEdit = (doc) => {
    setEditDoc(doc)
    setEditTitle(doc.title || '')
    setEditCategory(doc.category)
    setEditMemo(doc.memo || '')
  }

  const saveEdit = async () => {
    setSaving(true)
    try {
      await axios.patch(`${API}/me/documents/${editDoc.id}`, {
        title: editTitle, category: editCategory, memo: editMemo,
      })
      queryClient.invalidateQueries({ queryKey: ['my-documents'] })
      message.success('수정됐습니다')
      setEditDoc(null)
    } catch (e) {
      message.error(e?.response?.data?.detail || '수정에 실패했습니다')
    } finally {
      setSaving(false)
    }
  }

  const removeDoc = async (id) => {
    try {
      await axios.delete(`${API}/me/documents/${id}`)
      queryClient.invalidateQueries({ queryKey: ['my-documents'] })
      message.success('삭제됐습니다')
    } catch (e) {
      message.error(e?.response?.data?.detail || '삭제에 실패했습니다')
    }
  }

  const columns = [
    {
      title: '문서', dataIndex: 'title',
      render: (title, d) => (
        <div>
          <Text strong style={{ display: 'block' }}>{title || d.filename}</Text>
          {title && <Text type="secondary" style={{ fontSize: 11.5 }}>{d.filename}</Text>}
        </div>
      ),
    },
    { title: '카테고리', dataIndex: 'category', width: 110, render: (c) => <Tag color={CATEGORY_COLOR[c]}>{CATEGORY_LABEL[c] ?? c}</Tag> },
    { title: '형식', dataIndex: 'file_type', width: 80, render: (t) => t ? <Tag>{t.toUpperCase()}</Tag> : '—' },
    { title: '상태', dataIndex: 'status', width: 100, render: (s) => STATUS_TAG[s] ?? <Tag>{s}</Tag> },
    { title: '조회수', dataIndex: 'view_count', width: 80, render: (v) => <span><EyeOutlined style={{ marginRight: 4 }} />{v ?? 0}</span> },
    { title: '업로드일', dataIndex: 'uploaded_at', width: 160 },
    {
      title: '', width: 100,
      render: (_, d) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(d)} />
          <Popconfirm title="이 문서를 삭제할까요?" okText="삭제" cancelText="취소" onConfirm={() => removeDoc(d.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '28px 32px 40px' }}>
      <Title level={2} style={{ margin: '0 0 4px' }}>내 정보</Title>
      <Text type="secondary">내가 업로드한 문서를 여기서 직접 수정·삭제할 수 있습니다.</Text>

      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, margin: '22px 0 26px',
        padding: '16px 20px', background: '#fff', border: '1px solid #eef0f2', borderRadius: 10,
      }}>
        <Avatar size={52} src={me?.picture} icon={!me?.picture && <UserOutlined />} />
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{me?.name ?? me?.email}</div>
          <Text type="secondary" style={{ fontSize: 12.5 }}>{me?.email}</Text>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#1677ff' }}>{docs.length}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>업로드한 문서</Text>
        </div>
      </div>

      <Table
        columns={columns}
        dataSource={docs}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 10 }}
        locale={{ emptyText: <Empty description="아직 업로드한 문서가 없습니다" /> }}
      />

      <Modal
        title="문서 수정"
        open={!!editDoc}
        onCancel={() => setEditDoc(null)}
        onOk={saveEdit}
        okText="저장"
        confirmLoading={saving}
      >
        <div style={{ marginBottom: 12 }}>
          <Text strong style={{ fontSize: 12.5 }}>제목</Text>
          <Input
            style={{ marginTop: 6 }}
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            placeholder={editDoc?.filename}
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <Text strong style={{ fontSize: 12.5 }}>카테고리</Text>
          <Select
            style={{ marginTop: 6, width: '100%' }}
            value={editCategory}
            onChange={setEditCategory}
            options={allCategories.map(c => ({ value: c, label: CATEGORY_LABEL[c] ?? c }))}
          />
        </div>
        <div>
          <Text strong style={{ fontSize: 12.5 }}>특이사항</Text>
          <Input.TextArea
            style={{ marginTop: 6 }}
            rows={3}
            maxLength={1000}
            value={editMemo}
            onChange={(e) => setEditMemo(e.target.value)}
          />
        </div>
      </Modal>
    </div>
  )
}
