import Vision
import CoreImage
import UIKit
import Accelerate
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "EnvelopeDetector")

/// 位置狀態
struct PositionStatus {
    let isAligned: Bool
    let needsMoveLeft: Bool
    let needsMoveRight: Bool
    let needsMoveUp: Bool
    let needsMoveDown: Bool

    var message: String {
        if isAligned {
            return "位置正確 ✓"
        }
        var hints: [String] = []
        if needsMoveLeft { hints.append("←") }
        if needsMoveRight { hints.append("→") }
        if needsMoveUp { hints.append("↑") }
        if needsMoveDown { hints.append("↓") }
        return "請移動 " + hints.joined(separator: " ")
    }

    static let unknown = PositionStatus(
        isAligned: false,
        needsMoveLeft: false,
        needsMoveRight: false,
        needsMoveUp: false,
        needsMoveDown: false
    )
}

/// 傾斜方向
enum TiltDirection {
    case level      // 水平
    case rotateLeft // 需向左轉（逆時針）
    case rotateRight // 需向右轉（順時針）

    var hint: String {
        switch self {
        case .level: return "水平 ✓"
        case .rotateLeft: return "請向左轉 ↺"
        case .rotateRight: return "請向右轉 ↻"
        }
    }
}

/// 信封偵測結果
struct EnvelopeDetectionResult {
    /// 是否偵測到矩形
    let isDetected: Bool

    /// 偵測到的矩形四角（正規化座標 0-1，Vision 座標系：左下為原點）
    let normalizedCorners: [CGPoint]?

    /// 轉換為 UIKit 座標系的角落（左上為原點）
    let uiKitCorners: [CGPoint]?

    /// 矩形的 bounding box（正規化座標）
    let boundingBox: CGRect?

    /// 是否對齊引導框
    let isAligned: Bool

    /// 位置狀態（包含方向提示）
    let positionStatus: PositionStatus

    /// 傾斜角度（度數，正值表示順時針傾斜）
    let tiltAngle: Double

    /// 傾斜方向
    let tiltDirection: TiltDirection

    /// 信封在畫面中的佔比（0-1）
    let coverageRatio: Double

    /// 亮度分數（0-1，越高越亮）
    let brightnessScore: Double

    /// 清晰度分數（Laplacian 變異數，越高越清晰）
    let sharpnessScore: Double

    /// 偵測信心度
    let confidence: Float

    /// 橘色分數（0-1，越高表示越像橘色信封）
    let orangeScore: Double

    /// 是否為橘色信封（放寬：5% 橘色像素即可）
    var isOrangeEnvelope: Bool {
        orangeScore > 0.05
    }

    /// 距離狀態（放寬範圍）
    var distanceStatus: DistanceStatus {
        if coverageRatio < 0.08 {
            return .tooFar
        } else if coverageRatio > 0.95 {
            return .tooClose
        } else {
            return .good
        }
    }

    /// 是否所有條件都滿足，可以拍照（大幅放寬門檻）
    var isReadyForCapture: Bool {
        isDetected &&
        positionStatus.isAligned &&
        tiltAngle < 12 &&
        distanceStatus == .good &&
        brightnessScore > 0.1 &&
        brightnessScore < 0.95 &&
        sharpnessScore > 5
    }

    /// 是否所有條件都滿足且為橘色信封
    var isReadyForOrangeCapture: Bool {
        isReadyForCapture && isOrangeEnvelope
    }

    /// 空結果
    static let empty = EnvelopeDetectionResult(
        isDetected: false,
        normalizedCorners: nil,
        uiKitCorners: nil,
        boundingBox: nil,
        isAligned: false,
        positionStatus: .unknown,
        tiltAngle: 0,
        tiltDirection: .level,
        coverageRatio: 0,
        brightnessScore: 0.5,
        sharpnessScore: 0,
        confidence: 0,
        orangeScore: 0
    )
}

/// 距離狀態
enum DistanceStatus {
    case tooFar
    case tooClose
    case good

    var message: String {
        switch self {
        case .tooFar: return "請靠近一點"
        case .tooClose: return "請遠離一點"
        case .good: return "距離適中"
        }
    }
}

/// 信封偵測器：使用 Vision Framework 偵測矩形
final class EnvelopeDetector {

    // MARK: - Properties

    /// 直式信封的長寬比範圍（寬/高）
    private let minAspectRatio: Float = 0.4  // 較窄
    private let maxAspectRatio: Float = 0.7  // 較寬

    /// 信封最小佔畫面比例
    private let minSize: Float = 0.2

    /// 信心度閾值（放寬）
    private let confidenceThreshold: Float = 0.4

    /// 引導框的正規化範圍（用於判斷對齊）
    private var guideRect: CGRect = .zero

    /// 對齊容許誤差（佔引導框寬度的比例）
    private let alignmentTolerance: CGFloat = 0.15

    // MARK: - Public Methods

    /// 設定引導框位置（正規化座標，UIKit 座標系）
    func setGuideRect(_ rect: CGRect) {
        // 轉換為 Vision 座標系（Y 軸翻轉）
        guideRect = CGRect(
            x: rect.minX,
            y: 1 - rect.maxY,
            width: rect.width,
            height: rect.height
        )
    }

    /// 偵測圖像中的信封
    func detect(in pixelBuffer: CVPixelBuffer) -> EnvelopeDetectionResult {
        // 計算亮度
        let brightness = calculateBrightness(from: pixelBuffer)

        // 計算清晰度
        let sharpness = calculateSharpness(from: pixelBuffer)

        // 建立矩形偵測請求
        let request = VNDetectRectanglesRequest()
        request.minimumAspectRatio = minAspectRatio
        request.maximumAspectRatio = maxAspectRatio
        request.minimumSize = minSize
        request.minimumConfidence = confidenceThreshold
        request.maximumObservations = 1

        // 執行偵測
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])

        do {
            try handler.perform([request])
        } catch {
            logger.error("Vision request failed: \(error.localizedDescription)")
            return EnvelopeDetectionResult(
                isDetected: false,
                normalizedCorners: nil,
                uiKitCorners: nil,
                boundingBox: nil,
                isAligned: false,
                positionStatus: .unknown,
                tiltAngle: 0,
                tiltDirection: .level,
                coverageRatio: 0,
                brightnessScore: brightness,
                sharpnessScore: sharpness,
                confidence: 0,
                orangeScore: 0
            )
        }

        // 處理結果
        guard let results = request.results, let rectangle = results.first else {
            return EnvelopeDetectionResult(
                isDetected: false,
                normalizedCorners: nil,
                uiKitCorners: nil,
                boundingBox: nil,
                isAligned: false,
                positionStatus: .unknown,
                tiltAngle: 0,
                tiltDirection: .level,
                coverageRatio: 0,
                brightnessScore: brightness,
                sharpnessScore: sharpness,
                confidence: 0,
                orangeScore: 0
            )
        }

        // 取得四角座標（Vision 座標系）
        let corners = [
            rectangle.topLeft,
            rectangle.topRight,
            rectangle.bottomRight,
            rectangle.bottomLeft
        ]

        // 轉換為 UIKit 座標系
        let uiKitCorners = corners.map { point in
            CGPoint(x: point.x, y: 1 - point.y)
        }

        // 計算 bounding box
        let boundingBox = rectangle.boundingBox

        // 計算佔比
        let coverageRatio = Double(boundingBox.width * boundingBox.height)

        // 計算傾斜角度與方向
        let (tiltAngle, tiltDirection) = calculateTiltAngleAndDirection(
            topLeft: rectangle.topLeft,
            topRight: rectangle.topRight
        )

        // 判斷位置狀態
        let positionStatus = calculatePositionStatus(boundingBox: boundingBox)

        // 計算橘色分數（在偵測到的矩形區域內）
        let orangeScore = calculateOrangeScore(from: pixelBuffer, in: boundingBox)

        logger.debug("Detected: coverage=\(coverageRatio, format: .fixed(precision: 2)), tilt=\(tiltAngle, format: .fixed(precision: 1))°, pos=\(positionStatus.isAligned)")

        return EnvelopeDetectionResult(
            isDetected: true,
            normalizedCorners: corners,
            uiKitCorners: uiKitCorners,
            boundingBox: boundingBox,
            isAligned: positionStatus.isAligned,
            positionStatus: positionStatus,
            tiltAngle: tiltAngle,
            tiltDirection: tiltDirection,
            coverageRatio: coverageRatio,
            brightnessScore: brightness,
            sharpnessScore: sharpness,
            confidence: rectangle.confidence,
            orangeScore: orangeScore
        )
    }

    // MARK: - Private Methods

    /// 計算傾斜角度和方向
    private func calculateTiltAngleAndDirection(topLeft: CGPoint, topRight: CGPoint) -> (Double, TiltDirection) {
        let deltaY = topRight.y - topLeft.y
        let deltaX = topRight.x - topLeft.x

        // 計算與水平線的夾角（帶正負號）
        let radians = atan2(deltaY, deltaX)
        let degrees = radians * 180 / .pi
        let absDegrees = abs(degrees)

        // 判斷傾斜方向
        let direction: TiltDirection
        if absDegrees < 3 {
            direction = .level
        } else if degrees > 0 {
            // 右邊較高，需要順時針轉（向右轉）
            direction = .rotateRight
        } else {
            // 左邊較高，需要逆時針轉（向左轉）
            direction = .rotateLeft
        }

        return (absDegrees, direction)
    }

    /// 計算位置狀態（包含方向提示）
    private func calculatePositionStatus(boundingBox: CGRect) -> PositionStatus
    {
        guard guideRect != .zero else
        {
            return .unknown
        }

        // 計算中心點偏移
        let detectedCenter = CGPoint(x: boundingBox.midX, y: boundingBox.midY)
        let guideCenter = CGPoint(x: guideRect.midX, y: guideRect.midY)

        let offsetX = detectedCenter.x - guideCenter.x
        let offsetY = detectedCenter.y - guideCenter.y

        // 容許誤差
        let toleranceX = guideRect.width * alignmentTolerance
        let toleranceY = guideRect.height * alignmentTolerance

        // 檢查大小是否接近
        let widthRatio = boundingBox.width / guideRect.width
        let heightRatio = boundingBox.height / guideRect.height
        let sizeMatch = widthRatio > 0.6 && widthRatio < 1.4 &&
                        heightRatio > 0.6 && heightRatio < 1.4

        // 判斷對齊狀態
        let isXAligned = abs(offsetX) < toleranceX
        let isYAligned = abs(offsetY) < toleranceY
        let isAligned = isXAligned && isYAligned && sizeMatch

        // 計算方向提示（Vision 座標系：Y 軸向上）
        // 當偵測到的信封中心在引導框中心的右邊，表示信封需要向左移動
        let needsMoveLeft = offsetX > toleranceX
        let needsMoveRight = offsetX < -toleranceX
        // Vision 座標 Y 向上，所以 offsetY > 0 表示信封太高，需要向下移
        let needsMoveDown = offsetY > toleranceY
        let needsMoveUp = offsetY < -toleranceY

        return PositionStatus(
            isAligned: isAligned,
            needsMoveLeft: needsMoveLeft,
            needsMoveRight: needsMoveRight,
            needsMoveUp: needsMoveUp,
            needsMoveDown: needsMoveDown
        )
    }

    /// 計算亮度（從 pixel buffer）
    private func calculateBrightness(from pixelBuffer: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)

        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            return 0.5
        }

        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let buffer = baseAddress.assumingMemoryBound(to: UInt8.self)

        // 取樣計算平均亮度（每隔 10 個像素取樣）
        var totalBrightness: Double = 0
        var sampleCount: Double = 0
        let step = 10

        for y in stride(from: 0, to: height, by: step) {
            for x in stride(from: 0, to: width, by: step) {
                let offset = y * bytesPerRow + x * 4

                // 假設 BGRA 格式
                let b = Double(buffer[offset])
                let g = Double(buffer[offset + 1])
                let r = Double(buffer[offset + 2])

                // 計算亮度 (ITU-R BT.601)
                let brightness = 0.299 * r + 0.587 * g + 0.114 * b
                totalBrightness += brightness
                sampleCount += 1
            }
        }

        return sampleCount > 0 ? (totalBrightness / sampleCount) / 255.0 : 0.5
    }

    /// 計算清晰度（使用 Laplacian 變異數）
    /// 變異數越高表示影像越清晰
    private func calculateSharpness(from pixelBuffer: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)

        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            return 0
        }

        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let buffer = baseAddress.assumingMemoryBound(to: UInt8.self)

        // 取中心區域進行分析（減少計算量）
        let sampleSize = 200
        let startX = max(0, (width - sampleSize) / 2)
        let startY = max(0, (height - sampleSize) / 2)
        let endX = min(width - 1, startX + sampleSize)
        let endY = min(height - 1, startY + sampleSize)

        // 使用 Laplacian 算子計算二階導數
        // Laplacian kernel: [0, 1, 0]
        //                   [1,-4, 1]
        //                   [0, 1, 0]
        var laplacianValues: [Double] = []
        laplacianValues.reserveCapacity((endX - startX - 2) * (endY - startY - 2))

        for y in (startY + 1)..<(endY - 1) {
            for x in (startX + 1)..<(endX - 1) {
                // 取灰階值（使用綠色通道作為近似）
                let center = Int(buffer[y * bytesPerRow + x * 4 + 1])
                let top = Int(buffer[(y - 1) * bytesPerRow + x * 4 + 1])
                let bottom = Int(buffer[(y + 1) * bytesPerRow + x * 4 + 1])
                let left = Int(buffer[y * bytesPerRow + (x - 1) * 4 + 1])
                let right = Int(buffer[y * bytesPerRow + (x + 1) * 4 + 1])

                // Laplacian = top + bottom + left + right - 4 * center
                let laplacian = Double(top + bottom + left + right - 4 * center)
                laplacianValues.append(laplacian)
            }
        }

        guard !laplacianValues.isEmpty else { return 0 }

        // 計算變異數
        let count = Double(laplacianValues.count)
        let mean = laplacianValues.reduce(0, +) / count
        let variance = laplacianValues.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / count

        // 返回標準差作為清晰度指標（更易於設定閾值）
        return sqrt(variance)
    }

    /// 計算橘色分數（在指定區域內）
    /// - Parameters:
    ///   - pixelBuffer: 像素緩衝區
    ///   - region: 正規化區域（Vision 座標系）
    /// - Returns: 橘色像素佔比（0-1）
    private func calculateOrangeScore(from pixelBuffer: CVPixelBuffer, in region: CGRect) -> Double {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)

        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            return 0
        }

        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let buffer = baseAddress.assumingMemoryBound(to: UInt8.self)

        // 計算實際像素區域（Vision 座標系 Y 軸翻轉）
        let startX = Int(region.minX * CGFloat(width))
        let endX = Int(region.maxX * CGFloat(width))
        let startY = Int((1 - region.maxY) * CGFloat(height))
        let endY = Int((1 - region.minY) * CGFloat(height))

        var orangePixelCount: Double = 0
        var totalPixelCount: Double = 0
        let step = 5  // 取樣間隔

        for y in stride(from: max(0, startY), to: min(height, endY), by: step) {
            for x in stride(from: max(0, startX), to: min(width, endX), by: step) {
                let offset = y * bytesPerRow + x * 4

                // BGRA 格式
                let b = Double(buffer[offset]) / 255.0
                let g = Double(buffer[offset + 1]) / 255.0
                let r = Double(buffer[offset + 2]) / 255.0

                // RGB 轉 HSV
                let (h, s, v) = rgbToHSV(r: r, g: g, b: b)

                // 判斷是否為橘色
                // 橘色 Hue 範圍：約 10-40 度（0-360 度制）
                // 轉換為 0-1：約 0.028-0.111
                let isOrange = h >= 0.02 && h <= 0.12 &&  // Hue: 橘色範圍
                               s >= 0.3 &&                 // Saturation: 夠飽和
                               v >= 0.3                    // Value: 不太暗

                if isOrange {
                    orangePixelCount += 1
                }
                totalPixelCount += 1
            }
        }

        let score = totalPixelCount > 0 ? orangePixelCount / totalPixelCount : 0
        return score
    }

    /// RGB 轉 HSV
    /// - Parameters:
    ///   - r: 紅色（0-1）
    ///   - g: 綠色（0-1）
    ///   - b: 藍色（0-1）
    /// - Returns: (H, S, V)，H 為 0-1（對應 0-360 度）
    private func rgbToHSV(r: Double, g: Double, b: Double) -> (h: Double, s: Double, v: Double) {
        let maxVal = max(r, g, b)
        let minVal = min(r, g, b)
        let delta = maxVal - minVal

        // Value
        let v = maxVal

        // Saturation
        let s: Double
        if maxVal == 0 {
            s = 0
        } else {
            s = delta / maxVal
        }

        // Hue
        var h: Double = 0
        if delta > 0 {
            if maxVal == r {
                h = (g - b) / delta
                if g < b {
                    h += 6
                }
            } else if maxVal == g {
                h = 2 + (b - r) / delta
            } else {
                h = 4 + (r - g) / delta
            }
            h /= 6  // 正規化到 0-1
        }

        return (h, s, v)
    }
}
