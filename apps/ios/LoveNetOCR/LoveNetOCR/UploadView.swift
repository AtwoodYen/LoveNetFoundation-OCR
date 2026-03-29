import SwiftUI
import UniformTypeIdentifiers

struct UploadView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var isImporterPresented = false
    @State private var priority = 2
    @State private var customOCRURL = ""
    @State private var isUploading = false
    @State private var alertMessage: String?
    @State private var submittedTaskId: String?

    private let types: [UTType] = [.pdf, .image, .plainText]

    var body: some View {
        Form {
            Section {
                Button {
                    isImporterPresented = true
                } label: {
                    Label("選擇檔案（PDF / 圖片）", systemImage: "doc.badge.plus")
                }
                .disabled(isUploading)
            } header: {
                Text("上傳 OCR")
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
        .navigationTitle("上傳")
        .fileImporter(
            isPresented: $isImporterPresented,
            allowedContentTypes: types,
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
