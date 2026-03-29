import React from 'react'
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Alert,
  Image,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'
import type { RootStackParamList } from '../navigation/AppNavigator'

type Props = NativeStackScreenProps<RootStackParamList, 'Camera'>

export function CameraScreen({ navigation }: Props) {
  const pickImage = async (useCamera: boolean) => {
    // 請求權限
    if (useCamera) {
      const { status } = await ImagePicker.requestCameraPermissionsAsync()
      if (status !== 'granted') {
        Alert.alert('權限不足', '需要相機權限才能拍照')
        return
      }
    } else {
      const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync()
      if (status !== 'granted') {
        Alert.alert('權限不足', '需要相簿權限才能選擇照片')
        return
      }
    }

    const result = useCamera
      ? await ImagePicker.launchCameraAsync({
          mediaTypes: ['images'],
          quality: 0.8,
        })
      : await ImagePicker.launchImageLibraryAsync({
          mediaTypes: ['images'],
          quality: 0.8,
        })

    if (!result.canceled && result.assets.length > 0) {
      const asset = result.assets[0]
      const filename =
        asset.fileName ?? `scan_${Date.now()}.jpg`
      navigation.navigate('Upload', {
        imageUri: asset.uri,
        filename,
      })
    }
  }

  return (
    <View style={styles.container}>
      {/* Hero 區域 */}
      <View style={styles.hero}>
        <Text style={styles.icon}>📷</Text>
        <Text style={styles.title}>GLM-OCR 手寫辨識</Text>
        <Text style={styles.subtitle}>
          拍照或選取含有手寫文字的圖片{'\n'}系統將自動辨識所有文字內容
        </Text>
      </View>

      {/* 按鈕區域 */}
      <View style={styles.actions}>
        <TouchableOpacity
          style={styles.primaryBtn}
          activeOpacity={0.8}
          onPress={() => pickImage(true)}>
          <Text style={styles.primaryBtnText}>📸  拍照掃描</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.secondaryBtn}
          activeOpacity={0.8}
          onPress={() => pickImage(false)}>
          <Text style={styles.secondaryBtnText}>🖼  從相簿選擇</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0f172a',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  hero: {
    alignItems: 'center',
    marginBottom: 48,
  },
  icon: {
    fontSize: 64,
    marginBottom: 16,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: '#f1f5f9',
    marginBottom: 12,
  },
  subtitle: {
    fontSize: 15,
    color: '#94a3b8',
    textAlign: 'center',
    lineHeight: 22,
  },
  actions: {
    gap: 14,
  },
  primaryBtn: {
    backgroundColor: '#3b82f6',
    paddingVertical: 16,
    borderRadius: 14,
    alignItems: 'center',
  },
  primaryBtnText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '600',
  },
  secondaryBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 16,
    borderRadius: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#334155',
  },
  secondaryBtnText: {
    color: '#e2e8f0',
    fontSize: 17,
    fontWeight: '600',
  },
})
