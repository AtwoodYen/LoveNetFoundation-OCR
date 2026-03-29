import React from 'react'
import { View, Text, StyleSheet } from 'react-native'
import type { TaskStatus } from '../api/types'

const STATUS_MAP: Record<TaskStatus, { label: string; bg: string; fg: string }> = {
  pending: { label: '等待中', bg: '#fef3c7', fg: '#92400e' },
  processing: { label: '辨識中', bg: '#dbeafe', fg: '#1e40af' },
  completed: { label: '已完成', bg: '#d1fae5', fg: '#065f46' },
  failed: { label: '失敗', bg: '#fee2e2', fg: '#991b1b' },
}

export function StatusBadge({ status }: { status: TaskStatus | null }) {
  if (!status) return null
  const cfg = STATUS_MAP[status] ?? STATUS_MAP.pending

  return (
    <View style={[styles.badge, { backgroundColor: cfg.bg }]}>
      <Text style={[styles.text, { color: cfg.fg }]}>{cfg.label}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 12,
    alignSelf: 'center',
  },
  text: {
    fontSize: 13,
    fontWeight: '600',
  },
})
