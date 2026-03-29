import React from 'react'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { CameraScreen } from '../screens/CameraScreen'
import { UploadScreen } from '../screens/UploadScreen'
import { ResultScreen } from '../screens/ResultScreen'
import type { TaskStatusData } from '../api/types'

export type RootStackParamList = {
  Camera: undefined
  Upload: { imageUri: string; filename: string }
  Result: { taskData: TaskStatusData }
}

const Stack = createNativeStackNavigator<RootStackParamList>()

export function AppNavigator() {
  return (
    <Stack.Navigator
      initialRouteName="Camera"
      screenOptions={{
        headerStyle: { backgroundColor: '#0f172a' },
        headerTintColor: '#f1f5f9',
        headerTitleStyle: { fontWeight: '600' },
        contentStyle: { backgroundColor: '#0f172a' },
      }}>
      <Stack.Screen
        name="Camera"
        component={CameraScreen}
        options={{ title: 'GLM-OCR', headerShown: false }}
      />
      <Stack.Screen
        name="Upload"
        component={UploadScreen}
        options={{ title: '上傳辨識' }}
      />
      <Stack.Screen
        name="Result"
        component={ResultScreen}
        options={{ title: '辨識結果', headerBackVisible: false }}
      />
    </Stack.Navigator>
  )
}
