import SwiftUI

struct TaskListView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var items: [TaskSummaryPayload] = []
    @State private var filter: String = ""
    @State private var isLoading = false
    @State private var errorText: String?

    var body: some View {
        List {
            if let errorText {
                Section {
                    Text(errorText)
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }
            ForEach(items, id: \.task_id) { t in
                NavigationLink(value: t.task_id) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(t.original_filename ?? t.task_id.prefix(8) + "…")
                            .font(.headline)
                        HStack {
                            Text(statusLabel(t.status))
                                .font(.caption)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(statusColor(t.status).opacity(0.2))
                                .foregroundStyle(statusColor(t.status))
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                            if let p = t.progress {
                                Text("\(Int(p))%")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let step = t.current_step {
                            Text(step)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("任務")
        .navigationDestination(for: String.self) { id in
            TaskDetailView(taskId: id)
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .disabled(isLoading)
            }
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Button("全部") { filter = ""; Task { await load() } }
                    Button("等待中") { filter = "pending"; Task { await load() } }
                    Button("處理中") { filter = "processing"; Task { await load() } }
                    Button("已完成") { filter = "completed"; Task { await load() } }
                    Button("失敗") { filter = "failed"; Task { await load() } }
                } label: {
                    Label("篩選", systemImage: "line.3.horizontal.decrease.circle")
                }
            }
        }
        .refreshable {
            await load()
        }
        .task {
            await load()
        }
    }

    private func load() async {
        isLoading = true
        errorText = nil
        defer { isLoading = false }
        do {
            let f = filter.isEmpty ? nil : filter
            let data = try await env.client.listTasks(status: f, limit: 80)
            items = data.tasks
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func statusLabel(_ s: String) -> String {
        switch s {
        case "pending": return "等待"
        case "processing": return "處理中"
        case "completed": return "完成"
        case "failed": return "失敗"
        case "cancelled": return "已取消"
        default: return s
        }
    }

    private func statusColor(_ s: String) -> Color {
        switch s {
        case "completed": return .green
        case "failed": return .red
        case "processing": return .blue
        case "pending": return .orange
        case "cancelled": return .gray
        default: return .primary
        }
    }
}
