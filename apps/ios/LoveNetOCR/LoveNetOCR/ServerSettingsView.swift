import SwiftUI
import UIKit

struct ServerSettingsView: View {
    @EnvironmentObject private var env: AppEnvironment
    @State private var draftURL: String = ""
    @State private var message: String?
    @State private var isChecking = false

    // 按鈕動畫狀態
    @State private var isApplyPressed = false
    @State private var isTestPressed = false

    var body: some View {
        Form {
            Section {
                TextField("後端網址", text: $draftURL)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()
                Text("模擬器可用 `http://127.0.0.1:8000`。真機請填 Mac 的區網 IP，並確認 Mac 與手機同一 Wi‑Fi、防火牆允許 8000 埠。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } header: {
                Text("API 基底網址")
            }

            Section {
                Button {
                    triggerButtonFeedback(isPressed: $isApplyPressed)
                    applyAndSave()
                } label: {
                    Label("套用並儲存", systemImage: "checkmark.circle")
                        .foregroundColor(isApplyPressed ? .yellow : .red)
                }
                .scaleEffect(isApplyPressed ? 1.08 : 1.0)
                .animation(.easeInOut(duration: 0.15), value: isApplyPressed)

                Button(role: .none) {
                    triggerButtonFeedback(isPressed: $isTestPressed)
                    Task { await testConnection() }
                } label: {
                    if isChecking {
                        Label("測試連線中…", systemImage: "antenna.radiowaves.left.and.right")
                            .foregroundColor(.orange)
                    } else {
                        Label("測試連線", systemImage: "network")
                            .foregroundColor(isTestPressed ? .yellow : .red)
                    }
                }
                .scaleEffect(isTestPressed ? 1.08 : 1.0)
                .animation(.easeInOut(duration: 0.15), value: isTestPressed)
                .disabled(isChecking)
            }

            if let message {
                Section {
                    Text(message)
                        .font(.footnote)
                }
            }
        }
        .navigationTitle("設定")
        .onAppear {
            draftURL = env.baseURLString
        }
    }

    private func applyAndSave() {
        let t = AppEnvironment.trimmedBaseURLString(from: draftURL)
        draftURL = t
        env.baseURLString = t
        message = "已更新為 \(t)"
    }

    private func testConnection() async {
        isChecking = true
        message = nil
        defer { isChecking = false }
        let t = AppEnvironment.trimmedBaseURLString(from: draftURL)
        guard let url = URL(string: t) else {
            message = "網址格式不正確。"
            return
        }
        let c = OCRAPIClient(baseURL: url)
        do {
            let h = try await c.fetchSystemHealth()
            message = "連線成功：\(h.status)，版本 \(h.version ?? "?")，Worker \(h.active_workers ?? 0)/\(h.workers_count ?? 0)"
        } catch {
            message = "連線失敗：\(error.localizedDescription)"
        }
    }

    /// 觸發按鈕回饋動畫
    private func triggerButtonFeedback(isPressed: Binding<Bool>) {
        // 觸覺回饋
        let generator = UIImpactFeedbackGenerator(style: .medium)
        generator.impactOccurred()

        // 視覺回饋：放大 + 變色
        isPressed.wrappedValue = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            isPressed.wrappedValue = false
        }
    }
}
