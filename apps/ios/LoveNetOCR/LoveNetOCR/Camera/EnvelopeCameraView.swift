import SwiftUI
import AVFoundation
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "EnvelopeCamera")

/// 奉獻袋掃描相機視圖
struct EnvelopeCameraView: View {
    /// 拍照完成回調
    let onCapture: (UIImage) -> Void
    /// 取消回調
    let onDismiss: () -> Void

    @StateObject private var cameraManager = CameraManager()
    @State private var isCapturing = false
    @State private var showPermissionAlert = false
    @State private var viewSize: CGSize = .zero

    // MARK: - Auto Capture

    /// 是否啟用自動拍照
    @State private var isAutoCaptureEnabled = true

    /// 條件滿足開始時間
    @State private var stableStartTime: Date?

    /// 自動拍照倒數進度（0-1）
    @State private var autoCaptureProgress: Double = 0

    /// 自動拍照所需穩定時間（秒）
    private let requiredStableDuration: TimeInterval = 1.0

    /// 倒數更新計時器
    @State private var countdownTimer: Timer?

    // MARK: - Orange Envelope Mode

    /// 是否啟用橘色信封偵測模式
    @State private var isOrangeEnvelopeModeEnabled = true

    // MARK: - Perspective Correction

    /// 是否啟用透視校正
    @State private var isPerspectiveCorrectionEnabled = true

    /// 拍照時的偵測結果快照（用於透視校正）
    @State private var captureDetectionSnapshot: EnvelopeDetectionResult?

    /// 綜合判斷是否準備好拍照（考慮橘色模式）
    private var isReadyForCapture: Bool {
        let result = cameraManager.detectionResult
        if isOrangeEnvelopeModeEnabled {
            return result.isReadyForOrangeCapture
        } else {
            return result.isReadyForCapture
        }
    }

    /// 根據偵測結果決定引導框狀態
    private var guideState: GuideFrameState {
        let result = cameraManager.detectionResult

        if isReadyForCapture {
            return .ready
        } else if result.isDetected {
            return .adjusting
        } else {
            return .searching
        }
    }

    /// 狀態提示訊息
    private var statusMessage: String {
        let result = cameraManager.detectionResult

        if !result.isDetected {
            return isOrangeEnvelopeModeEnabled ? "請將橘色信封對準引導框" : "請將信封對準引導框"
        }

        // 根據各項條件給出具體提示
        if result.distanceStatus == .tooFar {
            return "請靠近一點"
        } else if result.distanceStatus == .tooClose {
            return "請遠離一點"
        } else if result.tiltAngle >= 10 {
            return "請將信封擺正"
        } else if !result.isAligned {
            return "請將信封移到框內"
        } else if result.brightnessScore < 0.2 {
            return "光線不足，請找亮一點的地方"
        } else if result.brightnessScore > 0.9 {
            return "光線過強，請避開直射光"
        } else if result.sharpnessScore < 50 {
            return "畫面模糊，請保持穩定"
        } else if isOrangeEnvelopeModeEnabled && !result.isOrangeEnvelope {
            return "未偵測到橘色信封"
        } else if cameraManager.isAdjustingFocus {
            return "對焦中..."
        } else if autoCaptureProgress > 0 {
            return "保持不動..."
        } else {
            return "位置正確，請拍照"
        }
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // 相機預覽層
                CameraPreviewView(
                    session: cameraManager.captureSession,
                    onTapToFocus: { point in
                        cameraManager.focus(at: point)
                    }
                )
                .ignoresSafeArea()

                // 引導框覆蓋層
                GuideOverlayView(state: guideState)

                // 十字輔助線（傾斜時顯示）
                let guideRect = calculateGuideRect(in: geometry.size)
                CrosshairOverlayView(
                    guideRect: guideRect,
                    tiltAngle: cameraManager.detectionResult.tiltAngle,
                    tiltDirection: cameraManager.detectionResult.tiltDirection,
                    showCrosshair: cameraManager.detectionResult.isDetected &&
                                   cameraManager.detectionResult.tiltAngle >= 3
                )


                // 左側工具列（X 按鈕 + 選項）
                leftSidebar

                // 拍照按鈕（Y軸在橘框底部與螢幕底部的中間）
                captureButton
                    .position(x: geometry.size.width / 2, y: guideRect.maxY + (geometry.size.height - guideRect.maxY) / 2)

                // 調試：顯示中心點座標與十字線
                DebugCenterOverlay(
                    detectionResult: cameraManager.detectionResult,
                    guideRect: guideRect,
                    viewSize: geometry.size
                )

                // 5 行狀態面板（貼近螢幕底部）
                VStack {
                    Spacer()
                    DetectionStatusView(
                        result: cameraManager.detectionResult,
                        isAdjustingFocus: cameraManager.isAdjustingFocus
                    )
                    .padding(.horizontal, 2)
                    .padding(.bottom, 4)
                }

                // 自動拍照倒數圈
                if autoCaptureProgress > 0 && isAutoCaptureEnabled {
                    autoCaptureCountdownOverlay
                }

                // 拍照中的覆蓋層
                if isCapturing {
                    Color.black.opacity(0.3)
                        .ignoresSafeArea()
                    ProgressView()
                        .scaleEffect(1.5)
                        .tint(.white)
                }
            }
            .onAppear {
                viewSize = geometry.size
                // 設定引導框位置給偵測器
                let guideRect = calculateGuideRect(in: geometry.size)
                cameraManager.setGuideRect(guideRect, in: geometry.size)
            }
            .onChange(of: geometry.size) { _, newSize in
                viewSize = newSize
                let guideRect = calculateGuideRect(in: newSize)
                cameraManager.setGuideRect(guideRect, in: newSize)
            }
        }
        .statusBarHidden(true)
        .task {
            await setupCamera()
        }
        .onDisappear {
            cameraManager.stop()
            countdownTimer?.invalidate()
        }
        .onChange(of: isReadyForCapture) { _, isReady in
            handleAutoCaptureStateChange(isReady: isReady)
        }
        .alert("需要相機權限", isPresented: $showPermissionAlert) {
            Button("前往設定") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            Button("取消", role: .cancel) {
                onDismiss()
            }
        } message: {
            Text("請在設定中允許存取相機，以便掃描奉獻袋。")
        }
        .alert("相機錯誤", isPresented: Binding(
            get: { cameraManager.error != nil },
            set: { if !$0 { cameraManager.error = nil } }
        )) {
            Button("確定", role: .cancel) {
                onDismiss()
            }
        } message: {
            Text(cameraManager.error?.localizedDescription ?? "未知錯誤")
        }
    }

    // MARK: - Auto Capture Countdown Overlay

    private var autoCaptureCountdownOverlay: some View {
        ZStack {
            // 半透明背景
            Circle()
                .fill(Color.black.opacity(0.3))
                .frame(width: 120, height: 120)

            // 進度圈
            Circle()
                .stroke(Color.white.opacity(0.3), lineWidth: 8)
                .frame(width: 100, height: 100)

            Circle()
                .trim(from: 0, to: autoCaptureProgress)
                .stroke(Color.green, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                .frame(width: 100, height: 100)
                .rotationEffect(.degrees(-90))
                .animation(.linear(duration: 0.1), value: autoCaptureProgress)

            // 倒數文字
            VStack(spacing: 4) {
                Image(systemName: "camera.fill")
                    .font(.title)
                    .foregroundColor(.white)

                Text("保持不動")
                    .font(.caption)
                    .fontWeight(.medium)
                    .foregroundColor(.white)
            }
        }
    }

    // MARK: - Guide Rect Calculation

    /// 計算引導框位置（與 GuideOverlayView 保持一致）
    private func calculateGuideRect(in size: CGSize) -> CGRect {
        let envelopeAspectRatio: CGFloat = 0.5
        let guideHeightRatio: CGFloat = 0.7

        let guideHeight = size.height * guideHeightRatio
        let guideWidth = guideHeight * envelopeAspectRatio

        let maxWidth = size.width - 40
        let finalWidth = min(guideWidth, maxWidth)
        let finalHeight = finalWidth / envelopeAspectRatio

        let x = (size.width - finalWidth) / 2
        let y = (size.height - finalHeight) / 2 - 30 - size.height * 0.03

        return CGRect(x: x, y: y, width: finalWidth, height: finalHeight)
    }

    // MARK: - Left Sidebar

    private var leftSidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            // X 關閉按鈕（置頂）
            Button {
                onDismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.title2)
                    .foregroundColor(.white)
                    .frame(width: 44, height: 44)
                    .background(Color.black.opacity(0.5))
                    .clipShape(Circle())
            }
            .padding(.top, 60)
            .padding(.bottom, 16)

            // 選項按鈕（垂直排列）
            VStack(spacing: 12) {
                // 橘色信封模式開關
                Button {
                    isOrangeEnvelopeModeEnabled.toggle()
                    let generator = UIImpactFeedbackGenerator(style: .light)
                    generator.impactOccurred()
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: isOrangeEnvelopeModeEnabled ? "circle.fill" : "circle")
                            .font(.title3)
                        Text("橘色")
                            .font(.caption2)
                    }
                    .foregroundColor(isOrangeEnvelopeModeEnabled ? .orange : .gray)
                    .frame(width: 50, height: 50)
                    .background(Color.black.opacity(0.5))
                    .cornerRadius(10)
                }

                // 自動拍照開關
                Button {
                    isAutoCaptureEnabled.toggle()
                    if !isAutoCaptureEnabled {
                        resetAutoCapture()
                    }
                    let generator = UIImpactFeedbackGenerator(style: .light)
                    generator.impactOccurred()
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: isAutoCaptureEnabled ? "a.circle.fill" : "a.circle")
                            .font(.title3)
                        Text("自動")
                            .font(.caption2)
                    }
                    .foregroundColor(isAutoCaptureEnabled ? .green : .gray)
                    .frame(width: 50, height: 50)
                    .background(Color.black.opacity(0.5))
                    .cornerRadius(10)
                }

                // 透視校正開關
                Button {
                    isPerspectiveCorrectionEnabled.toggle()
                    let generator = UIImpactFeedbackGenerator(style: .light)
                    generator.impactOccurred()
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: isPerspectiveCorrectionEnabled ? "perspective" : "rectangle")
                            .font(.title3)
                        Text("校正")
                            .font(.caption2)
                    }
                    .foregroundColor(isPerspectiveCorrectionEnabled ? .cyan : .gray)
                    .frame(width: 50, height: 50)
                    .background(Color.black.opacity(0.5))
                    .cornerRadius(10)
                }
            }

            Spacer()
        }
        .padding(.leading, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Capture Button

    private var captureButton: some View {
        Button {
            capturePhoto()
        } label: {
            ZStack {
                // 外圈（縮小）
                Circle()
                    .stroke(Color.white, lineWidth: 3)
                    .frame(width: 56, height: 56)

                // 自動拍照進度圈（在按鈕上）
                if autoCaptureProgress > 0 && isAutoCaptureEnabled {
                    Circle()
                        .trim(from: 0, to: autoCaptureProgress)
                        .stroke(Color.green, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .frame(width: 56, height: 56)
                        .rotationEffect(.degrees(-90))
                }

                // 內圈（縮小）
                Circle()
                    .fill(isReadyForCapture ? Color.white : Color.white.opacity(0.6))
                    .frame(width: 44, height: 44)

                // 準備好時顯示勾選圖示
                if isReadyForCapture {
                    Image(systemName: "checkmark")
                        .font(.title3)
                        .foregroundColor(.green)
                }
            }
        }
        .disabled(isCapturing)
        .scaleEffect(isReadyForCapture ? 1.1 : 1.0)
        .animation(.easeInOut(duration: 0.2), value: isReadyForCapture)
    }

    // MARK: - Bottom Controls (舊版 - 保留備用)

    private var bottomControls: some View {
        VStack(spacing: 20) {
            Text(statusMessage)
                .font(.subheadline)
                .fontWeight(.medium)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(guideState.color.opacity(0.85))
                .cornerRadius(8)

            Button {
                capturePhoto()
            } label: {
                ZStack {
                    Circle()
                        .stroke(Color.white, lineWidth: 4)
                        .frame(width: 72, height: 72)
                    Circle()
                        .fill(guideState == .ready ? Color.white : Color.white.opacity(0.6))
                        .frame(width: 60, height: 60)
                }
            }
            .disabled(isCapturing)
            .padding(.bottom, 40)
        }
    }

    // MARK: - Auto Capture Logic

    private func handleAutoCaptureStateChange(isReady: Bool) {
        guard isAutoCaptureEnabled && !isCapturing else {
            resetAutoCapture()
            return
        }

        if isReady {
            // 開始計時
            if stableStartTime == nil {
                stableStartTime = Date()
                startCountdownTimer()
                logger.debug("Auto capture countdown started")

                // 開始時輕微震動
                let generator = UIImpactFeedbackGenerator(style: .light)
                generator.impactOccurred()
            }
        } else {
            // 條件不滿足，重置
            resetAutoCapture()
        }
    }

    private func startCountdownTimer() {
        countdownTimer?.invalidate()
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            Task { @MainActor in
                updateAutoCaptureProgress()
            }
        }
    }

    private func updateAutoCaptureProgress() {
        guard let startTime = stableStartTime else {
            autoCaptureProgress = 0
            return
        }

        let elapsed = Date().timeIntervalSince(startTime)
        autoCaptureProgress = min(elapsed / requiredStableDuration, 1.0)

        // 檢查是否達到觸發時間
        if elapsed >= requiredStableDuration {
            triggerAutoCapture()
        }
    }

    private func triggerAutoCapture() {
        guard !isCapturing else { return }

        logger.info("Auto capture triggered!")

        // 停止計時器
        countdownTimer?.invalidate()
        countdownTimer = nil

        // 強烈震動提示
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.success)

        // 執行拍照
        capturePhoto()
    }

    private func resetAutoCapture() {
        stableStartTime = nil
        autoCaptureProgress = 0
        countdownTimer?.invalidate()
        countdownTimer = nil
    }

    // MARK: - Methods

    private func setupCamera() async {
        // 檢查權限
        let hasPermission = await cameraManager.checkPermission()

        if !hasPermission {
            showPermissionAlert = true
            return
        }

        // 配置並啟動相機
        cameraManager.configure()

        // 等待一下讓 session 配置完成
        try? await Task.sleep(nanoseconds: 100_000_000)

        cameraManager.start()
        logger.info("Camera setup completed")
    }

    private func capturePhoto() {
        guard !isCapturing else { return }

        // 重置自動拍照狀態
        resetAutoCapture()

        // 保存當前偵測結果快照（用於透視校正）
        captureDetectionSnapshot = cameraManager.detectionResult

        isCapturing = true
        logger.info("Capturing photo...")

        // 觸覺回饋
        let generator = UIImpactFeedbackGenerator(style: .medium)
        generator.impactOccurred()

        Task {
            do {
                var image = try await cameraManager.capturePhoto()
                logger.info("Photo captured successfully: \(Int(image.size.width))x\(Int(image.size.height))")

                // 透視校正並裁切到信封區域
                if isPerspectiveCorrectionEnabled,
                   let snapshot = captureDetectionSnapshot,
                   snapshot.isDetected {
                    logger.info("Applying perspective correction and cropping...")
                    image = image.perspectiveCropped(with: snapshot)
                    logger.info("Perspective correction applied: \(Int(image.size.width))x\(Int(image.size.height))")
                }

                await MainActor.run {
                    isCapturing = false
                    captureDetectionSnapshot = nil
                    onCapture(image)
                }
            } catch {
                logger.error("Capture failed: \(error.localizedDescription)")
                await MainActor.run {
                    isCapturing = false
                    captureDetectionSnapshot = nil
                }
            }
        }
    }
}

// MARK: - Status Indicator

/// 狀態指示器元件
struct StatusIndicator: View {
    let icon: String
    let label: String
    let isActive: Bool
    var activeColor: Color = .green

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.caption)
            Text(label)
                .font(.caption)
        }
        .foregroundColor(isActive ? activeColor : .yellow)
    }
}

// MARK: - Debug Center Overlay

/// 調試用：顯示偵測中心與引導框中心的十字線
struct DebugCenterOverlay: View {
    let detectionResult: EnvelopeDetectionResult
    let guideRect: CGRect
    let viewSize: CGSize

    var body: some View {
        ZStack {
            // 十字線與邊框繪製
            Canvas { context, size in
                // 引導框（藍色）- 十字線 + 邊框
                let guideCenterX = guideRect.midX
                let guideCenterY = guideRect.midY

                drawCrosshair(
                    context: context,
                    center: CGPoint(x: guideCenterX, y: guideCenterY),
                    color: .blue,
                    size: size
                )

                // 引導框邊框（藍色）
                let guideRectPath = Path(guideRect)
                context.stroke(guideRectPath, with: .color(.blue), lineWidth: 2)

                // 偵測到的信封（綠色）- 十字線 + 邊框
                if detectionResult.isDetected, let boundingBox = detectionResult.boundingBox {
                    // Vision 座標轉 UIKit 座標
                    let detectedCenterX = boundingBox.midX * size.width
                    let detectedCenterY = (1 - boundingBox.midY) * size.height

                    drawCrosshair(
                        context: context,
                        center: CGPoint(x: detectedCenterX, y: detectedCenterY),
                        color: .green,
                        size: size
                    )

                    // boundingBox 邊框（綠色）- Vision 座標轉 UIKit
                    let boxX = boundingBox.minX * size.width
                    let boxY = (1 - boundingBox.maxY) * size.height
                    let boxWidth = boundingBox.width * size.width
                    let boxHeight = boundingBox.height * size.height
                    let uiKitBoundingBox = CGRect(x: boxX, y: boxY, width: boxWidth, height: boxHeight)

                    let boundingBoxPath = Path(uiKitBoundingBox)
                    context.stroke(boundingBoxPath, with: .color(.green), lineWidth: 2)
                }
            }

            // 中央顯示座標數值
            VStack(spacing: 4) {
                // 引導框中心（藍色，正規化座標）
                let guideNormX = guideRect.midX / viewSize.width
                let guideNormY = 1 - (guideRect.midY / viewSize.height)  // 轉為 Vision 座標
                Text("引導: (\(String(format: "%.3f", guideNormX)), \(String(format: "%.3f", guideNormY)))")
                    .font(.caption)
                    .foregroundColor(.blue)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(Color.black.opacity(0.7))
                    .cornerRadius(4)

                // 偵測到的信封中心（綠色）
                if detectionResult.isDetected, let boundingBox = detectionResult.boundingBox {
                    Text("偵測: (\(String(format: "%.3f", boundingBox.midX)), \(String(format: "%.3f", boundingBox.midY)))")
                        .font(.caption)
                        .foregroundColor(.green)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color.black.opacity(0.7))
                        .cornerRadius(4)
                } else {
                    Text("偵測: --")
                        .font(.caption)
                        .foregroundColor(.gray)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color.black.opacity(0.7))
                        .cornerRadius(4)
                }
            }
        }
        .allowsHitTesting(false)
    }

    private func drawCrosshair(context: GraphicsContext, center: CGPoint, color: Color, size: CGSize) {
        let lineLength: CGFloat = 60

        // 橫線
        var hPath = Path()
        hPath.move(to: CGPoint(x: center.x - lineLength, y: center.y))
        hPath.addLine(to: CGPoint(x: center.x + lineLength, y: center.y))

        context.stroke(hPath, with: .color(color), lineWidth: 2)

        // 直線
        var vPath = Path()
        vPath.move(to: CGPoint(x: center.x, y: center.y - lineLength))
        vPath.addLine(to: CGPoint(x: center.x, y: center.y + lineLength))

        context.stroke(vPath, with: .color(color), lineWidth: 2)
    }
}

// MARK: - Detected Rectangle View

/// 顯示偵測到的矩形（調試用）
struct DetectedRectangleView: View {
    let corners: [CGPoint]
    let viewSize: CGSize

    var body: some View {
        Canvas { context, size in
            guard corners.count == 4 else { return }

            // 將正規化座標轉換為視圖座標
            let points = corners.map { point in
                CGPoint(
                    x: point.x * size.width,
                    y: point.y * size.height
                )
            }

            var path = Path()
            path.move(to: points[0])
            for i in 1..<points.count {
                path.addLine(to: points[i])
            }
            path.closeSubpath()

            context.stroke(
                path,
                with: .color(.cyan.opacity(0.8)),
                style: StrokeStyle(lineWidth: 2, dash: [5, 5])
            )
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Preview

#Preview {
    EnvelopeCameraView(
        onCapture: { _ in },
        onDismiss: { }
    )
}
