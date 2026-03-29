import React from 'react'
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native'
import Markdown from 'react-native-markdown-display'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'
import type { RootStackParamList } from '../navigation/AppNavigator'
import type { LayoutBlock } from '../api/types'

type Props = NativeStackScreenProps<RootStackParamList, 'Result'>

/** 判斷區塊是否為手寫文字 */
const HANDWRITING_LABELS = new Set(['handwriting', 'handwritten', 'hand_writing'])

function isHandwriting(block: LayoutBlock): boolean {
  return HANDWRITING_LABELS.has((block.layout_type ?? '').toLowerCase())
}

/** 移除 HTML tag，取純文字 */
function plainText(html: string): string {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}

export function ResultScreen({ route, navigation }: Props) {
  const { taskData } = route.params
  const layout = taskData.layout ?? []
  const handwrittenBlocks = layout.filter(isHandwriting)
  const markdown = taskData.full_markdown ?? ''
  const meta = taskData.metadata

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* 摘要 */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>📋  辨識摘要</Text>
        {meta?.original_filename && (
          <Text style={styles.metaRow}>檔名：{meta.original_filename}</Text>
        )}
        {meta?.total_pages != null && (
          <Text style={styles.metaRow}>總頁數：{meta.total_pages}</Text>
        )}
        <Text style={styles.metaRow}>區塊數：{layout.length}</Text>
        <Text style={styles.metaRow}>手寫區塊：{handwrittenBlocks.length}</Text>
      </View>

      {/* 手寫文字區塊 */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>✍️  手寫文字</Text>
        {handwrittenBlocks.length === 0 ? (
          <Text style={styles.hint}>
            未偵測到獨立的手寫區塊。{'\n'}
            若模型將手寫併入一般文字，請參考下方完整結果。
          </Text>
        ) : (
          handwrittenBlocks.map((b, i) => (
            <View key={b.block_id ?? i} style={styles.blockItem}>
              <Text style={styles.blockLabel}>
                #{i + 1}  頁 {b.page_index}
              </Text>
              <Text style={styles.blockText}>{plainText(b.block_content)}</Text>
            </View>
          ))
        )}
      </View>

      {/* 完整 Markdown 結果 */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>📄  完整辨識結果</Text>
        {markdown ? (
          <Markdown style={markdownStyles}>{markdown}</Markdown>
        ) : (
          <Text style={styles.hint}>無內容</Text>
        )}
      </View>

      {/* 提示 */}
      <Text style={styles.footerHint}>
        💡 前往 Web 後台可檢視所有任務並匯出 Excel
      </Text>

      {/* 操作按鈕 */}
      <TouchableOpacity
        style={styles.newScanBtn}
        activeOpacity={0.8}
        onPress={() => navigation.popToTop()}>
        <Text style={styles.newScanBtnText}>📷  繼續掃描</Text>
      </TouchableOpacity>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0f172a',
  },
  content: {
    padding: 20,
    paddingBottom: 48,
  },
  card: {
    backgroundColor: '#1e293b',
    borderRadius: 14,
    padding: 18,
    marginBottom: 16,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: '#f1f5f9',
    marginBottom: 12,
  },
  metaRow: {
    fontSize: 14,
    color: '#94a3b8',
    marginBottom: 4,
  },
  hint: {
    fontSize: 13,
    color: '#64748b',
    lineHeight: 20,
  },
  blockItem: {
    backgroundColor: '#0f172a',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  blockLabel: {
    fontSize: 12,
    fontWeight: '600',
    color: '#64748b',
    marginBottom: 4,
  },
  blockText: {
    fontSize: 15,
    color: '#e2e8f0',
    lineHeight: 22,
  },
  footerHint: {
    fontSize: 12,
    color: '#475569',
    textAlign: 'center',
    marginBottom: 16,
  },
  newScanBtn: {
    backgroundColor: '#3b82f6',
    paddingVertical: 16,
    borderRadius: 14,
    alignItems: 'center',
  },
  newScanBtnText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '600',
  },
})

const markdownStyles = StyleSheet.create({
  body: { color: '#cbd5e1', fontSize: 14, lineHeight: 22 },
  heading1: { color: '#f1f5f9', fontSize: 20, fontWeight: '700' as const, marginBottom: 8 },
  heading2: { color: '#f1f5f9', fontSize: 18, fontWeight: '600' as const, marginBottom: 6 },
  paragraph: { marginBottom: 8 },
  code_inline: { backgroundColor: '#334155', color: '#e2e8f0', paddingHorizontal: 4, borderRadius: 4 },
})
