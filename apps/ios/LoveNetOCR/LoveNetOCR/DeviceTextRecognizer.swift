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
    /// 依版面由上而下、由左而右；候選字串取較長者（較易保留千分位逗號等）。
    private static func bestCandidateString(from observation: VNRecognizedTextObservation) -> String {
        let cands = observation.topCandidates(5).map(\.string)
        return cands.max(by: { $0.count < $1.count }) ?? ""
    }

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
                let sorted = observations.sorted { a, b in
                    let dy = abs(a.boundingBox.midY - b.boundingBox.midY)
                    if dy > 0.018 {
                        return a.boundingBox.midY > b.boundingBox.midY
                    }
                    return a.boundingBox.midX < b.boundingBox.midX
                }
                let lines = sorted.map { bestCandidateString(from: $0) }
                cont.resume(returning: lines.joined(separator: "\n"))
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
}
