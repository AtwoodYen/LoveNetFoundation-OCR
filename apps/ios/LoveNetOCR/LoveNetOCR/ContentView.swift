import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var env: AppEnvironment

    var body: some View {
        TabView {
            NavigationStack {
                UploadView()
            }
            .tabItem {
                Label("拍照", systemImage: "camera.fill")
            }

            NavigationStack {
                TaskListView()
            }
            .tabItem {
                Label("任務", systemImage: "list.bullet.rectangle")
            }

            NavigationStack {
                ServerSettingsView()
            }
            .tabItem {
                Label("設定", systemImage: "gearshape")
            }
        }
    }
}

#Preview {
    ContentView()
        .environmentObject(AppEnvironment())
}
