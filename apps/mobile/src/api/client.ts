import { API_BASE_URL } from '../constants/config'
import type { ApiResponse, UploadTaskData, TaskStatusData } from './types'

/**
 * 上傳照片至後端 OCR
 * React Native 的 FormData 接受 { uri, type, name } 作為檔案值
 */
export async function uploadPhoto(
  imageUri: string,
  filename: string,
  mimeType = 'image/jpeg',
): Promise<UploadTaskData> {
  const formData = new FormData()
  formData.append('file', {
    uri: imageUri,
    type: mimeType,
    name: filename,
  } as any)
  formData.append('processing_mode', 'pipeline')

  const res = await fetch(`${API_BASE_URL}/tasks/upload`, {
    method: 'POST',
    body: formData,
    // React Native 會自動設定 multipart/form-data boundary
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Upload failed (${res.status}): ${text}`)
  }

  const json: ApiResponse<UploadTaskData> = await res.json()
  if (!json.success) {
    throw new Error(json.message || 'Upload failed')
  }
  return json.data
}

/**
 * 查詢任務狀態
 */
export async function getTaskStatus(taskId: string): Promise<TaskStatusData> {
  const res = await fetch(`${API_BASE_URL}/tasks/${taskId}`)

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Query failed (${res.status}): ${text}`)
  }

  const json: ApiResponse<TaskStatusData> = await res.json()
  if (!json.success) {
    throw new Error(json.message || 'Query task status failed')
  }
  return json.data
}
