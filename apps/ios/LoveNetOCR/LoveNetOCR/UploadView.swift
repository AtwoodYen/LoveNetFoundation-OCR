import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct UploadView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var isImporterPresented = false
    @State private var showCamera = false
    @State private var photoPickerItem: PhotosPickerItem?
    @State private var priority = 2
    @State private var customOCRURL = ""
    /// 預設開啟：用 Apple Vision 在裝置辨識，後端不再呼叫 GLM-OCR 服務（免 API Key）。
    @State private var useDeviceOCR = true
    @State private var isUploading = false
    @State private var alertMessage: String?
    @State private var submittedTaskId: String?

    private let fileTypes: [UTType] = [.pdf, .image, .plainText]

    private var cameraAvailable: Bool {
        UIImagePickerController.isSourceTypeAvailable(.camera)
    }

    var body: some View {
        Form {
            Section {
                Toggle("在手機辨識文字（Apple Vision）", isOn: $useDeviceOCR)
                Text("開啟時不上傳到智譜或自架 GLM 服務，只在裝置上辨識後把文字交給後端整理；關閉則使用後端 pipeline（需有可連線的版面 OCR 服務）。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } header: {
                Text("辨識方式")
            }

            Section {
                Button {
                    showCamera = true
                } label: {
                    Label("拍攝文件並辨識", systemImage: "camera.fill")
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
                if !useDeviceOCR {
                    TextField("自訂 OCR 服務 URL（選填）", text: $customOCRURL)
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
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

    private func jpegTempURL(from image: UIImage) throws -> URL {
        guard let data = image.jpegData(compressionQuality: 0.92) else {
            throw URLError(.cannotCreateFile)
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("ocr_camera_\(UUID().uuidString).jpg")
        try data.write(to: url, options: .atomic)
        return url
    }

    private func uploadCapturedImage(_ image: UIImage) async {
        do {
            let url = try jpegTempURL(from: image)
            if useDeviceOCR {
                let text = try await DeviceTextRecognizer.recognizeText(in: image)
                guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    alertMessage = "未辨識到文字，請改拍清楚或關閉「在手機辨識」改用後端 OCR。"
                    return
                }
                await performUpload(fileURL: url, clientMarkdown: text)
            } else {
                await performUpload(fileURL: url, clientMarkdown: nil)
            }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func loadPhotoFromLibrary(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                await MainActor.run { alertMessage = "無法讀取照片。" }
                return
            }
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("ocr_library_\(UUID().uuidString).jpg")
            if let uiImage = UIImage(data: data), let jpeg = uiImage.jpegData(compressionQuality: 0.92) {
                try jpeg.write(to: url, options: .atomic)
            } else {
                try data.write(to: url, options: .atomic)
            }

            if useDeviceOCR, let uiImage = UIImage(data: data) {
                let text = try await DeviceTextRecognizer.recognizeText(in: uiImage)
                guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    await MainActor.run {
                        alertMessage = "未辨識到文字。"
                        photoPickerItem = nil
                    }
                    return
                }
                await performUpload(fileURL: url, clientMarkdown: text)
            } else {
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

    private func performUpload(fileURL: URL, clientMarkdown: String?) async {
        isUploading = true
        submittedTaskId = nil
        defer { isUploading = false }
        do {
            let mode = clientMarkdown != nil ? "client_vision" : "pipeline"
            let payload = try await env.client.uploadTask(
                fileURL: fileURL,
                processingMode: mode,
                priority: priority,
                outputFormat: "markdown",
                customOCRURL: useDeviceOCR ? nil : (customOCRURL.isEmpty ? nil : customOCRURL),
                clientMarkdown: clientMarkdown
            )
            submittedTaskId = payload.task_id
            alertMessage = "已建立任務，可至「任務」分頁查看進度。"
        } catch {
            alertMessage = error.localizedDescription
        }
    }
}
