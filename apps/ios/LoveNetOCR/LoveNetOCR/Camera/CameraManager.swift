import AVFoundation
import UIKit
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "Camera")

/// 相機管理器：負責 AVCaptureSession 的初始化、啟動、停止與拍照
@MainActor
final class CameraManager: NSObject, ObservableObject {

    // MARK: - Published Properties

    /// 相機是否已準備好
    @Published private(set) var isSessionRunning = false

    /// 是否正在對焦中
    @Published private(set) var isAdjustingFocus = false

    /// 對焦是否完成（穩定）
    @Published private(set) var isFocused = false

    /// 信封偵測結果
    @Published private(set) var detectionResult: EnvelopeDetectionResult = .empty

    /// 錯誤訊息
    @Published var error: CameraError?

    // MARK: - AVFoundation Components

    let captureSession = AVCaptureSession()
    private var videoDeviceInput: AVCaptureDeviceInput?
    private let photoOutput = AVCapturePhotoOutput()
    private let videoDataOutput = AVCaptureVideoDataOutput()

    /// 用於配置 session 的背景佇列
    private let sessionQueue = DispatchQueue(label: "com.lovenet.ocr.camera.session")

    /// 用於視訊幀處理的背景佇列
    private let videoProcessingQueue = DispatchQueue(label: "com.lovenet.ocr.camera.video", qos: .userInitiated)

    /// 拍照完成的回調
    private var photoCaptureCompletion: ((Result<UIImage, CameraError>) -> Void)?

    // MARK: - Detection

    /// 信封偵測器
    private nonisolated(unsafe) let envelopeDetector = EnvelopeDetector()

    /// 上次分析時間（用於控制分析頻率）
    private nonisolated(unsafe) var lastAnalysisTime: Date = .distantPast

    /// 分析間隔（秒）
    private let analysisInterval: TimeInterval = 1.0 / 15.0  // 15 fps

    /// 是否啟用偵測
    nonisolated(unsafe) var isDetectionEnabled = true

    // MARK: - KVO Observation

    private var focusObservation: NSKeyValueObservation?

    // MARK: - Initialization

    override init() {
        super.init()
        logger.info("CameraManager initialized")
    }

    deinit {
        focusObservation?.invalidate()
    }

    // MARK: - Public Methods

    /// 檢查相機權限
    func checkPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            return true
        case .notDetermined:
            return await AVCaptureDevice.requestAccess(for: .video)
        case .denied, .restricted:
            return false
        @unknown default:
            return false
        }
    }

    /// 配置並啟動 capture session
    func configure() {
        sessionQueue.async { [weak self] in
            self?.configureSession()
        }
    }

    /// 啟動 session
    func start() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if !self.captureSession.isRunning {
                self.captureSession.startRunning()
                logger.info("Capture session started")
            }
            Task { @MainActor in
                self.isSessionRunning = self.captureSession.isRunning
            }
        }
    }

    /// 停止 session
    func stop() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if self.captureSession.isRunning {
                self.captureSession.stopRunning()
                logger.info("Capture session stopped")
            }
            Task { @MainActor in
                self.isSessionRunning = false
            }
        }
    }

    /// 設定引導框位置（用於對齊偵測）
    func setGuideRect(_ rect: CGRect, in viewSize: CGSize) {
        // 正規化座標
        let normalizedRect = CGRect(
            x: rect.minX / viewSize.width,
            y: rect.minY / viewSize.height,
            width: rect.width / viewSize.width,
            height: rect.height / viewSize.height
        )
        envelopeDetector.setGuideRect(normalizedRect)
    }

    /// 拍照
    func capturePhoto() async throws -> UIImage {
        return try await withCheckedThrowingContinuation { continuation in
            sessionQueue.async { [weak self] in
                guard let self = self else {
                    continuation.resume(throwing: CameraError.sessionNotRunning)
                    return
                }

                guard self.captureSession.isRunning else {
                    continuation.resume(throwing: CameraError.sessionNotRunning)
                    return
                }

                Task { @MainActor in
                    self.photoCaptureCompletion = { result in
                        switch result {
                        case .success(let image):
                            continuation.resume(returning: image)
                        case .failure(let error):
                            continuation.resume(throwing: error)
                        }
                    }
                }

                let settings = AVCapturePhotoSettings()
                settings.flashMode = .auto

                // 設定高品質輸出
                if self.photoOutput.availablePhotoCodecTypes.contains(.jpeg) {
                    settings.photoQualityPrioritization = .balanced
                }

                self.photoOutput.capturePhoto(with: settings, delegate: self)
                logger.info("Photo capture initiated")
            }
        }
    }

    /// 手動對焦到指定點
    func focus(at point: CGPoint) {
        sessionQueue.async { [weak self] in
            guard let device = self?.videoDeviceInput?.device else { return }

            do {
                try device.lockForConfiguration()

                if device.isFocusPointOfInterestSupported && device.isFocusModeSupported(.autoFocus) {
                    device.focusPointOfInterest = point
                    device.focusMode = .autoFocus
                }

                if device.isExposurePointOfInterestSupported && device.isExposureModeSupported(.autoExpose) {
                    device.exposurePointOfInterest = point
                    device.exposureMode = .autoExpose
                }

                device.unlockForConfiguration()
                logger.debug("Focus point set to: \(point.x), \(point.y)")
            } catch {
                logger.error("Failed to set focus: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - Private Methods

    private func configureSession() {
        captureSession.beginConfiguration()
        defer { captureSession.commitConfiguration() }

        // 設定 session preset（高品質照片）
        captureSession.sessionPreset = .photo

        // 1. 添加視訊輸入
        guard let videoDevice = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            Task { @MainActor in
                self.error = .cameraUnavailable
            }
            logger.error("No back camera available")
            return
        }

        do {
            let videoInput = try AVCaptureDeviceInput(device: videoDevice)

            if captureSession.canAddInput(videoInput) {
                captureSession.addInput(videoInput)
                videoDeviceInput = videoInput
                logger.info("Video input added")

                // 設定持續自動對焦
                try videoDevice.lockForConfiguration()
                if videoDevice.isFocusModeSupported(.continuousAutoFocus) {
                    videoDevice.focusMode = .continuousAutoFocus
                }
                if videoDevice.isExposureModeSupported(.continuousAutoExposure) {
                    videoDevice.exposureMode = .continuousAutoExposure
                }
                videoDevice.unlockForConfiguration()

                // 監聽對焦狀態
                setupFocusObserver(for: videoDevice)
            } else {
                Task { @MainActor in
                    self.error = .cannotAddInput
                }
                logger.error("Cannot add video input")
                return
            }
        } catch {
            Task { @MainActor in
                self.error = .cannotAddInput
            }
            logger.error("Failed to create video input: \(error.localizedDescription)")
            return
        }

        // 2. 添加照片輸出
        if captureSession.canAddOutput(photoOutput) {
            captureSession.addOutput(photoOutput)

            // 設定照片輸出
            photoOutput.isHighResolutionCaptureEnabled = true
            photoOutput.maxPhotoQualityPrioritization = .balanced

            logger.info("Photo output added")
        } else {
            Task { @MainActor in
                self.error = .cannotAddOutput
            }
            logger.error("Cannot add photo output")
            return
        }

        // 3. 添加視訊資料輸出（用於即時偵測）
        videoDataOutput.alwaysDiscardsLateVideoFrames = true
        videoDataOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        videoDataOutput.setSampleBufferDelegate(self, queue: videoProcessingQueue)

        if captureSession.canAddOutput(videoDataOutput) {
            captureSession.addOutput(videoDataOutput)
            logger.info("Video data output added")
        } else {
            logger.warning("Cannot add video data output (detection will be disabled)")
        }

        logger.info("Camera session configured successfully")
    }

    private func setupFocusObserver(for device: AVCaptureDevice) {
        focusObservation = device.observe(\.isAdjustingFocus, options: [.new]) { [weak self] device, change in
            let isAdjusting = change.newValue ?? false
            Task { @MainActor in
                self?.isAdjustingFocus = isAdjusting
                if !isAdjusting {
                    self?.isFocused = true
                    // 對焦完成後短暫延遲重置，準備下次偵測
                    try? await Task.sleep(nanoseconds: 500_000_000) // 0.5s
                    self?.isFocused = false
                }
            }
            logger.debug("Focus adjusting: \(isAdjusting)")
        }
    }
}

// MARK: - AVCapturePhotoCaptureDelegate

extension CameraManager: AVCapturePhotoCaptureDelegate {

    nonisolated func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        Task { @MainActor in
            if let error = error {
                logger.error("Photo capture error: \(error.localizedDescription)")
                photoCaptureCompletion?(.failure(.captureFailed))
                photoCaptureCompletion = nil
                return
            }

            guard let imageData = photo.fileDataRepresentation(),
                  let image = UIImage(data: imageData) else {
                logger.error("Failed to get image data")
                photoCaptureCompletion?(.failure(.captureFailed))
                photoCaptureCompletion = nil
                return
            }

            logger.info("Photo captured: \(Int(image.size.width))x\(Int(image.size.height))")
            photoCaptureCompletion?(.success(image))
            photoCaptureCompletion = nil
        }
    }
}

// MARK: - AVCaptureVideoDataOutputSampleBufferDelegate

extension CameraManager: AVCaptureVideoDataOutputSampleBufferDelegate {

    nonisolated func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        // 控制分析頻率
        let now = Date()
        guard now.timeIntervalSince(lastAnalysisTime) >= analysisInterval else { return }
        lastAnalysisTime = now

        // 檢查是否啟用偵測
        guard isDetectionEnabled else { return }

        // 使用 autoreleasepool 避免記憶體累積
        autoreleasepool {
            // 取得 pixel buffer
            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

            // 執行偵測
            let result = envelopeDetector.detect(in: pixelBuffer)

            // 更新結果到主執行緒
            Task { @MainActor in
                self.detectionResult = result
            }
        }
    }
}

// MARK: - Camera Error

enum CameraError: LocalizedError {
    case cameraUnavailable
    case permissionDenied
    case cannotAddInput
    case cannotAddOutput
    case sessionNotRunning
    case captureFailed

    var errorDescription: String? {
        switch self {
        case .cameraUnavailable:
            return "相機無法使用"
        case .permissionDenied:
            return "相機權限被拒絕，請至設定開啟"
        case .cannotAddInput:
            return "無法初始化相機輸入"
        case .cannotAddOutput:
            return "無法初始化相機輸出"
        case .sessionNotRunning:
            return "相機未啟動"
        case .captureFailed:
            return "拍照失敗"
        }
    }
}
