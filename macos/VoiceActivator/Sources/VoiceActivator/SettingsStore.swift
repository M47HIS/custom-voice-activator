import Combine
import Foundation
import SwiftUI

/// UI-facing settings model. The local Python config is authoritative so the
/// menu-bar app continues to work without a Docker backend.
@MainActor
final class SettingsStore: ObservableObject {
    @Published var hotkey: String = BackendSettings.default.hotkey
    @Published var controlHotkey: String = "ctrl+shift+space"
    @Published var controlSilenceDB: Double = -38
    @Published var mode: String = BackendSettings.default.mode  // "hold" | "toggle"
    @Published var action: String = BackendSettings.default.action
    @Published var isSaving: Bool = false
    @Published var saveError: String? = nil
    @Published var saveSuccessAt: Date? = nil

    /// Whether local state diverges from the last saved local configuration.
    var isDirty: Bool {
        BackendSettings(hotkey: hotkey, mode: mode, action: action) != snapshot
            || controlHotkey != savedControlHotkey
            || controlSilenceDB != savedControlSilenceDB
    }

    private var snapshot: BackendSettings = .default
    private var savedControlHotkey = "ctrl+shift+space"
    private var savedControlSilenceDB: Double = -38
    private var feedbackResetTask: Task<Void, Never>?

    func apply(remote: BackendSettings) {
        hotkey = remote.hotkey
        mode = remote.mode
        action = "clipboard"
        snapshot = BackendSettings(hotkey: remote.hotkey, mode: remote.mode, action: "clipboard")
    }

    func revert() {
        hotkey = snapshot.hotkey
        controlHotkey = savedControlHotkey
        controlSilenceDB = savedControlSilenceDB
        mode = snapshot.mode
        action = snapshot.action
    }

    func load() {
        guard let data = try? Data(contentsOf: AppPaths.pythonConfigFile),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            apply(remote: .default)
            return
        }
        let loaded = BackendSettings(
            hotkey: object["hotkey"] as? String ?? BackendSettings.default.hotkey,
            mode: object["mode"] as? String ?? BackendSettings.default.mode,
            action: "clipboard"
        )
        apply(remote: loaded)
        controlHotkey = object["control_hotkey"] as? String ?? "ctrl+shift+space"
        savedControlHotkey = controlHotkey
        controlSilenceDB = object["control_silence_db"] as? Double ?? -38
        savedControlSilenceDB = controlSilenceDB
    }

    func save() throws {
        isSaving = true
        saveError = nil
        defer { isSaving = false }
        let payload = BackendSettings(hotkey: hotkey, mode: mode, action: action)
        do {
            guard AppPaths.ensureDirectories() else { throw CocoaError(.fileWriteNoPermission) }
            var document: [String: Any] = [:]
            if let data = try? Data(contentsOf: AppPaths.pythonConfigFile),
               let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                document = existing
            }
            document["hotkey"] = payload.hotkey
            document["control_hotkey"] = controlHotkey
            document["control_silence_db"] = controlSilenceDB
            document["mode"] = payload.mode
            document["action"] = payload.action
            let data = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: AppPaths.pythonConfigFile, options: .atomic)
            // Atomic write recreates the file with the default umask; the
            // config can hold auth_token, so restore owner-only permissions.
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: AppPaths.pythonConfigFile.path
            )
            snapshot = payload
            savedControlHotkey = controlHotkey
            savedControlSilenceDB = controlSilenceDB
            LogStore.shared.log("Settings saved locally.")
        } catch {
            markSaveError(error.localizedDescription)
            LogStore.shared.error("Local settings save failed: \(error.localizedDescription)")
            throw error
        }
    }

    func markSaveSuccess() {
        saveSuccessAt = Date()
        saveError = nil
        scheduleFeedbackReset()
    }

    func markSaveError(_ message: String) {
        saveError = message
        saveSuccessAt = nil
        scheduleFeedbackReset()
    }

    private func scheduleFeedbackReset() {
        feedbackResetTask?.cancel()
        feedbackResetTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled else { return }
            self?.saveError = nil
            self?.saveSuccessAt = nil
        }
    }
}
