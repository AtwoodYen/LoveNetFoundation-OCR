import React, { useState, useEffect } from 'react'
import {
  View,
  Text,
  StyleSheet,
  Image,
  TouchableOpacity,
  ActivityIndicator,
  ScrollView,
} from 'react-native'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'
import type { RootStackParamList } from '../navigation/AppNavigator'
import { uploadPhoto } from '../api/client'
import { useTaskPolling } from '../hooks/useTaskPolling'
import { StatusBadge } from '../components/StatusBadge'

type Props = NativeStackScreenProps<RootStackParamList, 'Upload'>

export function UploadScreen({ route, navigation }: Props) {
  const { imageUri, filename } = route.params
  const [taskId, setTaskId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const polling = useTaskPolling(taskId)

  // 任務完成時自動跳轉結果頁
  useEffect(() => {
    if (polling.status === 'completed' && polling.result) {
      navigation.replace('Result', { taskData: polling.result })
    }
  }, [polling.status, polling.result, navigation])

  const handleUpload = async () => {
    setUploading(true)
    setUploadError(null)
    try {
      const data = await uploadPhoto(imageUri, filename)
      setTaskId(data.task_id)
    } catch (err: any) {
      setUploadError(err.message ?? '上傳失敗')
    } finally {
      setUploading(false)
    }
  }

  const isProcessing = taskId !== null && polling.status !== 'failed'
  const isFailed = polling.status === 'failed'

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}>
      {/* 圖片預覽 */}
      <Image source={{ uri: imageUri }} style={styles.preview} resizeMode="contain" />

      {/* 狀態區 */}
      <View style={styles.statusArea}>
        {!taskId && !uploading && !uploadError && (
          <TouchableOpacity
            style={styles.uploadBtn}
            activeOpacity={0.8}
            onPress={handleUpload}>
            <Text style={styles.uploadBtnText}>🚀  開始辨識</Text>
          </TouchableOpacity>
        )}

        {uploading && (
          <View style={styles.row}>
            <ActivityIndicator color="#3b82f6" />
            <Text style={styles.statusText}>正在上傳…</Text>
          </View>
        )}

        {uploadError && (
          <>
            <Text style={styles.errorText}>{uploadError}</Text>
            <TouchableOpacity
              style={styles.retryBtn}
              onPress={handleUpload}>
              <Text style={styles.retryBtnText}>重試</Text>
            </TouchableOpacity>
          </>
        )}

        {isProcessing && !uploading && (
          <View style={styles.processingArea}>
            <StatusBadge status={polling.status} />
            <View style={styles.row}>
              <ActivityIndicator color="#3b82f6" size="small" />
              <Text style={styles.statusText}>
                {polling.currentStep ?? '處理中…'}
                {polling.progress > 0 ? `  ${Math.round(polling.progress)}%` : ''}
              </Text>
            </View>
          </View>
        )}

        {isFailed && (
          <>
            <StatusBadge status="failed" />
            <Text style={styles.errorText}>
              {polling.error ?? '辨識失敗，請重試'}
            </Text>
            <TouchableOpacity
              style={styles.retryBtn}
              onPress={() => {
                setTaskId(null)
                setUploadError(null)
              }}>
              <Text style={styles.retryBtnText}>重試</Text>
            </TouchableOpacity>
          </>
        )}
      </View>

      {/* 返回按鈕 */}
      {!isProcessing && !uploading && (
        <TouchableOpacity
          style={styles.backBtn}
          onPress={() => navigation.goBack()}>
          <Text style={styles.backBtnText}>← 重新選擇</Text>
        </TouchableOpacity>
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0f172a',
  },
  content: {
    padding: 24,
    alignItems: 'center',
  },
  preview: {
    width: '100%',
    height: 360,
    borderRadius: 12,
    backgroundColor: '#1e293b',
    marginBottom: 24,
  },
  statusArea: {
    width: '100%',
    alignItems: 'center',
    gap: 12,
  },
  processingArea: {
    alignItems: 'center',
    gap: 12,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  statusText: {
    color: '#94a3b8',
    fontSize: 15,
  },
  errorText: {
    color: '#f87171',
    fontSize: 14,
    textAlign: 'center',
  },
  uploadBtn: {
    backgroundColor: '#3b82f6',
    paddingVertical: 16,
    paddingHorizontal: 48,
    borderRadius: 14,
  },
  uploadBtnText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '600',
  },
  retryBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    paddingHorizontal: 32,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#334155',
  },
  retryBtnText: {
    color: '#e2e8f0',
    fontSize: 15,
    fontWeight: '600',
  },
  backBtn: {
    marginTop: 24,
  },
  backBtnText: {
    color: '#64748b',
    fontSize: 14,
  },
})
