import SwiftUI

@main
struct LoveNetOCRApp: App {
    @StateObject private var env = AppEnvironment()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(env)
        }
    }
}
