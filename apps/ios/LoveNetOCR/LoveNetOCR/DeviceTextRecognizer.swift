import UIKit
import Vision

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
    static func recognizeText(in image: UIImage) async throws -> String {
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
                    cont.resume(returning: "")
                    return
                }
                let lines = observations.compactMap { $0.topCandidates(1).first?.string }
                cont.resume(returning: lines.joined(separator: "\n"))
            }
            request.recognitionLevel = .accurate
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
}
