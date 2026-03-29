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
                Text("後端支援常見圖片格式；拍照後會以 JPEG 上傳。")
            }

            Section {
                Button {
                    isImporterPresented = true
                } label: {
                    Label("選擇檔案（PDF／圖片）", systemImage: "doc.badge.plus")
                }
                .disabled(isUploading)
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
                TextField("自訂 OCR 服務 URL（選填）", text: $customOCRURL)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()
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
            await upload(url: url)
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
            await upload(url: url)
        } catch {
            await MainActor.run { alertMessage = error.localizedDescription }
        }
        await MainActor.run { photoPickerItem = nil }
    }

    private func upload(url: URL) async {
        isUploading = true
        submittedTaskId = nil
        defer { isUploading = false }
        let gotAccess = url.startAccessingSecurityScopedResource()
        defer {
            if gotAccess { url.stopAccessingSecurityScopedResource() }
        }
        do {
            let payload = try await env.client.uploadTask(
                fileURL: url,
                processingMode: "pipeline",
                priority: priority,
                outputFormat: "markdown",
                customOCRURL: customOCRURL.isEmpty ? nil : customOCRURL
            )
            submittedTaskId = payload.task_id
            alertMessage = "已建立任務，可至「任務」分頁查看進度。"
        } catch {
            alertMessage = error.localizedDescription
        }
    }
}
