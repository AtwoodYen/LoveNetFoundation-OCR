import UIKit
import Vision
import CoreImage
import CoreImage.CIFilterBuiltins
import Accelerate
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "OfferingProcessor")

/// 奉獻袋專用處理器：過濾橘色印刷，只保留藍/黑色手寫
enum OfferingEnvelopeProcessor {

    // MARK: - PDF 印刷文字（愛網全人關懷協會奉獻袋.PDF）
    /// 這些是 PDF 上的印刷文字，用於比對排除
    static let pdfPrintedTexts: [String] = [
        // 標題與經文
        "憐憫貧窮的，就是借給耶和華，",
        "他的善行，耶和華必償還。（箴19:17）",
        // 表頭
        "項目", "金額",
        // 項目名稱
        "貧困關懷",
        "弱勢及偏鄉兒童青少年",
        "偏鄉老人",
        "愛心小站",
        "經常費",
        // 合計
        "合計$", "合計",
        // 日期區
        "奉獻日期", "年", "月", "日",
        // 收據區
        "奉獻收據",
        "不需要",
        "上傳國稅局（無收據）", "上傳國稅局",
        "需要收據：紙本 電子檔", "需要收據", "紙本", "電子檔", "電子擔",
        // 平台提示
        "歡迎利用線上奉獻平台", "詳見背面",
        // 個人資料區
        "奉獻人姓名",
        "電話/手機", "電話", "手機",
        "收據抬頭",
        "同奉獻者",
        "身份證字號",
        "郵寄地址",
        "電子信箱",
        // 其他
        "無電子信箱者必填"
    ]

    /// 印刷標籤集合（快速查找用）
    static let printedLabels: Set<String> = Set(pdfPrintedTexts)

    // MARK: - 處理結果
    struct ProcessedResult {
        /// 預處理後的圖片（用於 OCR）
        let preprocessedImage: UIImage
        /// 過濾後的圖片（只保留藍/黑色，用於手寫判斷）
        let filteredImage: UIImage
        /// 原始 OCR 結果（所有文字）
        let rawOCRText: String
        /// 手寫內容提取結果
        let handwrittenFields: [HandwrittenField]
        /// 最終格式化輸出
        let formattedOutput: String
    }

    struct HandwrittenField {
        let label: String      // 標籤（如「奉獻日期」）
        let value: String      // 手寫值（如「115年3月22日」）
        let isBlueOrBlack: Bool
    }

    // MARK: - 預處理配置

    /// 預處理選項
    struct PreprocessOptions {
        /// 是否移除橘色背景
        var removeOrangeBackground = true
        /// 是否增強墨水（藍/黑色）
        var enhanceInk = true
        /// 是否增強對比度
        var enhanceContrast = true
        /// 是否二值化
        var binarize = true
        /// 是否降噪
        var denoise = true
        /// 除錯模式（上傳每一步的中間結果）
        var debugMode = false

        static let `default` = PreprocessOptions()

        /// 保守預處理（不做二值化和降噪，保留更多細節）
        static let conservative: PreprocessOptions = {
            var options = PreprocessOptions()
            options.binarize = false
            options.denoise = false
            return options
        }()
    }

    // MARK: - 預處理管線

    /// 完整預處理管線：針對橘色底、印刷不清的奉獻袋優化
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - options: 預處理選項
    /// - Returns: 預處理後的圖片
    static func preprocess(image: UIImage, options: PreprocessOptions = .default) -> UIImage {
        logger.info("🔧 開始預處理管線")
        var result = image

        // 步驟 1: 移除橘色背景
        if options.removeOrangeBackground {
            result = removeOrangeBackground(image: result)
            logger.info("  ✓ 橘色背景移除完成")
        }

        // 步驟 2: 增強墨水（藍/黑色 → 純黑）
        if options.enhanceInk {
            result = enhanceInkColors(image: result)
            logger.info("  ✓ 墨水增強完成")
        }

        // 步驟 3: 增強對比度
        if options.enhanceContrast {
            result = enhanceContrast(image: result)
            logger.info("  ✓ 對比度增強完成")
        }

        // 步驟 4: 自適應二值化
        if options.binarize {
            result = adaptiveBinarize(image: result)
            logger.info("  ✓ 二值化完成")
        }

        // 步驟 5: 形態學降噪
        if options.denoise {
            result = morphologicalDenoise(image: result)
            logger.info("  ✓ 降噪完成")
        }

        logger.info("✅ 預處理管線完成")
        return result
    }

    // MARK: - 除錯預處理（每一步上傳到 Server）

    /// 除錯用預處理管線：每一步都上傳到 Server
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - client: API Client
    ///   - taskId: 任務 ID
    ///   - options: 預處理選項
    /// - Returns: 預處理後的圖片
    static func preprocessWithDebug(
        image: UIImage,
        client: OCRAPIClient,
        taskId: String,
        options: PreprocessOptions = .default
    ) async -> UIImage {
        logger.info("🔧 開始除錯預處理管線")
        var result = image
        var stepNumber = 1

        // 上傳原圖
        await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "original")
        stepNumber += 1

        // 步驟 1: 移除橘色背景
        if options.removeOrangeBackground {
            result = removeOrangeBackground(image: result)
            await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "orange_removed")
            stepNumber += 1
            logger.info("  ✓ 橘色背景移除完成")
        }

        // 步驟 2: 增強墨水（藍/黑色 → 加深）
        if options.enhanceInk {
            result = enhanceInkColors(image: result)
            await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "ink_enhanced")
            stepNumber += 1
            logger.info("  ✓ 墨水增強完成")
        }

        // 步驟 3: 增強對比度
        if options.enhanceContrast {
            result = enhanceContrast(image: result)
            await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "contrast_enhanced")
            stepNumber += 1
            logger.info("  ✓ 對比度增強完成")
        }

        // 步驟 4: 自適應二值化
        if options.binarize {
            result = adaptiveBinarize(image: result)
            await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "binarized")
            stepNumber += 1
            logger.info("  ✓ 二值化完成")
        }

        // 步驟 5: 形態學降噪
        if options.denoise {
            result = morphologicalDenoise(image: result)
            await uploadDebugImage(result, client: client, taskId: taskId, step: stepNumber, name: "denoised")
            logger.info("  ✓ 降噪完成")
        }

        logger.info("✅ 除錯預處理管線完成，共上傳 \(stepNumber) 張圖片")
        return result
    }

    /// 上傳除錯圖片
    private static func uploadDebugImage(
        _ image: UIImage,
        client: OCRAPIClient,
        taskId: String,
        step: Int,
        name: String
    ) async {
        guard let pngData = image.pngData() else {
            logger.error("❌ 無法轉換圖片為 PNG: step \(step)")
            return
        }
        let filename = String(format: "page_%04d_%@.png", step, name)
        do {
            try await client.uploadDebugImage(image: pngData, taskId: taskId, filename: filename)
            logger.info("📤 上傳除錯圖片: \(filename)")
        } catch {
            logger.error("❌ 上傳除錯圖片失敗: \(error.localizedDescription)")
        }
    }

    // MARK: - 步驟 1: 移除橘色背景

    /// 移除橘色背景（亮度感知版：暗色文字保留為灰階，亮橘背景轉白色）
    private static func removeOrangeBackground(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * width

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        guard let data = context.data else { return image }
        let buffer = data.bindMemory(to: UInt8.self, capacity: width * height * bytesPerPixel)

        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let r = CGFloat(buffer[offset]) / 255.0
                let g = CGFloat(buffer[offset + 1]) / 255.0
                let b = CGFloat(buffer[offset + 2]) / 255.0

                let (h, s, v) = rgbToHsv(r: r, g: g, b: b)

                // 計算感知亮度 (ITU-R BT.601)
                let luminance = 0.299 * r + 0.587 * g + 0.114 * b

                // 橘色色調判斷（寬鬆，包含深橘到淺橘）
                let isOrangeHue = (h >= 0.0 && h <= 0.14) && s > 0.15

                guard isOrangeHue else { continue }  // 非橘色色調 → 完全不動

                if luminance > 0.55 {
                    // ★ 亮橘色背景 → 白色（這是原本就要去的）
                    buffer[offset] = 255
                    buffer[offset + 1] = 255
                    buffer[offset + 2] = 255
                } else if luminance > 0.35 {
                    // ★ 中等亮度帶橘色調（文字邊緣、淺色印刷）→ 去色保留亮度
                    // 轉灰階但稍微壓暗，讓文字更清晰
                    let gray = UInt8(max(0, min(255, luminance * 230)))
                    buffer[offset] = gray
                    buffer[offset + 1] = gray
                    buffer[offset + 2] = gray
                }
                // ★ luminance <= 0.35：暗色像素（確定是文字）→ 完全不動
            }
        }

        guard let outputCGImage = context.makeImage() else { return image }
        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    // MARK: - 步驟 2: 增強墨水顏色

    /// 增強墨水顏色：只加深藍色/黑色墨水，其他顏色保留原樣
    /// 改進版：不再將非墨水顏色強制轉為白色，避免丟失重要資訊
    private static func enhanceInkColors(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * width

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        guard let data = context.data else { return image }
        let buffer = data.bindMemory(to: UInt8.self, capacity: width * height * bytesPerPixel)

        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let r = CGFloat(buffer[offset]) / 255.0
                let g = CGFloat(buffer[offset + 1]) / 255.0
                let b = CGFloat(buffer[offset + 2]) / 255.0

                let (h, s, v) = rgbToHsv(r: r, g: g, b: b)

                // 判斷是否為墨水顏色
                // 藍色：H = 200°-260° (0.55-0.72), S > 15%
                let isBlue = (h >= 0.55 && h <= 0.75) && s > 0.15

                // 黑色/深灰：V < 45%
                let isBlack = v < 0.45

                // 深藍黑色墨水（褪色的藍黑筆）
                let isDarkBlueBlack = (h >= 0.50 && h <= 0.80) && v < 0.60

                if isBlue || isBlack || isDarkBlueBlack {
                    // 墨水 → 加深成純黑（或深灰）
                    // 使用更溫和的加深：原始亮度的 60%
                    let intensity = UInt8(max(0, min(200, v * 150)))
                    buffer[offset] = intensity
                    buffer[offset + 1] = intensity
                    buffer[offset + 2] = intensity
                }
                // 其他顏色保持不變，不做處理
            }
        }

        guard let outputCGImage = context.makeImage() else { return image }
        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    // MARK: - 步驟 3: 增強對比度

    /// 使用 Core Image 增強對比度（類似 CLAHE 效果）
    private static func enhanceContrast(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let ciImage = CIImage(cgImage: cgImage)
        let context = CIContext(options: [.useSoftwareRenderer: false])

        // 使用 CIColorControls 增強對比度
        guard let colorControlsFilter = CIFilter(name: "CIColorControls") else { return image }
        colorControlsFilter.setValue(ciImage, forKey: kCIInputImageKey)
        colorControlsFilter.setValue(1.3, forKey: kCIInputContrastKey)  // 增強對比度
        colorControlsFilter.setValue(0.05, forKey: kCIInputBrightnessKey)  // 略微提亮

        guard let contrastOutput = colorControlsFilter.outputImage else { return image }

        // 使用 CIUnsharpMask 銳化邊緣
        guard let sharpenFilter = CIFilter(name: "CIUnsharpMask") else {
            // 如果銳化失敗，至少返回對比度增強的結果
            if let outputCGImage = context.createCGImage(contrastOutput, from: contrastOutput.extent) {
                return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
            }
            return image
        }
        sharpenFilter.setValue(contrastOutput, forKey: kCIInputImageKey)
        sharpenFilter.setValue(1.5, forKey: kCIInputRadiusKey)
        sharpenFilter.setValue(0.5, forKey: kCIInputIntensityKey)

        guard let finalOutput = sharpenFilter.outputImage,
              let outputCGImage = context.createCGImage(finalOutput, from: finalOutput.extent) else {
            return image
        }

        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    // MARK: - 步驟 4: 自適應二值化

    /// 自適應閾值二值化
    private static func adaptiveBinarize(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * width

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        guard let data = context.data else { return image }
        let buffer = data.bindMemory(to: UInt8.self, capacity: width * height * bytesPerPixel)

        // 建立灰度陣列
        var grayscale = [UInt8](repeating: 0, count: width * height)
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let r = Int(buffer[offset])
                let g = Int(buffer[offset + 1])
                let b = Int(buffer[offset + 2])
                // 加權灰度
                grayscale[y * width + x] = UInt8((r * 299 + g * 587 + b * 114) / 1000)
            }
        }

        // 計算全域閾值（Otsu's method 簡化版）
        var histogram = [Int](repeating: 0, count: 256)
        for gray in grayscale {
            histogram[Int(gray)] += 1
        }

        let total = width * height
        var sum: Double = 0
        for i in 0..<256 {
            sum += Double(i * histogram[i])
        }

        var sumB: Double = 0
        var wB = 0
        var maxVariance: Double = 0
        var threshold = 128

        for t in 0..<256 {
            wB += histogram[t]
            if wB == 0 { continue }

            let wF = total - wB
            if wF == 0 { break }

            sumB += Double(t * histogram[t])

            let mB = sumB / Double(wB)
            let mF = (sum - sumB) / Double(wF)

            let variance = Double(wB) * Double(wF) * (mB - mF) * (mB - mF)

            if variance > maxVariance {
                maxVariance = variance
                threshold = t
            }
        }

        // 調高閾值以保留更多淡色文字（針對印刷不清的情況）
        // 原本的 Otsu 閾值可能太低，導致淡色文字被當成背景
        threshold = min(200, threshold + 20)

        logger.debug("  二值化閾值: \(threshold)")

        // 應用閾值：灰度 < 閾值 → 黑色（文字），否則 → 白色（背景）
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let gray = grayscale[y * width + x]

                // 注意：較暗的像素（gray < threshold）是文字，應該設為黑色
                let value: UInt8 = gray < threshold ? 0 : 255
                buffer[offset] = value
                buffer[offset + 1] = value
                buffer[offset + 2] = value
            }
        }

        guard let outputCGImage = context.makeImage() else { return image }
        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    // MARK: - 步驟 5: 形態學降噪

    /// 形態學降噪（開運算 = 侵蝕 + 膨脹）
    private static func morphologicalDenoise(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * width

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        guard let data = context.data else { return image }
        let buffer = data.bindMemory(to: UInt8.self, capacity: width * height * bytesPerPixel)

        // 建立工作緩衝區
        var tempBuffer = [UInt8](repeating: 255, count: width * height)

        // 從原始緩衝區提取灰度
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                tempBuffer[y * width + x] = buffer[offset]
            }
        }

        // 侵蝕（移除小白點）
        var eroded = [UInt8](repeating: 255, count: width * height)
        let kernelSize = 1  // 3x3 kernel

        for y in kernelSize..<(height - kernelSize) {
            for x in kernelSize..<(width - kernelSize) {
                var minVal: UInt8 = 255
                for ky in -kernelSize...kernelSize {
                    for kx in -kernelSize...kernelSize {
                        let val = tempBuffer[(y + ky) * width + (x + kx)]
                        minVal = min(minVal, val)
                    }
                }
                eroded[y * width + x] = minVal
            }
        }

        // 膨脹（恢復筆畫粗細）
        var dilated = [UInt8](repeating: 0, count: width * height)

        for y in kernelSize..<(height - kernelSize) {
            for x in kernelSize..<(width - kernelSize) {
                var maxVal: UInt8 = 0
                for ky in -kernelSize...kernelSize {
                    for kx in -kernelSize...kernelSize {
                        let val = eroded[(y + ky) * width + (x + kx)]
                        maxVal = max(maxVal, val)
                    }
                }
                dilated[y * width + x] = maxVal
            }
        }

        // 寫回緩衝區
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let value = dilated[y * width + x]
                buffer[offset] = value
                buffer[offset + 1] = value
                buffer[offset + 2] = value
            }
        }

        guard let outputCGImage = context.makeImage() else { return image }
        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    /// 堂次對應表
    private static let serviceNumberMap: [String: Int] = [
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6
    ]

    // MARK: - 主處理函數
    static func process(image: UIImage) async throws -> ProcessedResult {
        logger.info("🎨 開始奉獻袋圖片處理")

        // 1. 完整預處理管線：移除橘色、增強墨水、對比度增強、二值化、降噪
        let preprocessedImage = preprocess(image: image)
        logger.info("✅ 預處理管線完成")

        // 2. 舊版顏色過濾（用於手寫判斷）
        let filteredImage = filterOrangeKeepBlueBlack(image: image)
        logger.info("✅ 顏色過濾完成")

        // 3. 對預處理後的圖進行 OCR（獲取所有文字和位置）
        let observations = try await recognizeTextWithPositions(in: preprocessedImage)
        logger.info("📝 預處理圖辨識到 \(observations.count) 個文字區塊")

        // 4. 對顏色過濾後的圖進行 OCR（只有藍/黑色文字，用於判斷手寫）
        let filteredObservations = try await recognizeTextWithPositions(in: filteredImage)
        logger.info("📝 過濾圖辨識到 \(filteredObservations.count) 個文字區塊")

        // 4. 分析哪些是手寫內容
        let handwrittenFields = extractHandwrittenFields(
            allObservations: observations,
            blueBlackObservations: filteredObservations,
            imageSize: image.size
        )

        // 5. 生成格式化輸出
        let formatted = formatOutput(fields: handwrittenFields)
        let rawText = observations.map { $0.text }.joined(separator: "\n")

        logger.info("📄 最終輸出:\n\(formatted)")

        return ProcessedResult(
            preprocessedImage: preprocessedImage,
            filteredImage: filteredImage,
            rawOCRText: rawText,
            handwrittenFields: handwrittenFields,
            formattedOutput: formatted
        )
    }

    /// 只執行預處理（用於上傳到後端 OCR 前的前處理）
    /// - Parameters:
    ///   - image: 原始圖片
    ///   - options: 預處理選項（預設啟用所有步驟）
    /// - Returns: 預處理後的圖片
    static func preprocessForUpload(image: UIImage, options: PreprocessOptions = .default) -> UIImage {
        logger.info("📤 開始上傳前預處理")
        let result = preprocess(image: image, options: options)
        logger.info("📤 上傳前預處理完成")
        return result
    }

    /// 輕量預處理（只移除橘色背景和增強墨水，不做二值化和降噪）
    /// 適用於需要保留彩色資訊的情況
    static func lightPreprocess(image: UIImage) -> UIImage {
        var options = PreprocessOptions()
        options.binarize = false
        options.denoise = false
        return preprocess(image: image, options: options)
    }

    // MARK: - 顏色過濾（舊版，用於手寫判斷）
    private static func filterOrangeKeepBlueBlack(image: UIImage) -> UIImage {
        guard let cgImage = image.cgImage else { return image }

        let width = cgImage.width
        let height = cgImage.height
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * width
        let bitsPerComponent = 8

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: bitsPerComponent,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return image }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        guard let data = context.data else { return image }
        let buffer = data.bindMemory(to: UInt8.self, capacity: width * height * bytesPerPixel)

        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * bytesPerPixel
                let r = CGFloat(buffer[offset]) / 255.0
                let g = CGFloat(buffer[offset + 1]) / 255.0
                let b = CGFloat(buffer[offset + 2]) / 255.0

                // 轉換為 HSV
                let (h, s, v) = rgbToHsv(r: r, g: g, b: b)

                // 新：先算感知亮度，再決定要白化、灰階化、還是不動
                let luminance = 0.299 * r + 0.587 * g + 0.114 * b
                let isOrangeHue = (h >= 0.0 && h <= 0.14) && s > 0.15
                // 僅在偏橘色區域依亮度白化／灰階化；其餘像素不動
                if isOrangeHue {
                    if luminance > 0.55 {
                        buffer[offset] = 255
                        buffer[offset + 1] = 255
                        buffer[offset + 2] = 255
                    } else if luminance > 0.35 {
                        let gray = UInt8(clamping: Int(luminance * 255.0 * 0.9))
                        buffer[offset] = gray
                        buffer[offset + 1] = gray
                        buffer[offset + 2] = gray
                    }
                }
                
                /*
                // 判斷顏色類型
                let isOrange = (h >= 0.02 && h <= 0.12) && s > 0.3 && v > 0.4
                let isBlue = (h >= 0.55 && h <= 0.72) && s > 0.2
                let isBlack = v < 0.35 && s < 0.3
                let isDarkInk = v < 0.5 && s < 0.4  // 深色墨水


                if isOrange {
                    // 橘色 → 設為白色
                    buffer[offset] = 255
                    buffer[offset + 1] = 255
                    buffer[offset + 2] = 255
                } else if isBlue || isBlack || isDarkInk {
                    // 藍/黑色 → 保留（加深對比）
                    buffer[offset] = UInt8(max(0, CGFloat(buffer[offset]) * 0.7))
                    buffer[offset + 1] = UInt8(max(0, CGFloat(buffer[offset + 1]) * 0.7))
                    buffer[offset + 2] = UInt8(max(0, CGFloat(buffer[offset + 2]) * 0.7))
                } else if v > 0.85 {
                    // 淺色/白色 → 保持白色
                    buffer[offset] = 255
                    buffer[offset + 1] = 255
                    buffer[offset + 2] = 255
                } else {
                    // 其他顏色 → 設為淺灰（減少干擾）
                    buffer[offset] = 240
                    buffer[offset + 1] = 240
                    buffer[offset + 2] = 240
                }
                */
            }
        }

        guard let outputCGImage = context.makeImage() else { return image }
        return UIImage(cgImage: outputCGImage, scale: image.scale, orientation: image.imageOrientation)
    }

    private static func rgbToHsv(r: CGFloat, g: CGFloat, b: CGFloat) -> (h: CGFloat, s: CGFloat, v: CGFloat) {
        let maxC = max(r, g, b)
        let minC = min(r, g, b)
        let delta = maxC - minC

        var h: CGFloat = 0
        if delta > 0 {
            if maxC == r {
                h = ((g - b) / delta).truncatingRemainder(dividingBy: 6)
            } else if maxC == g {
                h = (b - r) / delta + 2
            } else {
                h = (r - g) / delta + 4
            }
            h /= 6
            if h < 0 { h += 1 }
        }

        let s = maxC > 0 ? delta / maxC : 0
        let v = maxC

        return (h, s, v)
    }

    // MARK: - OCR with positions
    struct TextObservation {
        let text: String
        let boundingBox: CGRect  // normalized (0-1)
        let confidence: Float
    }

    private static func recognizeTextWithPositions(in image: UIImage) async throws -> [TextObservation] {
        guard let cgImage = image.cgImage else {
            throw DeviceOCRError.noCGImage
        }

        return try await withCheckedThrowingContinuation { cont in
            let request = VNRecognizeTextRequest { request, error in
                if let error {
                    cont.resume(throwing: error)
                    return
                }
                guard let observations = request.results as? [VNRecognizedTextObservation] else {
                    cont.resume(returning: [])
                    return
                }

                let results = observations.compactMap { obs -> TextObservation? in
                    guard let candidate = obs.topCandidates(1).first else { return nil }
                    return TextObservation(
                        text: candidate.string,
                        boundingBox: obs.boundingBox,
                        confidence: candidate.confidence
                    )
                }
                cont.resume(returning: results)
            }
            request.recognitionLevel = .accurate
            if #available(iOS 17.0, *) {
                request.revision = VNRecognizeTextRequestRevision3
            }
            if #available(iOS 16.0, *) {
                request.recognitionLanguages = ["zh-Hant", "zh-Hans", "en-US"]
            }
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
            } catch {
                cont.resume(throwing: error)
            }
        }
    }

    // MARK: - 提取手寫欄位
    private static func extractHandwrittenFields(
        allObservations: [TextObservation],
        blueBlackObservations: [TextObservation],
        imageSize: CGSize
    ) -> [HandwrittenField] {
        var fields: [HandwrittenField] = []
        let blueBlackTexts = Set(blueBlackObservations.map { $0.text })

        logger.info("═══════════════════════════════════════════════════════════")
        logger.info("🔍 開始分析手寫欄位")
        logger.info("═══════════════════════════════════════════════════════════")

        // 列出 PDF 印刷文字參考
        logger.info("📄 PDF 印刷文字（用於排除比對）:")
        for (i, printed) in pdfPrintedTexts.prefix(10).enumerated() {
            logger.info("  [\(i)] \"\(printed)\"")
        }
        logger.info("  ... 共 \(pdfPrintedTexts.count) 項")

        logger.info("───────────────────────────────────────────────────────────")
        logger.info("🎨 過濾後（藍/黑色）文字集合:")
        for text in blueBlackTexts {
            logger.info("  • \"\(text)\"")
        }

        // 按 Y 座標排序（由上到下），再按 X 排序（由左到右）
        // Vision 的 Y 座標是從下往上 (0=底部, 1=頂部)，所以要反過來排
        let sortedObservations = allObservations.sorted { a, b in
            let dy = abs(a.boundingBox.midY - b.boundingBox.midY)
            if dy > 0.02 {  // 不同行
                return a.boundingBox.midY > b.boundingBox.midY  // Y 大的在上面
            }
            return a.boundingBox.midX < b.boundingBox.midX  // 同行按 X 排
        }

        // 將觀察結果分組為「行」
        var lines: [[TextObservation]] = []
        var currentLine: [TextObservation] = []
        var lastY: CGFloat = -1

        for obs in sortedObservations {
            if lastY < 0 || abs(obs.boundingBox.midY - lastY) < 0.025 {
                currentLine.append(obs)
            } else {
                if !currentLine.isEmpty {
                    lines.append(currentLine)
                }
                currentLine = [obs]
            }
            lastY = obs.boundingBox.midY
        }
        if !currentLine.isEmpty {
            lines.append(currentLine)
        }

        logger.info("───────────────────────────────────────────────────────────")
        logger.info("📋 原圖 OCR 結果（按行顯示，共 \(lines.count) 行）:")

        // 用於儲存被排除的文字和原因
        var excludedTexts: [(text: String, reason: String)] = []
        // 用於儲存保留的手寫文字
        var handwrittenTexts: [(text: String, obs: TextObservation, lineIndex: Int)] = []

        for (lineIndex, line) in lines.enumerated() {
            let lineY = line.first?.boundingBox.midY ?? 0
            let lineTexts = line.map { $0.text }
            let lineStr = lineTexts.joined(separator: " | ")
            logger.info("  行\(lineIndex + 1) [Y=\(String(format: "%.3f", lineY))]: \(lineStr)")

            // 分析每個文字區塊
            for obs in line {
                let text = obs.text.trimmingCharacters(in: .whitespaces)
                let inFiltered = blueBlackTexts.contains(text) ||
                    blueBlackTexts.contains(where: { $0.contains(text) || text.contains($0) })

                // 檢查是否與 PDF 印刷文字相似度 > 80%
                var matchedPrinted: String?
                var similarity: Double = 0
                for printed in pdfPrintedTexts {
                    let sim = stringSimilarity(text, printed)
                    if sim > similarity {
                        similarity = sim
                        if sim > 0.8 {
                            matchedPrinted = printed
                        }
                    }
                }

                if let matched = matchedPrinted, !inFiltered {
                    // 與印刷文字相似度 > 80% 且不是藍/黑色 → 排除
                    let reason = "與印刷文字「\(matched)」相似度 \(String(format: "%.0f", similarity * 100))%，且非藍/黑色"
                    excludedTexts.append((text, reason))
                    logger.info("    ❌ \"\(text)\" → 排除：\(reason)")
                } else if inFiltered {
                    // 在過濾後結果中 → 保留為手寫
                    handwrittenTexts.append((text, obs, lineIndex))
                    logger.info("    ✅ \"\(text)\" → 保留：藍/黑色手寫")
                } else if similarity > 0.5 {
                    // 部分相似但未達 80%
                    logger.info("    ⚠️ \"\(text)\" → 待定：與印刷文字相似度 \(String(format: "%.0f", similarity * 100))%，非藍/黑色")
                } else {
                    // 不像印刷文字，可能是手寫
                    handwrittenTexts.append((text, obs, lineIndex))
                    logger.info("    ✅ \"\(text)\" → 保留：不匹配任何印刷文字")
                }
            }
        }

        logger.info("───────────────────────────────────────────────────────────")
        logger.info("📊 分析結果:")
        logger.info("  排除: \(excludedTexts.count) 項")
        logger.info("  保留: \(handwrittenTexts.count) 項")

        // ═══════════════════════════════════════════════════════════
        // 特別處理：右上角標記（短日期、堂次）
        // 這些可能是印刷的橘色文字，但對辨識很重要
        // ═══════════════════════════════════════════════════════════
        logger.info("───────────────────────────────────────────────────────────")
        logger.info("🔍 搜尋右上角標記（日期、堂次）:")

        var cornerShortDate: (month: String, day: String)?
        var cornerServiceNumber: Int?

        // 搜尋右上角區域的文字（Vision Y 座標：1=頂部, 0=底部）
        // X > 0.6 且 Y > 0.7 表示右上角
        for obs in allObservations {
            let text = obs.text.trimmingCharacters(in: .whitespaces)
            let x = obs.boundingBox.midX
            let y = obs.boundingBox.midY

            // 在右上角區域
            let isUpperRight = x > 0.5 && y > 0.6
            // 或在頂部
            let isTop = y > 0.75

            if isUpperRight || isTop {
                logger.info("  📍 右上/頂部文字: \"\(text)\" at X=\(String(format: "%.2f", x)), Y=\(String(format: "%.2f", y))")

                // 檢查短日期格式：3/22, 3月22日, 3/22日
                if cornerShortDate == nil {
                    if let (m, d) = parseShortDate(text) {
                        cornerShortDate = (m, d)
                        logger.info("    ✓ 找到短日期: \(m)/\(d)")
                    }
                }

                // 檢查堂次標記：[一], [二], (1), 第一堂
                if cornerServiceNumber == nil {
                    if let svc = parseServiceNumber(text) {
                        cornerServiceNumber = svc
                        logger.info("    ✓ 找到堂次: 第\(svc)堂")
                    }
                }
            }
        }

        // 如果右上角沒找到，在所有文字中搜尋
        if cornerShortDate == nil || cornerServiceNumber == nil {
            logger.info("  右上角未完全找到，搜尋全部文字...")
            for obs in allObservations {
                let text = obs.text.trimmingCharacters(in: .whitespaces)

                if cornerShortDate == nil {
                    if let (m, d) = parseShortDate(text) {
                        cornerShortDate = (m, d)
                        logger.info("    ✓ 全文找到短日期: \(m)/\(d) (from: \"\(text)\")")
                    }
                }

                if cornerServiceNumber == nil {
                    if let svc = parseServiceNumber(text) {
                        cornerServiceNumber = svc
                        logger.info("    ✓ 全文找到堂次: 第\(svc)堂 (from: \"\(text)\")")
                    }
                }
            }
        }

        // 收集所有金額數字
        var allAmounts: [(text: String, amount: String, obs: TextObservation)] = []
        for (text, obs, _) in handwrittenTexts {
            if let amount = parseAmount(text) {
                allAmounts.append((text, amount, obs))
                logger.info("💰 找到金額: \"\(text)\" → \(amount)")
            }
        }

        // 收集日期碎片
        var yearPart: String?
        var monthPart: String?
        var dayPart: String?

        for (text, _, _) in handwrittenTexts {
            // 找年份：數字+年 或 數字/年
            if text.contains("年") {
                let digits = text.replacingOccurrences(of: "[^0-9]", with: "", options: .regularExpression)
                if !digits.isEmpty {
                    yearPart = digits
                    logger.info("📅 找到年份碎片: \"\(text)\" → 年=\(digits)")
                }
            }
            // 找月份/日期：>數字 或 純數字
            if text.hasPrefix(">") || text.hasPrefix("≥") {
                let digits = text.replacingOccurrences(of: "[^0-9]", with: "", options: .regularExpression)
                if !digits.isEmpty {
                    // 可能是月或日，根據數值判斷
                    if let num = Int(digits) {
                        if num >= 1 && num <= 12 && monthPart == nil {
                            monthPart = digits
                            logger.info("📅 找到月份碎片: \"\(text)\" → 月=\(digits)")
                        } else if num >= 1 && num <= 31 {
                            dayPart = digits
                            logger.info("📅 找到日期碎片: \"\(text)\" → 日=\(digits)")
                        }
                    }
                }
            }
        }

        logger.info("───────────────────────────────────────────────────────────")
        logger.info("🏷️ 欄位提取:")

        // 0. 添加堂次（如果找到）
        if let svc = cornerServiceNumber {
            let cnNums = ["", "一", "二", "三", "四", "五", "六"]
            let serviceText = "第\(cnNums[svc])堂"
            fields.append(HandwrittenField(
                label: "禮拜堂次",
                value: serviceText,
                isBlueOrBlack: false  // 可能是印刷的
            ))
            logger.info("  ✓ 禮拜堂次: \(serviceText)")
        }

        // 1. 找勾選的項目
        for (text, _, _) in handwrittenTexts {
            if text.contains("弱勢") || text.contains("偏鄉兒童") || text.contains("偏鄉") {
                var amountStr = "(金額未識別)"
                if let firstAmount = allAmounts.first {
                    amountStr = firstAmount.amount
                    logger.info("  → 項目關聯金額: \(firstAmount.text) → \(amountStr)")
                } else {
                    logger.warning("  ⚠️ 未找到任何金額數字")
                }
                fields.append(HandwrittenField(
                    label: "支持項目",
                    value: "☑ 弱勢及偏鄉兒童青少年  \(amountStr)",
                    isBlueOrBlack: true
                ))
                logger.info("  ✓ 支持項目: 弱勢及偏鄉兒童青少年, 金額=\(amountStr)")
            }

            // 收據選項
            if text.contains("不需要") || text.contains("不需") {
                fields.append(HandwrittenField(
                    label: "奉獻收據",
                    value: "不需要",
                    isBlueOrBlack: true
                ))
                logger.info("  ✓ 奉獻收據: 不需要 (from: \"\(text)\")")
            }
        }

        // 2. 組合日期
        if let y = yearPart {
            var yearStr = y
            if yearStr.count == 2 {
                yearStr = "1" + yearStr
            } else if yearStr.count == 1 {
                yearStr = "11" + yearStr
            }
            var dateStr = "\(yearStr)年"
            // 優先使用手寫碎片的月日，如果沒有則用右上角短日期
            let finalMonth = monthPart ?? cornerShortDate?.month
            let finalDay = dayPart ?? cornerShortDate?.day
            if let m = finalMonth {
                dateStr += "\(m)月"
            }
            if let d = finalDay {
                dateStr += "\(d)日"
            }
            fields.append(HandwrittenField(
                label: "奉獻日期",
                value: dateStr,
                isBlueOrBlack: true
            ))
            logger.info("  ✓ 奉獻日期（組合）: \(dateStr)")
        } else if let shortDate = cornerShortDate {
            // 沒有年份碎片，但有右上角短日期，推算今年
            let calendar = Calendar.current
            let year = calendar.component(.year, from: Date())
            let rocYear = year - 1911
            let dateStr = "\(rocYear)年\(shortDate.month)月\(shortDate.day)日"
            fields.append(HandwrittenField(
                label: "奉獻日期",
                value: dateStr + "（推算）",
                isBlueOrBlack: false
            ))
            logger.info("  ✓ 奉獻日期（從右上角短日期推算）: \(dateStr)")
        } else {
            logger.warning("  ⚠️ 未找到年份碎片，也未找到短日期")
        }

        // 3. 找姓名 - 檢查是否在「奉獻人姓名」標籤同一行或附近
        logger.info("  🔎 尋找姓名...")

        // 先找「奉獻人姓名」標籤的位置
        var nameLabelLineIndex: Int?
        var nameLabelY: CGFloat?
        for (lineIndex, line) in lines.enumerated() {
            for obs in line {
                if obs.text.contains("奉獻人姓名") || obs.text.contains("姓名") {
                    nameLabelLineIndex = lineIndex
                    nameLabelY = obs.boundingBox.midY
                    logger.info("    找到「奉獻人姓名」標籤在行 \(lineIndex + 1), Y=\(String(format: "%.3f", obs.boundingBox.midY))")
                    break
                }
            }
            if nameLabelLineIndex != nil { break }
        }

        // 在同一行或附近找姓名
        for (text, obs, lineIndex) in handwrittenTexts {
            if isChineseName(text) {
                let sameLineAsLabel = nameLabelLineIndex != nil && lineIndex == nameLabelLineIndex
                let nearLabel = nameLabelY != nil && abs(obs.boundingBox.midY - nameLabelY!) < 0.05

                logger.info("    候選姓名: \"\(text)\" 在行 \(lineIndex + 1), 與標籤同行=\(sameLineAsLabel), 接近標籤=\(nearLabel)")

                if !fields.contains(where: { $0.label == "奉獻人姓名" }) {
                    fields.append(HandwrittenField(
                        label: "奉獻人姓名",
                        value: text,
                        isBlueOrBlack: true
                    ))
                    logger.info("  ✓ 奉獻人姓名: \(text)")
                }
            }
        }

        if !fields.contains(where: { $0.label == "奉獻人姓名" }) {
            logger.warning("  ⚠️ 未找到姓名")
            // 列出所有候選
            for (text, _, lineIndex) in handwrittenTexts {
                let cleaned = text.trimmingCharacters(in: .whitespaces)
                if cleaned.count >= 2 && cleaned.count <= 4 {
                    logger.info("    可能的姓名候選: \"\(text)\" 在行 \(lineIndex + 1)")
                }
            }
        }

        // 4. 找身份證字號（台灣格式：1 英文 + 9 數字）
        logger.info("  🔎 尋找身份證字號...")
        for (text, _, _) in handwrittenTexts {
            if let idNum = parseIdNumber(text) {
                if !fields.contains(where: { $0.label == "身份證字號" }) {
                    fields.append(HandwrittenField(
                        label: "身份證字號",
                        value: idNum,
                        isBlueOrBlack: true
                    ))
                    logger.info("  ✓ 身份證字號: \(idNum)")
                }
            }
        }
        // 也在所有文字中搜尋（可能是印刷的欄位）
        if !fields.contains(where: { $0.label == "身份證字號" }) {
            for obs in allObservations {
                if let idNum = parseIdNumber(obs.text) {
                    fields.append(HandwrittenField(
                        label: "身份證字號",
                        value: idNum,
                        isBlueOrBlack: false
                    ))
                    logger.info("  ✓ 身份證字號（全文）: \(idNum)")
                    break
                }
            }
        }

        // 5. 找電話/手機
        logger.info("  🔎 尋找電話/手機...")
        for (text, _, _) in handwrittenTexts {
            if let phone = parsePhone(text) {
                if !fields.contains(where: { $0.label == "電話/手機" }) {
                    fields.append(HandwrittenField(
                        label: "電話/手機",
                        value: phone,
                        isBlueOrBlack: true
                    ))
                    logger.info("  ✓ 電話/手機: \(phone)")
                }
            }
        }
        // 也在所有文字中搜尋
        if !fields.contains(where: { $0.label == "電話/手機" }) {
            for obs in allObservations {
                if let phone = parsePhone(obs.text) {
                    fields.append(HandwrittenField(
                        label: "電話/手機",
                        value: phone,
                        isBlueOrBlack: false
                    ))
                    logger.info("  ✓ 電話/手機（全文）: \(phone)")
                    break
                }
            }
        }

        logger.info("═══════════════════════════════════════════════════════════")

        // 去重
        var seen = Set<String>()
        return fields.filter { field in
            let key = "\(field.label):\(field.value)"
            if seen.contains(key) { return false }
            seen.insert(key)
            return true
        }
    }

    /// 計算兩個字串的相似度 (0.0 - 1.0)
    private static func stringSimilarity(_ a: String, _ b: String) -> Double {
        if a == b { return 1.0 }
        if a.isEmpty || b.isEmpty { return 0.0 }

        // 使用字元集合比較
        let setA = Set(a)
        let setB = Set(b)
        let intersection = setA.intersection(setB).count
        let union = setA.union(setB).count

        // Jaccard 相似度
        let jaccard = Double(intersection) / Double(union)

        // 也考慮長度差異
        let lenRatio = Double(min(a.count, b.count)) / Double(max(a.count, b.count))

        // 檢查是否包含
        if a.contains(b) || b.contains(a) {
            return max(0.85, jaccard)
        }

        return (jaccard + lenRatio) / 2.0
    }


    private static func findNearbyAmount(
        for obs: TextObservation,
        in allObservations: [TextObservation],
        blueBlackTexts: Set<String>
    ) -> String? {
        // 找右邊或下方的數字
        for other in allObservations {
            let text = other.text.trimmingCharacters(in: .whitespaces)
            // 檢查是否是金額格式
            if let amount = parseAmount(text) {
                // 檢查位置是否在右邊或下方
                let dx = other.boundingBox.midX - obs.boundingBox.midX
                let dy = obs.boundingBox.midY - other.boundingBox.midY  // Vision Y 是反的
                if dx > -0.1 && abs(dy) < 0.1 {  // 大致在右邊
                    // 檢查是否是手寫（藍/黑色）
                    if blueBlackTexts.contains(text) || blueBlackTexts.contains(where: { $0.contains(text.filter { $0.isNumber }) }) {
                        return amount
                    }
                }
            }
        }
        return nil
    }

    private static func parseAmount(_ text: String) -> String? {
        // 匹配金額格式：1000, 1,000, 200 等
        let cleaned = text
            .replacingOccurrences(of: ",", with: "")
            .replacingOccurrences(of: "，", with: "")
            .replacingOccurrences(of: " ", with: "")
            .replacingOccurrences(of: "$", with: "")
            .replacingOccurrences(of: "＄", with: "")
            .trimmingCharacters(in: .whitespaces)

        // 只保留數字
        let digitsOnly = cleaned.filter { $0.isNumber }

        if let num = Int(digitsOnly), num >= 100, num <= 1000000 {
            // 格式化為千分位
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            logger.debug("  金額解析: \"\(text)\" → \(num)")
            return formatter.string(from: NSNumber(value: num))
        }

        // 檢查原始文字是否全為數字
        if let num = Int(cleaned), num >= 100, num <= 1000000 {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            logger.debug("  金額解析: \"\(text)\" → \(num)")
            return formatter.string(from: NSNumber(value: num))
        }

        return nil
    }

    private static func extractDate(from text: String, isHandwritten: Bool) -> String? {
        // 匹配日期格式：115年3月22日, 115 年 3 月 22 日 等
        let pattern = #"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"#
        if let match = text.range(of: pattern, options: .regularExpression) {
            let dateStr = String(text[match])
            // 只有手寫日期才返回
            if isHandwritten {
                return dateStr
            }
        }
        return nil
    }

    /// 解析短日期格式：3/22, 3月22日, 3/22日
    private static func parseShortDate(_ text: String) -> (month: String, day: String)? {
        let cleaned = text.trimmingCharacters(in: .whitespaces)

        // 格式1：3/22 或 3／22
        let slashPattern = #"^(\d{1,2})\s*[/／]\s*(\d{1,2})$"#
        if let match = cleaned.range(of: slashPattern, options: .regularExpression) {
            let matched = String(cleaned[match])
            let parts = matched.split { $0 == "/" || $0 == "／" }
            if parts.count == 2,
               let m = Int(parts[0].trimmingCharacters(in: .whitespaces)),
               let d = Int(parts[1].trimmingCharacters(in: .whitespaces)),
               m >= 1, m <= 12, d >= 1, d <= 31 {
                return (String(m), String(d))
            }
        }

        // 格式2：3月22日 或 3月22
        let chinesePattern = #"^(\d{1,2})\s*月\s*(\d{1,2})\s*日?$"#
        if let match = cleaned.range(of: chinesePattern, options: .regularExpression) {
            let matched = String(cleaned[match])
            // 提取數字
            let nums = matched.components(separatedBy: CharacterSet.decimalDigits.inverted)
                .filter { !$0.isEmpty }
            if nums.count >= 2,
               let m = Int(nums[0]),
               let d = Int(nums[1]),
               m >= 1, m <= 12, d >= 1, d <= 31 {
                return (String(m), String(d))
            }
        }

        return nil
    }

    /// 解析堂次標記：[一], [二], (1), 第一堂, 一堂
    private static func parseServiceNumber(_ text: String) -> Int? {
        let cleaned = text.trimmingCharacters(in: .whitespaces)

        // 格式1：[一], [二], [1], [2] 或 （一）, (1) 等括號格式
        let bracketPattern = #"[\[［【\(（]([一二三四五六1-6])[\]］】\)）]"#
        if let range = cleaned.range(of: bracketPattern, options: .regularExpression) {
            let matched = String(cleaned[range])
            // 提取括號內的字元
            let inner = matched.dropFirst().dropLast()
            if let num = serviceNumberMap[String(inner)] {
                return num
            }
        }

        // 格式2：第一堂, 一堂, 第1堂, 1堂
        let servicePattern = #"第?\s*([一二三四五六1-6])\s*堂"#
        if let range = cleaned.range(of: servicePattern, options: .regularExpression) {
            let matched = String(cleaned[range])
            // 提取數字
            for (key, value) in serviceNumberMap {
                if matched.contains(key) {
                    return value
                }
            }
        }

        return nil
    }

    /// 解析身份證字號（台灣格式：1 英文 + 9 數字）
    private static func parseIdNumber(_ text: String) -> String? {
        let cleaned = text.trimmingCharacters(in: .whitespaces).uppercased()

        // 台灣身份證格式：[A-Z][1-2]\d{8}
        let pattern = #"[A-Z][12]\d{8}"#
        if let range = cleaned.range(of: pattern, options: .regularExpression) {
            let idNum = String(cleaned[range])
            // 驗證長度
            if idNum.count == 10 {
                return idNum
            }
        }

        return nil
    }

    /// 解析電話/手機號碼
    private static func parsePhone(_ text: String) -> String? {
        let cleaned = text.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "-", with: "")
            .replacingOccurrences(of: " ", with: "")

        // 手機格式：09 開頭，10 位數字
        let mobilePattern = #"09\d{8}"#
        if let range = cleaned.range(of: mobilePattern, options: .regularExpression) {
            return String(cleaned[range])
        }

        // 市話格式：02/03/04/05/06/07/08 開頭
        let landlinePattern = #"0[2-8]\d{7,8}"#
        if let range = cleaned.range(of: landlinePattern, options: .regularExpression) {
            return String(cleaned[range])
        }

        return nil
    }

    private static func isChineseName(_ text: String) -> Bool {
        let cleaned = text.trimmingCharacters(in: .whitespaces)

        // 2-4 個中文字
        guard cleaned.count >= 2, cleaned.count <= 4 else {
            logger.debug("  姓名檢查: \"\(cleaned)\" 長度不符 (\(cleaned.count) 字)")
            return false
        }

        // 全部是中文
        guard cleaned.allSatisfy({ $0.isChineseCharacter }) else {
            logger.debug("  姓名檢查: \"\(cleaned)\" 含非中文字元")
            return false
        }

        // 不是標籤 - 更精確的排除列表
        let excludeLabels: Set<String> = [
            "奉獻袋", "電子信箱", "郵寄地址", "收據抬頭", "貧困關懷",
            "偏鄉老人", "愛心小站", "經常費", "項目", "金額", "合計",
            "電話", "手機", "紙本", "電子檔", "電子擔"
        ]
        if excludeLabels.contains(cleaned) {
            logger.debug("  姓名檢查: \"\(cleaned)\" 在排除列表中")
            return false
        }

        // 排除含特定關鍵字的詞
        let excludeKeywords = ["奉獻", "收據", "地址", "信箱", "日期", "姓名", "電話", "手機"]
        for kw in excludeKeywords {
            if cleaned.contains(kw) {
                logger.debug("  姓名檢查: \"\(cleaned)\" 含排除關鍵字 \"\(kw)\"")
                return false
            }
        }

        logger.info("  姓名檢查: \"\(cleaned)\" ✓ 通過")
        return true
    }

    // MARK: - 格式化輸出
    private static func formatOutput(fields: [HandwrittenField]) -> String {
        var lines: [String] = []

        // 按順序輸出：堂次、項目、日期、收據、姓名、身份證、電話
        let order = ["禮拜堂次", "支持項目", "奉獻日期", "奉獻收據", "奉獻人姓名", "身份證字號", "電話/手機"]

        for label in order {
            if let field = fields.first(where: { $0.label == label }) {
                if label == "支持項目" {
                    lines.append("    \(field.value)")
                } else if label == "奉獻人姓名" || label == "身份證字號" || label == "電話/手機" {
                    lines.append("\(label)：\(field.value)")
                } else {
                    lines.append("\(label)  \(field.value)")
                }
            }
        }

        return lines.joined(separator: "\n")
    }
}

// MARK: - Character extension
private extension Character {
    var isChineseCharacter: Bool {
        guard let scalar = unicodeScalars.first else { return false }
        return (0x4E00...0x9FFF).contains(scalar.value) ||  // CJK Unified Ideographs
               (0x3400...0x4DBF).contains(scalar.value)     // CJK Extension A
    }
}
