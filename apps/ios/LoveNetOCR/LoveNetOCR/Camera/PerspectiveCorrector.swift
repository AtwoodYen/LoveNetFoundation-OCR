import CoreImage
import UIKit
import Vision
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "PerspectiveCorrector")

/// 透視校正器：將傾斜的信封圖片校正為正面視角
final class PerspectiveCorrector {

    // MARK: - Singleton

    static let shared = PerspectiveCorrector()

    private init() {}

    // MARK: - Properties

    private let context = CIContext(options: [
        .useSoftwareRenderer: false,
        .highQualityDownsample: true
    ])

    // MARK: - Public Methods

    /// 校正透視變形
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - corners: 四角座標（UIKit 座標系，正規化 0-1）
    ///             順序：左上、右上、右下、左下
    /// - Returns: 校正後的圖片
    func correct(image: UIImage, corners: [CGPoint]) -> UIImage? {
        guard corners.count == 4 else {
            logger.error("Invalid corners count: \(corners.count)")
            return nil
        }

        guard let cgImage = image.cgImage else {
            logger.error("Failed to get CGImage")
            return nil
        }

        let ciImage = CIImage(cgImage: cgImage)
        let imageSize = ciImage.extent.size

        // 將正規化座標轉換為實際像素座標
        // 注意：CIImage 座標系是左下為原點
        let pixelCorners = corners.map { point in
            CGPoint(
                x: point.x * imageSize.width,
                y: (1 - point.y) * imageSize.height  // 翻轉 Y 軸
            )
        }

        let topLeft = pixelCorners[0]
        let topRight = pixelCorners[1]
        let bottomRight = pixelCorners[2]
        let bottomLeft = pixelCorners[3]

        logger.debug("Correcting perspective with corners: TL=(\(topLeft.x), \(topLeft.y)), TR=(\(topRight.x), \(topRight.y)), BR=(\(bottomRight.x), \(bottomRight.y)), BL=(\(bottomLeft.x), \(bottomLeft.y))")

        // 計算目標尺寸（取四邊平均長度）
        let topWidth = distance(from: topLeft, to: topRight)
        let bottomWidth = distance(from: bottomLeft, to: bottomRight)
        let leftHeight = distance(from: topLeft, to: bottomLeft)
        let rightHeight = distance(from: topRight, to: bottomRight)

        let outputWidth = (topWidth + bottomWidth) / 2
        let outputHeight = (leftHeight + rightHeight) / 2

        logger.debug("Output size: \(outputWidth) x \(outputHeight)")

        // 使用 CIPerspectiveCorrection 濾鏡
        guard let filter = CIFilter(name: "CIPerspectiveCorrection") else {
            logger.error("Failed to create CIPerspectiveCorrection filter")
            return nil
        }

        filter.setValue(ciImage, forKey: kCIInputImageKey)
        filter.setValue(CIVector(cgPoint: topLeft), forKey: "inputTopLeft")
        filter.setValue(CIVector(cgPoint: topRight), forKey: "inputTopRight")
        filter.setValue(CIVector(cgPoint: bottomRight), forKey: "inputBottomRight")
        filter.setValue(CIVector(cgPoint: bottomLeft), forKey: "inputBottomLeft")

        guard let outputImage = filter.outputImage else {
            logger.error("Failed to get output image from filter")
            return nil
        }

        // 渲染結果
        let outputExtent = outputImage.extent
        guard let outputCGImage = context.createCGImage(outputImage, from: outputExtent) else {
            logger.error("Failed to create CGImage from output")
            return nil
        }

        let result = UIImage(cgImage: outputCGImage, scale: image.scale, orientation: .up)

        logger.info("Perspective correction completed: \(Int(result.size.width))x\(Int(result.size.height))")

        return result
    }

    /// 使用 Vision 偵測結果進行校正（只擺正，不裁切）
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - detectionResult: 信封偵測結果
    /// - Returns: 擺正後的完整圖片，若無法校正則返回原圖
    func correct(image: UIImage, detectionResult: EnvelopeDetectionResult) -> UIImage {
        guard detectionResult.isDetected,
              let uiKitCorners = detectionResult.uiKitCorners,
              uiKitCorners.count == 4 else {
            logger.warning("No valid detection result, returning original image")
            return image
        }

        // 確保角落順序正確：左上、右上、右下、左下
        let sortedCorners = sortCorners(uiKitCorners)

        // 只做旋轉擺正，不裁切
        guard let straightenedImage = straighten(image: image, corners: sortedCorners) else {
            logger.warning("Straightening failed, returning original image")
            return image
        }

        return straightenedImage
    }

    /// 只擺正圖片（旋轉），不裁切
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - corners: 四角座標（UIKit 座標系，正規化 0-1）
    ///             順序：左上、右上、右下、左下
    /// - Returns: 擺正後的完整圖片
    func straighten(image: UIImage, corners: [CGPoint]) -> UIImage? {
        guard corners.count == 4 else {
            logger.error("Invalid corners count: \(corners.count)")
            return nil
        }

        guard let cgImage = image.cgImage else {
            logger.error("Failed to get CGImage")
            return nil
        }

        let imageSize = CGSize(width: cgImage.width, height: cgImage.height)

        // 將正規化座標轉換為實際像素座標
        let pixelCorners = corners.map { point in
            CGPoint(
                x: point.x * imageSize.width,
                y: point.y * imageSize.height
            )
        }

        let topLeft = pixelCorners[0]
        let topRight = pixelCorners[1]
        let bottomRight = pixelCorners[2]
        let bottomLeft = pixelCorners[3]

        // 計算傾斜角度（取上邊和下邊的平均）
        let topAngle = atan2(topRight.y - topLeft.y, topRight.x - topLeft.x)
        let bottomAngle = atan2(bottomRight.y - bottomLeft.y, bottomRight.x - bottomLeft.x)
        let averageAngle = (topAngle + bottomAngle) / 2

        // 角度太小就不處理（小於 0.5 度）
        let angleInDegrees = averageAngle * 180 / .pi
        if abs(angleInDegrees) < 0.5 {
            logger.info("Angle too small (\(String(format: "%.2f", angleInDegrees))°), skipping rotation")
            return image
        }

        logger.info("Straightening image by \(String(format: "%.2f", -angleInDegrees))°")

        // 使用 Core Graphics 旋轉整張圖片
        let rotatedImage = rotateImage(image, byRadians: -averageAngle)

        return rotatedImage
    }

    /// 旋轉圖片（保持完整內容，擴展畫布）
    private func rotateImage(_ image: UIImage, byRadians radians: CGFloat) -> UIImage? {
        guard let cgImage = image.cgImage else { return nil }

        let originalWidth = CGFloat(cgImage.width)
        let originalHeight = CGFloat(cgImage.height)

        // 計算旋轉後的邊界框大小
        let sinAngle = abs(sin(radians))
        let cosAngle = abs(cos(radians))
        let newWidth = originalWidth * cosAngle + originalHeight * sinAngle
        let newHeight = originalWidth * sinAngle + originalHeight * cosAngle

        // 建立新的繪圖上下文
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: nil,
            width: Int(newWidth),
            height: Int(newHeight),
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            logger.error("Failed to create CGContext for rotation")
            return nil
        }

        // 填充白色背景
        context.setFillColor(UIColor.white.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: newWidth, height: newHeight))

        // 移動原點到新畫布中心，旋轉，再移回
        context.translateBy(x: newWidth / 2, y: newHeight / 2)
        context.rotate(by: radians)
        context.translateBy(x: -originalWidth / 2, y: -originalHeight / 2)

        // 繪製原始圖片
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: originalWidth, height: originalHeight))

        guard let rotatedCGImage = context.makeImage() else {
            logger.error("Failed to create rotated CGImage")
            return nil
        }

        let result = UIImage(cgImage: rotatedCGImage, scale: image.scale, orientation: .up)
        logger.info("Image straightened: \(Int(originalWidth))x\(Int(originalHeight)) -> \(Int(newWidth))x\(Int(newHeight))")

        return result
    }

    // MARK: - Private Methods

    /// 計算兩點距離
    private func distance(from p1: CGPoint, to p2: CGPoint) -> CGFloat {
        let dx = p2.x - p1.x
        let dy = p2.y - p1.y
        return sqrt(dx * dx + dy * dy)
    }

    /// 排序角落點為：左上、右上、右下、左下
    func sortCorners(_ corners: [CGPoint]) -> [CGPoint] {
        guard corners.count == 4 else { return corners }

        // 計算中心點
        let centerX = corners.reduce(0) { $0 + $1.x } / 4
        let centerY = corners.reduce(0) { $0 + $1.y } / 4

        // 根據相對於中心的位置分類
        var topLeft: CGPoint?
        var topRight: CGPoint?
        var bottomLeft: CGPoint?
        var bottomRight: CGPoint?

        for corner in corners {
            let isLeft = corner.x < centerX
            let isTop = corner.y < centerY

            if isLeft && isTop {
                topLeft = corner
            } else if !isLeft && isTop {
                topRight = corner
            } else if isLeft && !isTop {
                bottomLeft = corner
            } else {
                bottomRight = corner
            }
        }

        // 確保所有角落都有值
        guard let tl = topLeft, let tr = topRight,
              let bl = bottomLeft, let br = bottomRight else {
            logger.warning("Could not sort corners properly, using original order")
            return corners
        }

        return [tl, tr, br, bl]
    }

    // MARK: - Form Area Detection

    /// 裁切信封內的表格區域
    /// 奉獻袋表格位置固定，使用已知的比例直接裁切更可靠
    /// - Parameter envelopeImage: 已裁切的信封圖片
    /// - Returns: 裁切後的表格區域圖片
    func detectAndCropFormArea(from envelopeImage: UIImage) -> UIImage? {
        guard let cgImage = envelopeImage.cgImage else {
            logger.error("Failed to get CGImage for form detection")
            return nil
        }

        let width = CGFloat(cgImage.width)
        let height = CGFloat(cgImage.height)

        // 奉獻袋表格區域（根據實際信封版面）：
        // - 水平方向：左右各留 5% 邊距（表格幾乎佔滿寬度）
        // - 垂直方向：從頂部 18% 開始（跳過標題），到 78% 結束（跳過底部簽名區）
        // - 表格高度約佔信封的 60%
        let marginX = width * 0.05
        let topMargin = height * 0.18
        let bottomMargin = height * 0.22
        let formHeight = height - topMargin - bottomMargin

        let cropRect = CGRect(
            x: marginX,
            y: topMargin,
            width: width - marginX * 2,
            height: formHeight
        )

        logger.info("Cropping form area: x=\(Int(marginX)), y=\(Int(topMargin)), w=\(Int(cropRect.width)), h=\(Int(formHeight)) from \(Int(width))x\(Int(height))")

        guard let croppedCGImage = cgImage.cropping(to: cropRect) else {
            logger.error("Failed to crop form area")
            return nil
        }

        let result = UIImage(cgImage: croppedCGImage, scale: envelopeImage.scale, orientation: .up)
        logger.info("Form area cropped: \(Int(result.size.width))x\(Int(result.size.height))")
        return result
    }
}

// MARK: - Convenience Extension

extension UIImage {
    /// 擺正圖片（只旋轉，不裁切）
    func perspectiveCorrected(with detectionResult: EnvelopeDetectionResult) -> UIImage {
        PerspectiveCorrector.shared.correct(image: self, detectionResult: detectionResult)
    }

    /// 裁切並校正透視變形（原始功能，會裁切成偵測區域）
    func perspectiveCropped(with detectionResult: EnvelopeDetectionResult) -> UIImage {
        guard detectionResult.isDetected,
              let uiKitCorners = detectionResult.uiKitCorners,
              uiKitCorners.count == 4 else {
            return self
        }

        let sortedCorners = PerspectiveCorrector.shared.sortCorners(uiKitCorners)
        return PerspectiveCorrector.shared.correct(image: self, corners: sortedCorners) ?? self
    }

    /// 從信封圖片中偵測並裁切表格區域
    func cropFormArea() -> UIImage? {
        PerspectiveCorrector.shared.detectAndCropFormArea(from: self)
    }
}
