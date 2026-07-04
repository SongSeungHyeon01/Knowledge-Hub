// main.jsx — React 앱의 시작점
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import 'antd/dist/reset.css'

// QueryClient: 서버 데이터 캐싱/갱신을 관리하는 객체
const queryClient = new QueryClient()

ReactDOM.createRoot(document.getElementById('root')).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
)
