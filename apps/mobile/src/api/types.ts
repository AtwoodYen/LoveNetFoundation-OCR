/** 後端統一回應格式 */
export interface ApiResponse<T> {
  success: boolean
  data: T
  message?: string | null
  error?: string | null
}

/** POST /tasks/upload 回傳 */
export interface UploadTaskData {
  task_id: string
  document_id: string
  status: string
  processing_mode: string
  priority: number
  created_at: string
}

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed'

/** Layout 區塊 */
export interface LayoutBlock {
  block_content: string
  layout_type: string
  bbox: [number, number, number, number] | null
  block_id: number
  page_index: number
}

/** GET /tasks/{task_id} 回傳 */
export interface TaskStatusData {
  task_id: string
  document_id: string
  status: TaskStatus
  progress?: number
  current_step?: string | null
  created_at?: string
  started_at?: string
  completed_at?: string
  error_message?: string | null
  processing_mode?: string
  priority?: number
  full_markdown?: string
  metadata?: {
    task_id?: string
    document_id?: string
    original_filename?: string
    processing_mode?: string
    total_pages?: number
  }
  layout?: LayoutBlock[]
}
