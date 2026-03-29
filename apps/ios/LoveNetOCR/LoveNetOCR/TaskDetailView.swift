import SwiftUI

struct TaskDetailView: View {
    @EnvironmentObject private var env: AppEnvironment
    let taskId: String

    @State private var detail: TaskDetailPayload?
    @State private var errorText: String?
    @State private var showShareMarkdown = false
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

                    if let md = detail.full_markdown, !md.isEmpty {
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
        } catch {
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
