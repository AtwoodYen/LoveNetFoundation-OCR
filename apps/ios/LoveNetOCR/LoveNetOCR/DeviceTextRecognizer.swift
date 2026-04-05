import UIKit
import Vision
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "DeviceOCR")

enum DeviceOCRError: LocalizedError {
    case noCGImage

    var errorDescription: String? {
        switch self {
        case .noCGImage:
            return "無法從圖片取得影像資料。"
        }
    }
}

/// 使用 Apple Vision 在裝置上辨識文字（不需智譜或其他雲端 API）。
enum DeviceTextRecognizer {
    /// 依版面由上而下、由左而右；候選字串取較長者（較易保留千分位逗號等）。
    private static func bestCandidateString(from observation: VNRecognizedTextObservation) -> String {
        let cands = observation.topCandidates(5).map(\.string)
        return cands.max(by: { $0.count < $1.count }) ?? ""
    }

    static func recognizeText(in image: UIImage) async throws -> String {
        guard let cgImage = image.cgImage else {
            throw DeviceOCRError.noCGImage
        }
        logger.info("🔍 開始 Vision OCR，圖片尺寸: \(cgImage.width)x\(cgImage.height)")

        return try await withCheckedThrowingContinuation { cont in
            let request = VNRecognizeTextRequest { request, error in
                if let error {
                    logger.error("❌ Vision OCR 錯誤: \(error.localizedDescription)")
                    cont.resume(throwing: error)
                    return
                }
                guard let observations = request.results as? [VNRecognizedTextObservation] else {
                    logger.warning("⚠️ Vision OCR 無結果")
                    cont.resume(returning: "")
                    return
                }

                logger.info("📝 Vision 辨識到 \(observations.count) 個文字區塊")

                // 記錄每個區塊的原始位置和內容
                for (i, obs) in observations.enumerated() {
                    let box = obs.boundingBox
                    let text = bestCandidateString(from: obs)
                    logger.debug("  [\(i)] y=\(String(format: "%.3f", box.midY)) x=\(String(format: "%.3f", box.midX)) → \"\(text)\"")
                }

                let sorted = observations.sorted { a, b in
                    let dy = abs(a.boundingBox.midY - b.boundingBox.midY)
                    if dy > 0.018 {
                        return a.boundingBox.midY > b.boundingBox.midY
                    }
                    return a.boundingBox.midX < b.boundingBox.midX
                }
                let lines = sorted.map { bestCandidateString(from: $0) }
                let result = lines.joined(separator: "\n")

                logger.info("✅ Vision OCR 完成，總共 \(lines.count) 行")
                logger.info("📄 OCR 結果前 500 字:\n\(String(result.prefix(500)))")

                cont.resume(returning: result)
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
                logger.error("❌ Vision handler 執行失敗: \(error.localizedDescription)")
                cont.resume(throwing: error)
            }
        }
    }
}
