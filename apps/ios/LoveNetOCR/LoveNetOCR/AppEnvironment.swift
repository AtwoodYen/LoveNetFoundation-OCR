import Combine
import Foundation
import SwiftUI

/// 全域後端基底 URL（真機請改為 Mac 區網 IP，例如 http://192.168.1.20:8000）
final class AppEnvironment: ObservableObject {
    private static let storageKey = "LoveNetOCR.apiBaseURL"

    @Published var baseURLString: String {
        didSet {
            UserDefaults.standard.set(baseURLString, forKey: Self.storageKey)
            rebuildClient()
        }
    }

    @Published private(set) var client: OCRAPIClient

    init() {
        let initial = UserDefaults.standard.string(forKey: Self.storageKey)
            ?? "http://127.0.0.1:8000"
        let normalized = Self.trimmedBaseURLString(
            from: initial.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        guard let url = URL(string: normalized) else {
            fatalError("Invalid default API URL")
        }
        client = OCRAPIClient(baseURL: url)
        baseURLString = normalized
    }

    func rebuildClient() {
        let trimmed = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = URL(string: trimmed) {
            client = OCRAPIClient(baseURL: url)
        }
    }

    /// 儲存前正規化（避免尾端斜線造成雙斜線）
    func normalizedBaseURLString() -> String {
        Self.trimmedBaseURLString(from: baseURLString)
    }

    static func trimmedBaseURLString(from raw: String) -> String {
        raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}
