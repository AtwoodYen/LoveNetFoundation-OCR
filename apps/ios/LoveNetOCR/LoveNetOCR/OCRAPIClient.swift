import Foundation
import UniformTypeIdentifiers

enum OCRAPIError: LocalizedError {
    case invalidBaseURL
    case badStatus(Int, String?)
    case decodeFailed(String)
    case emptyData
    case uploadReadFailed

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "伺服器網址無效，請在「設定」中檢查。"
        case let .badStatus(code, body):
            let tail = body.map { ": \($0)" } ?? ""
            return "HTTP \(code)\(tail)"
        case let .decodeFailed(msg):
            return "回應解析失敗：\(msg)"
        case .emptyData:
            return "伺服器未回傳內容。"
        case .uploadReadFailed:
            return "無法讀取所選檔案。"
        }
    }
}

/// 與 LoveNet-OCR FastAPI 後端通訊（前綴 `/api/v1`）
final class OCRAPIClient {
    var baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    private func apiURL(_ path: String) -> URL {
        let segments = ["api", "v1"]
            + path.split(separator: "/")
            .map(String.init)
            .filter { !$0.isEmpty }
        var u = baseURL
        for s in segments {
            u = u.appendingPathComponent(s)
        }
        return u
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder()
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw OCRAPIError.decodeFailed(error.localizedDescription)
        }
    }

    private func get(_ url: URL) async throws -> Data {
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.timeoutInterval = 60
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw OCRAPIError.badStatus(-1, nil) }
        guard (200 ... 299).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8)
            throw OCRAPIError.badStatus(http.statusCode, text)
        }
        return data
    }

    // MARK: - Public

    func fetchSystemHealth() async throws -> SystemHealthPayload {
        let url = apiURL("system/health")
        let data = try await get(url)
        return try decode(SystemHealthPayload.self, from: data)
    }

    func listTasks(status: String? = nil, limit: Int = 50) async throws -> TaskListDataPayload {
        var comp = URLComponents(url: apiURL("tasks"), resolvingAgainstBaseURL: false)!
        var items: [URLQueryItem] = [URLQueryItem(name: "limit", value: "\(limit)")]
        if let status, !status.isEmpty {
            items.append(URLQueryItem(name: "status", value: status))
        }
        comp.queryItems = items
        let data = try await get(comp.url!)
        let env: APIEnvelope<TaskListDataPayload> = try decode(APIEnvelope<TaskListDataPayload>.self, from: data)
        guard env.success, let d = env.data else {
            throw OCRAPIError.decodeFailed(env.message ?? "success=false")
        }
        return d
    }

    func getTask(taskId: String) async throws -> TaskDetailPayload {
        let url = apiURL("tasks/\(taskId)")
        let data = try await get(url)
        let env: APIEnvelope<TaskDetailPayload> = try decode(APIEnvelope<TaskDetailPayload>.self, from: data)
        guard env.success, let d = env.data else {
            throw OCRAPIError.decodeFailed(env.message ?? "success=false")
        }
        return d
    }

    func cancelTask(taskId: String) async throws {
        var req = URLRequest(url: apiURL("tasks/\(taskId)"))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 60
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw OCRAPIError.badStatus(-1, nil) }
        guard (200 ... 299).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8)
            throw OCRAPIError.badStatus(http.statusCode, text)
        }
    }

    func exportXLSX(taskId: String) async throws -> URL {
        let url = apiURL("tasks/\(taskId)/export/xlsx")
        let data = try await get(url)
        let name = "ocr_\(String(taskId.prefix(8))).xlsx"
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try data.write(to: tmp, options: .atomic)
        return tmp
    }

    func uploadTask(
        fileURL: URL,
        processingMode: String = "pipeline",
        priority: Int = 2,
        outputFormat: String = "markdown",
        customOCRURL: String? = nil,
        clientMarkdown: String? = nil,
        formTemplate: String? = nil,
        formAreaImage: Data? = nil,
        formAreaUploadFilename: String? = nil
    ) async throws -> TaskSubmitPayload {
        guard fileURL.isFileURL else { throw OCRAPIError.uploadReadFailed }
        let fileData: Data
        do {
            fileData = try Data(contentsOf: fileURL)
        } catch {
            throw OCRAPIError.uploadReadFailed
        }

        let filename = fileURL.lastPathComponent
        let mime = mimeType(for: fileURL)

        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()

        func append(_ s: String) {
            if let d = s.data(using: .utf8) { body.append(d) }
        }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mime)\r\n\r\n")
        body.append(fileData)
        append("\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"processing_mode\"\r\n\r\n")
        append("\(processingMode)\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"priority\"\r\n\r\n")
        append("\(priority)\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"output_format\"\r\n\r\n")
        append("\(outputFormat)\r\n")

        if let u = customOCRURL?.trimmingCharacters(in: .whitespacesAndNewlines), !u.isEmpty {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"custom_url\"\r\n\r\n")
            append("\(u)\r\n")
        }

        if let cm = clientMarkdown, !cm.isEmpty {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"client_markdown\"\r\n\r\n")
            body.append(Data(cm.utf8))
            append("\r\n")
        }

        if let ft = formTemplate?.trimmingCharacters(in: .whitespacesAndNewlines), !ft.isEmpty {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"form_template\"\r\n\r\n")
            append("\(ft)\r\n")
        }

        // 第二張圖片：表格區域裁切
        if let formData = formAreaImage {
            let formFn: String
            if let n = formAreaUploadFilename, !n.isEmpty {
                formFn = n
            } else {
                formFn = "form_area.png"
            }
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"form_area_file\"; filename=\"\(formFn)\"\r\n")
            append("Content-Type: image/png\r\n\r\n")
            body.append(formData)
            append("\r\n")
        }

        append("--\(boundary)--\r\n")

        var req = URLRequest(url: apiURL("tasks/upload"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 300

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw OCRAPIError.badStatus(-1, nil) }
        guard (200 ... 299).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8)
            throw OCRAPIError.badStatus(http.statusCode, text)
        }

        let env: APIEnvelope<TaskSubmitPayload> = try decode(APIEnvelope<TaskSubmitPayload>.self, from: data)
        guard env.success, let d = env.data else {
            throw OCRAPIError.decodeFailed(env.message ?? "success=false")
        }
        return d
    }

    private func mimeType(for url: URL) -> String {
        if let t = UTType(filenameExtension: url.pathExtension),
           let m = t.preferredMIMEType {
            return m
        }
        return "application/octet-stream"
    }

    // MARK: - Debug Image Upload

    /// 上傳除錯圖片到指定任務的資料夾
    /// - Parameters:
    ///   - image: 要上傳的圖片
    ///   - taskId: 任務 ID
    ///   - filename: 檔案名稱（如 page_0001.png）
    func uploadDebugImage(image: Data, taskId: String, filename: String) async throws {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()

        func append(_ s: String) {
            if let d = s.data(using: .utf8) { body.append(d) }
        }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: image/png\r\n\r\n")
        body.append(image)
        append("\r\n")
        append("--\(boundary)--\r\n")

        var req = URLRequest(url: apiURL("tasks/\(taskId)/debug"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 60

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw OCRAPIError.badStatus(-1, nil) }
        guard (200 ... 299).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8)
            throw OCRAPIError.badStatus(http.statusCode, text)
        }
    }
}
