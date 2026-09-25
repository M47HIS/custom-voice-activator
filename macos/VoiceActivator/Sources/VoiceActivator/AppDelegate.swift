import AppKit
import Combine
import Foundation
import os.log
import SwiftUI

/// NSApplication delegate. We use it to bootstrap the supervisor and wire
/// termination handling. The SwiftUI App is not used as the @main because
/// `main.swift` needs to configure `NSApplication.setActivationPolicy(.accessory)`
/// *before* the SwiftUI runtime spins up a Dock icon.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let supervisor = ProcessSupervisor()
    let backend = BackendClient()
    let settings = SettingsStore()

    private var cancellables = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Hook backend client into supervisor (so it can poll status etc).
        supervisor.attach(backend: backend, settings: settings)

        // Kick off background startup: load / fetch auth token, start backend,
        // start client.
        Task { @MainActor in
            await supervisor.bootstrap()
        }

        // Install SwiftUI menu bar scene via NSApp. We use an NSHostingController
        // approach because we already started NSApplication manually. A separate
        // StatusBarController manages the NSStatusItem.
        StatusBarController.shared.install(
            supervisor: supervisor,
            backend: backend,
            settings: settings
        )
    }

    func applicationWillTerminate(_ notification: Notification) {
        supervisor.requestShutdown()
    }
}
