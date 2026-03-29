import { useEffect, useRef, useState, useCallback } from 'react'
import { getTaskStatus } from '../api/client'
import type { TaskStatusData, TaskStatus } from '../api/types'

interface PollingState {
  status: TaskStatus | null
  progress: number
  currentStep: string | null
  result: TaskStatusData | null
  error: string | null
}

/**
 * 輪詢任務狀態，每 2 秒查詢一次
 * 任務完成或失敗後自動停止
 */
export function useTaskPolling(taskId: string | null) {
  const [state, setState] = useState<PollingState>({
    status: null,
    progress: 0,
    currentStep: null,
    result: null,
    error: null,
  })
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!taskId) return

    const poll = async () => {
      try {
        const data = await getTaskStatus(taskId)
        setState({
          status: data.status,
          progress: data.progress ?? 0,
          currentStep: data.current_step ?? null,
          result: data,
          error: data.error_message ?? null,
        })

        if (data.status === 'completed' || data.status === 'failed') {
          stop()
        }
      } catch (err: any) {
        setState(prev => ({
          ...prev,
          error: err.message ?? 'Polling error',
        }))
        stop()
      }
    }

    // 立即查詢一次
    poll()

    // 每 2 秒輪詢
    intervalRef.current = setInterval(poll, 2000)

    return () => stop()
  }, [taskId, stop])

  return state
}
