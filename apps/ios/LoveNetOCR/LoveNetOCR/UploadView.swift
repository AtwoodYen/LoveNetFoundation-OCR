import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "Upload")

/// OCR 辨識方式
enum OCRMethod: String, CaseIterable, Identifiable {
    case deviceVision = "device_vision"      // 裝置 Apple Vision
    case backendGLM = "pipeline"             // 後端 GLM OCR
    case backendGoogleVision = "google_vision" // 後端 Google Vision

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .deviceVision: return "裝置辨識（Apple Vision）"
        case .backendGLM: return "後端 GLM OCR"
        case .backendGoogleVision: return "後端 Google Vision"
        }
    }

    var description: String {
        switch self {
        case .deviceVision:
            return "在手機上使用 Apple Vision 辨識，免 API Key，辨識後上傳文字給後端整理。"
        case .backendGLM:
            return "上傳圖片到後端，使用 GLM（智譜）版面 OCR 服務進行辨識。"
        case .backendGoogleVision:
            return "上傳圖片到後端，使用 Google Cloud Vision API 進行 OCR 辨識。"
        }
    }

    /// 是否在裝置端進行 OCR
    var isDeviceOCR: Bool {
        self == .deviceVision
    }

    /// 後端處理模式名稱
    var processingMode: String {
        rawValue
    }
}

struct UploadView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var isImporterPresented = false
    @State private var showCamera = false
    @State private var showEnvelopeCamera = false
    @State private var photoPickerItem: PhotosPickerItem?
    @State private var priority = 2
    @State private var customOCRURL = ""
    /// OCR 辨識方式
    @State private var ocrMethod: OCRMethod = .deviceVision
    /// 後端 pipeline 時裁切奉獻袋印刷區，只對手寫區做版面 OCR。
    @State private var useOfferingEnvelopeCrop = false
    /// 預處理除錯模式：每一步都上傳到 Server
    @State private var preprocessDebugMode = false
    @State private var isUploading = false
    @State private var alertMessage: String?
    @State private var submittedTaskId: String?

    private let fileTypes: [UTType] = [.pdf, .image, .plainText]

    /// 向後相容：是否使用裝置 OCR
    private var useDeviceOCR: Bool { ocrMethod.isDeviceOCR }

    /// 奉獻袋表單說明文字
    private var offeringEnvelopeDescription: String {
        switch ocrMethod {
        case .deviceVision:
            return "任務完成後以奉獻袋規則擷取金額、日期、勾選項（僅顯示有辨識到的欄位）。"
        case .backendGLM:
            return "後端裁切手寫區、藍黑筆跡濾波後再送 GLM 版面 OCR；結果同樣以奉獻袋摘要顯示。"
        case .backendGoogleVision:
            return "後端使用 Google Vision OCR 辨識後，以奉獻袋規則擷取金額、日期等欄位。"
        }
    }

    private var cameraAvailable: Bool {
        UIImagePickerController.isSourceTypeAvailable(.camera)
    }

    var body: some View {
        Form {
            Section {
                Picker("OCR 引擎", selection: $ocrMethod) {
                    ForEach(OCRMethod.allCases) { method in
                        Text(method.displayName).tag(method)
                    }
                }
                .pickerStyle(.menu)

                Text(ocrMethod.description)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } header: {
                Text("辨識方式")
            }

            Section {
                // 奉獻袋專用相機（有引導框）
                if useOfferingEnvelopeCrop {
                    Button {
                        showEnvelopeCamera = true
                    } label: {
                        Label("掃描奉獻袋", systemImage: "viewfinder")
                    }
                    .disabled(isUploading || !cameraAvailable)

                    Text("使用引導框對準奉獻袋拍照，提高辨識準確度。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                // 一般相機
                Button {
                    showCamera = true
                } label: {
                    Label(useOfferingEnvelopeCrop ? "一般拍照" : "拍攝文件並辨識", systemImage: "camera.fill")
                }
                .disabled(isUploading || !cameraAvailable)

                if !cameraAvailable {
                    Text("此裝置無相機（例如模擬器）。請改用下方「從相簿」或「選擇檔案」。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                PhotosPicker(selection: $photoPickerItem, matching: .images, photoLibrary: .shared()) {
                    Label("從相簿選擇照片", systemImage: "photo.on.rectangle.angled")
                }
                .disabled(isUploading)
            } header: {
                Text("圖片 OCR")
            } footer: {
                Text(useDeviceOCR ? "拍照或相簿圖片會先經 Vision 辨識再上傳。" : "圖片將交由後端完整 pipeline 處理。")
            }

            Section {
                Button {
                    isImporterPresented = true
                } label: {
                    Label("選擇檔案（PDF／圖片）", systemImage: "doc.badge.plus")
                }
                .disabled(isUploading)
                if useDeviceOCR {
                    Text("若選 PDF，請先關閉「在手機辨識」，改由後端處理（需 OCR 服務）。")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            } header: {
                Text("其他")
            }

            Section {
                Picker("優先順序", selection: $priority) {
                    Text("低 (1)").tag(1)
                    Text("正常 (2)").tag(2)
                    Text("高 (3)").tag(3)
                    Text("緊急 (4)").tag(4)
                }
                // 只有 GLM 模式才顯示自訂 URL 選項
                if ocrMethod == .backendGLM {
                    TextField("自訂 OCR 服務 URL（選填）", text: $customOCRURL)
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                }
                Toggle("奉獻袋表單", isOn: $useOfferingEnvelopeCrop)
                Text(offeringEnvelopeDescription)
                    .font(.footnote)
                    .foregroundStyle(.secondary)

                // 只有後端 OCR + 奉獻袋模式才顯示除錯選項
                if !useDeviceOCR && useOfferingEnvelopeCrop {
                    Toggle("預處理除錯模式", isOn: $preprocessDebugMode)
                    Text("啟用後，每一步預處理結果都會上傳到 Server 的任務資料夾，便於除錯。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("選項")
            }

            if isUploading {
                Section {
                    HStack {
                        ProgressView()
                        Text("上傳中…")
                    }
                }
            }

            if let tid = submittedTaskId {
                Section {
                    NavigationLink("查看任務 \(String(tid.prefix(8)))…") {
                        TaskDetailView(taskId: tid)
                    }
                }
            }
        }
        .navigationTitle("拍照 OCR")
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker(
                onCapture: { image in
                    showCamera = false
                    Task { await uploadCapturedImage(image) }
                },
                onDismiss: {
                    showCamera = false
                }
            )
            .ignoresSafeArea()
        }
        .fullScreenCover(isPresented: $showEnvelopeCamera) {
            EnvelopeCameraView(
                onCapture: { image in
                    showEnvelopeCamera = false
                    Task { await uploadCapturedImage(image) }
                },
                onDismiss: {
                    showEnvelopeCamera = false
                }
            )
        }
        .onChange(of: photoPickerItem) { _, newItem in
            guard let newItem else { return }
            Task { await loadPhotoFromLibrary(newItem) }
        }
        .fileImporter(
            isPresented: $isImporterPresented,
            allowedContentTypes: fileTypes,
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case let .success(urls):
                guard let url = urls.first else { return }
                Task { await upload(url: url) }
            case let .failure(err):
                alertMessage = err.localizedDescription
            }
        }
        .alert("提示", isPresented: Binding(
            get: { alertMessage != nil },
            set: { if !$0 { alertMessage = nil } }
        )) {
            Button("好", role: .cancel) {}
        } message: {
            Text(alertMessage ?? "")
        }
    }

    private func pngTempURL(from image: UIImage) throws -> URL {
        guard let data = image.pngData() else {
            throw URLError(.cannotCreateFile)
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("ocr_camera_\(UUID().uuidString).png")
        try data.write(to: url, options: .atomic)
        return url
    }

    private func uploadCapturedImage(_ image: UIImage) async {
        do {
            logger.info("📷 拍照完成，圖片尺寸: \(Int(image.size.width))x\(Int(image.size.height))")

            if useDeviceOCR {
                if useOfferingEnvelopeCrop {
                    // 奉獻袋專用處理：顏色過濾 + 智能提取
                    logger.info("🎨 使用奉獻袋專用處理器...")
                    let result = try await OfferingEnvelopeProcessor.process(image: image)

                    logger.info("📝 原始 OCR 結果:\n\(result.rawOCRText)")
                    logger.info("📄 手寫欄位提取結果:")
                    for field in result.handwrittenFields {
                        logger.info("  [\(field.label)] \(field.value) (藍/黑=\(field.isBlueOrBlack))")
                    }
                    logger.info("📄 最終格式化輸出:\n\(result.formattedOutput)")

                    guard !result.formattedOutput.isEmpty else {
                        logger.warning("⚠️ 未提取到有效欄位")
                        alertMessage = "未識別到手寫內容，請確保拍攝清晰且包含手寫部分。"
                        return
                    }

                    // 保存過濾後的圖片
                    let url = try pngTempURL(from: result.filteredImage)
                    logger.info("📤 上傳過濾後圖片，格式化輸出長度: \(result.formattedOutput.count)")
                    await performUpload(fileURL: url, clientMarkdown: result.formattedOutput)
                } else {
                    // 一般 OCR 模式
                    logger.info("🔍 開始裝置端 OCR 辨識...")
                    let text = try await DeviceTextRecognizer.recognizeText(in: image)

                    logger.info("📝 OCR 辨識結果長度: \(text.count) 字元")
                    logger.info("📄 完整 OCR 結果:\n\(text)")

                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                        logger.warning("⚠️ OCR 結果為空")
                        alertMessage = "未辨識到文字，請改拍清楚或關閉「在手機辨識」改用後端 OCR。"
                        return
                    }

                    let url = try pngTempURL(from: image)
                    logger.info("📤 上傳到伺服器，client_markdown 長度: \(text.count)")
                    await performUpload(fileURL: url, clientMarkdown: text)
                }
            } else {
                // 後端 OCR 模式
                var imageToUpload = image
                var formAreaData: Data? = nil

                // 如果是奉獻袋模式，先進行預處理再上傳
                if useOfferingEnvelopeCrop {
                    if preprocessDebugMode {
                        // 除錯模式：先建立任務，然後每一步都上傳
                        logger.info("🔧 奉獻袋除錯模式：每一步都上傳到 Server...")

                        // 先上傳原圖建立任務
                        let originalUrl = try pngTempURL(from: image)
                        let taskResult = await performUploadAndGetTaskId(fileURL: originalUrl, clientMarkdown: nil)

                        if let taskId = taskResult {
                            // 使用除錯預處理，每一步都上傳
                            imageToUpload = await OfferingEnvelopeProcessor.preprocessWithDebug(
                                image: image,
                                client: env.client,
                                taskId: taskId,
                                options: .conservative  // 使用保守設定，不做二值化和降噪
                            )
                            logger.info("✅ 除錯預處理完成")
                        }
                        return  // 任務已建立，不需要再次上傳
                    } else {
                        // 一般模式：使用保守預處理
                        logger.info("🔧 奉獻袋模式：進行前處理（保守模式）...")
                        imageToUpload = OfferingEnvelopeProcessor.preprocessForUpload(
                            image: image,
                            options: .conservative  // 不做二值化和降噪
                        )
                        logger.info("✅ 預處理完成，圖片尺寸: \(Int(imageToUpload.size.width))x\(Int(imageToUpload.size.height))")

                        // 偵測並裁切表格區域（用於金額 OCR 加強）
                        logger.info("📐 偵測並裁切表格區域...")
                        if let formAreaImage = image.cropFormArea() {
                            logger.info("✅ 表格區域裁切完成: \(Int(formAreaImage.size.width))x\(Int(formAreaImage.size.height))")
                            formAreaData = formAreaImage.pngData()
                        } else {
                            logger.warning("⚠️ 無法偵測表格區域，將只上傳完整信封圖片")
                        }
                    }
                }

                let url = try pngTempURL(from: imageToUpload)
                logger.info("📤 上傳到伺服器（後端 OCR 模式）")
                await performUpload(fileURL: url, clientMarkdown: nil, formAreaImage: formAreaData)
            }
        } catch {
            logger.error("❌ 上傳失敗: \(error.localizedDescription)")
            alertMessage = error.localizedDescription
        }
    }

    private func loadPhotoFromLibrary(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                await MainActor.run { alertMessage = "無法讀取照片。" }
                return
            }

            guard let uiImage = UIImage(data: data) else {
                await MainActor.run { alertMessage = "無法解析圖片。" }
                return
            }

            if useDeviceOCR {
                if useOfferingEnvelopeCrop {
                    // 奉獻袋專用處理
                    logger.info("🎨 相簿圖片：使用奉獻袋專用處理器...")
                    let result = try await OfferingEnvelopeProcessor.process(image: uiImage)

                    guard !result.formattedOutput.isEmpty else {
                        await MainActor.run {
                            alertMessage = "未識別到手寫內容。"
                            photoPickerItem = nil
                        }
                        return
                    }

                    let url = try pngTempURL(from: result.filteredImage)
                    await performUpload(fileURL: url, clientMarkdown: result.formattedOutput)
                } else {
                    // 一般 OCR
                    let text = try await DeviceTextRecognizer.recognizeText(in: uiImage)
                    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                        await MainActor.run {
                            alertMessage = "未辨識到文字。"
                            photoPickerItem = nil
                        }
                        return
                    }
                    let url = try pngTempURL(from: uiImage)
                    await performUpload(fileURL: url, clientMarkdown: text)
                }
            } else {
                // 後端 OCR 模式
                var imageToUpload = uiImage

                // 如果是奉獻袋模式，先進行預處理再上傳
                if useOfferingEnvelopeCrop {
                    logger.info("🔧 相簿圖片：奉獻袋模式進行前處理...")
                    imageToUpload = OfferingEnvelopeProcessor.preprocessForUpload(image: uiImage)
                }

                let url = try pngTempURL(from: imageToUpload)
                await performUpload(fileURL: url, clientMarkdown: nil)
            }
        } catch {
            await MainActor.run { alertMessage = error.localizedDescription }
        }
        await MainActor.run { photoPickerItem = nil }
    }

    private func upload(url: URL) async {
        let ext = url.pathExtension.lowercased()
        if useDeviceOCR && ext == "pdf" {
            alertMessage = "「在手機辨識」不支援 PDF。請關閉該選項改由後端處理，或改選圖片檔。"
            return
        }

        let gotAccess = url.startAccessingSecurityScopedResource()
        defer {
            if gotAccess { url.stopAccessingSecurityScopedResource() }
        }

        if useDeviceOCR {
            guard let data = try? Data(contentsOf: url),
                  let uiImage = UIImage(data: data)
            else {
                alertMessage = "無法載入圖片以進行辨識。"
                return
            }
            do {
                let text = try await DeviceTextRecognizer.recognizeText(in: uiImage)
                guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    alertMessage = "未辨識到文字。"
                    return
                }
                await performUpload(fileURL: url, clientMarkdown: text)
            } catch {
                alertMessage = error.localizedDescription
            }
            return
        }

        await performUpload(fileURL: url, clientMarkdown: nil)
    }

    private func performUpload(fileURL: URL, clientMarkdown: String?, formAreaImage: Data? = nil) async {
        isUploading = true
        submittedTaskId = nil
        defer { isUploading = false }
        do {
            // 決定處理模式
            let mode: String
            if clientMarkdown != nil {
                // 裝置端已辨識，使用 client_vision 模式
                mode = "client_vision"
            } else {
                // 後端辨識，根據選擇的 OCR 方法
                mode = ocrMethod.processingMode
            }

            logger.info("📤 上傳任務，處理模式: \(mode), 表格區域圖片: \(formAreaImage != nil ? "有" : "無")")

            let formTpl: String? = useOfferingEnvelopeCrop ? "offering_envelope" : nil
            let payload = try await env.client.uploadTask(
                fileURL: fileURL,
                processingMode: mode,
                priority: priority,
                outputFormat: "markdown",
                customOCRURL: useDeviceOCR ? nil : (customOCRURL.isEmpty ? nil : customOCRURL),
                clientMarkdown: clientMarkdown,
                formTemplate: formTpl,
                formAreaImage: formAreaImage
            )
            submittedTaskId = payload.task_id
            alertMessage = "已建立任務，可至「任務」分頁查看進度。"
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    /// 上傳檔案並返回任務 ID（用於除錯模式）
    private func performUploadAndGetTaskId(fileURL: URL, clientMarkdown: String?) async -> String? {
        isUploading = true
        submittedTaskId = nil
        defer { isUploading = false }
        do {
            let mode = ocrMethod.processingMode
            let formTpl: String? = useOfferingEnvelopeCrop ? "offering_envelope" : nil

            logger.info("📤 上傳任務（除錯模式），處理模式: \(mode)")

            let payload = try await env.client.uploadTask(
                fileURL: fileURL,
                processingMode: mode,
                priority: priority,
                outputFormat: "markdown",
                customOCRURL: customOCRURL.isEmpty ? nil : customOCRURL,
                clientMarkdown: clientMarkdown,
                formTemplate: formTpl
            )
            submittedTaskId = payload.task_id
            alertMessage = "已建立任務（除錯模式），預處理圖片正在上傳中..."
            return payload.task_id
        } catch {
            alertMessage = error.localizedDescription
            return nil
        }
    }
}
