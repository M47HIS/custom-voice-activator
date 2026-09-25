import AppKit
import Foundation

// Entry point. We set activation policy to .accessory so the app lives in
// the menu bar with no Dock icon. `AppDelegate` and friends are
// @MainActor-isolated, so we hop to the main actor before constructing it.
// The `main` attribute is implied by this being a top-level `main.swift`.

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

// Make sure shared state directories exist before anything else touches them.
_ = AppPaths.ensureDirectories()

let delegate = MainActor.assumeIsolated {
    AppDelegate()
}
app.delegate = delegate

app.run()
