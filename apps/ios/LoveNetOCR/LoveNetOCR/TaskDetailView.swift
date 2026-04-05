import SwiftUI
import os.log

private let logger = Logger(subsystem: "com.lovenet.ocr", category: "TaskDetail")

struct TaskDetailView: View {
    @EnvironmentObject private var env: AppEnvironment
    let taskId: String

    @State private var detail: TaskDetailPayload?
    @State private var errorText: String?
    @State private var showShareMarkdown = false
    @State private var showShareOffering = false
    @State private var offeringShareText: String?
    @State private var showShareXLSX = false
    @State private var xlsxURL: URL?

    var body: some View {
        Group {
            if let detail {
                List {
                    Section("狀態") {
                        LabeledContent("狀態", value: statusLabel(detail.status))
                        if let p = detail.progress {
                            LabeledContent("進度", value: "\(Int(p))%")
                        }
                        if let step = detail.current_step {
                            LabeledContent("步驟", value: step)
                        }
                        if let err = detail.error_message, !err.isEmpty {
                            Text(err)
                                .font(.footnote)
                                .foregroundStyle(.red)
                        }
                    }

                    offeringResultSections(detail: detail)

                    if let md = detail.full_markdown, !md.isEmpty,
                       detail.offering_display?.hide_raw_text != true
                    {
                        Section("辨識結果（Markdown）") {
                            Text(md)
                                .font(.body)
                                .textSelection(.enabled)
                            Button {
                                showShareMarkdown = true
                            } label: {
                                Label("分享文字", systemImage: "square.and.arrow.up")
                            }
                        }
                    }
                }
            } else if let errorText {
                ContentUnavailableView(
                    "無法載入",
                    systemImage: "exclamationmark.triangle",
                    description: Text(errorText)
                )
            } else {
                ProgressView("載入中…")
            }
        }
        .navigationTitle("任務")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                if detail?.status == "completed" {
                    Button {
                        Task { await exportXLSX() }
                    } label: {
                        Image(systemName: "tablecells")
                    }
                }
                if let d = detail, ["pending", "processing"].contains(d.status) {
                    Button(role: .destructive) {
                        Task { await cancel() }
                    } label: {
                        Image(systemName: "xmark.circle")
                    }
                }
                Button {
                    Task { await refreshOnce() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
        .sheet(isPresented: $showShareMarkdown) {
            if let md = detail?.full_markdown {
                ShareSheet(items: [md])
            }
        }
        .sheet(isPresented: $showShareOffering) {
            if let t = offeringShareText {
                ShareSheet(items: [t])
            }
        }
        .sheet(isPresented: $showShareXLSX) {
            if let xlsxURL {
                ShareSheet(items: [xlsxURL])
            }
        }
        .task(id: taskId) {
            await pollLoop()
        }
    }

    private func pollLoop() async {
        while !Task.isCancelled {
            await refreshOnce()
            guard let d = detail else {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                continue
            }
            if ["completed", "failed", "cancelled"].contains(d.status) {
                break
            }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    private func refreshOnce() async {
        errorText = nil
        do {
            detail = try await env.client.getTask(taskId: taskId)

            // 日誌：顯示服務器返回的結果
            if let d = detail {
                logger.info("📥 任務狀態: \(d.status), 進度: \(d.progress ?? 0)%")
                if let od = d.offering_display {
                    logger.info("📋 offering_display.formatted_text:\n\(od.formatted_text ?? "(空)")")
                    logger.info("📋 offering_display.summary 共 \(od.summary.count) 項:")
                    for row in od.summary {
                        logger.info("  - \(row.label): \(row.value)")
                    }
                }
                if let md = d.full_markdown {
                    logger.info("📄 full_markdown 前 300 字:\n\(String(md.prefix(300)))")
                }
            }
        } catch {
            logger.error("❌ 取得任務失敗: \(error.localizedDescription)")
            errorText = error.localizedDescription
        }
    }

    private func cancel() async {
        do {
            try await env.client.cancelTask(taskId: taskId)
            await refreshOnce()
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func exportXLSX() async {
        do {
            let url = try await env.client.exportXLSX(taskId: taskId)
            xlsxURL = url
            showShareXLSX = true
        } catch {
            errorText = error.localizedDescription
        }
    }

    /// 奉獻袋任務僅顯示擷取之摘要列，不顯示 PDF 印刷全文（後端 hide_raw_text + 不帶 full_markdown）。
    @ViewBuilder
    private func offeringResultSections(detail: TaskDetailPayload) -> some View {
        if let od = detail.offering_display {
            Section("辨識摘要（奉獻袋）") {
                if let ft = od.formatted_text, !ft.isEmpty {
                    Text(ft)
                        .font(.body)
                        .textSelection(.enabled)
                    Button {
                        offeringShareText = ft
                        showShareOffering = true
                    } label: {
                        Label("分享摘要文字", systemImage: "square.and.arrow.up")
                    }
                } else if !od.summary.isEmpty {
                    ForEach(od.summary) { row in
                        LabeledContent(row.label) {
                            Text(row.value)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                } else if !od.checked_items.isEmpty {
                    ForEach(Array(od.checked_items.enumerated()), id: \.offset) { _, item in
                        Label(item, systemImage: "checkmark.square.fill")
                    }
                } else {
                    Text(
                        "目前無法從照片辨識出項目、金額、日期、收據或姓名。請對焦手寫區、避免反光，並確認上傳時已開啟「奉獻袋表單」。"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func statusLabel(_ s: String) -> String {
        switch s {
        case "pending": return "等待中"
        case "processing": return "處理中"
        case "completed": return "已完成"
        case "failed": return "失敗"
        case "cancelled": return "已取消"
        default: return s
        }
    }
}
