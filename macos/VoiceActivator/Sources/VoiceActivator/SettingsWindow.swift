import AppKit
import SwiftUI

@MainActor
final class SettingsWindowController {
    static let shared = SettingsWindowController()

    private var window: NSWindow?

    private init() {}

    func show(
        supervisor: ProcessSupervisor? = nil,
        backend: BackendClient? = nil,
        settings: SettingsStore? = nil
    ) {
        let appDelegate = NSApp.delegate as? AppDelegate
        let resolvedSupervisor = supervisor ?? appDelegate?.supervisor
        let resolvedSettings = settings ?? appDelegate?.settings
        _ = backend

        guard let supervisor = resolvedSupervisor, let settings = resolvedSettings else {
            LogStore.shared.error("SettingsWindowController.show: missing environment objects.")
            return
        }

        if window == nil {
            let view = SettingsView()
                .environmentObject(supervisor)
                .environmentObject(settings)
            let host = NSHostingController(rootView: view)
            host.view.frame = NSRect(x: 0, y: 0, width: 520, height: 500)

            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 520, height: 500),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false
            )
            window.title = "Voice AI Settings"
            window.contentViewController = host
            window.isReleasedWhenClosed = false
            window.center()
            self.window = window
        }

        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

struct SettingsView: View {
    @EnvironmentObject var supervisor: ProcessSupervisor
    @EnvironmentObject var settings: SettingsStore

    @State private var capturedHotkey: Hotkey?
    @State private var capturedControlHotkey: Hotkey?
    @State private var launchAtLoginStatus = "Checking Launch at Login"
    @State private var launchAtLoginEnabled = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image(systemName: "waveform.circle.fill")
                    .font(.system(size: 28))
                    .foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Voice AI")
                        .font(.system(size: 15, weight: .semibold))
                    Text("Local dictation and voice control.")
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }
            }

            Form {
                Section("Shortcut") {
                    HStack(alignment: .center, spacing: 16) {
                        Label("Record", systemImage: "keyboard")
                        Spacer()
                        KeyCaptureView(
                            hotkey: $capturedHotkey,
                            placeholder: "Click to set"
                        )
                        .frame(width: 210, height: 34)
                    }
                    .onChange(of: capturedHotkey) { newValue in
                        if let hotkey = newValue {
                            settings.hotkey = hotkey.encode()
                        }
                    }
                    HStack(alignment: .center, spacing: 16) {
                        Label("Voice control", systemImage: "cursorarrow.rays")
                        Spacer()
                        KeyCaptureView(hotkey: $capturedControlHotkey, placeholder: "Click to set")
                            .frame(width: 210, height: 34)
                    }
                    .onChange(of: capturedControlHotkey) { newValue in
                        if let hotkey = newValue {
                            settings.controlHotkey = hotkey.encode()
                        }
                    }

                    Text("Voice control toggles continuous listening. Press its shortcut again to stop.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }

                Section("Recording") {
                    Picker("Shortcut behavior", selection: $settings.mode) {
                        Text("Hold to talk").tag("hold")
                        Text("Press to start and stop").tag("toggle")
                    }
                    .pickerStyle(.segmented)

                    Text("Transcripts are copied to the clipboard when ready.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                    Slider(value: $settings.controlSilenceDB, in: -55...(-25), step: 1) {
                        Text("Voice sensitivity")
                    }
                    Text("Adjust if voice control misses speech or hears room noise.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }

                Section("System") {
                    Toggle("Launch at Login", isOn: Binding(
                        get: { launchAtLoginEnabled },
                        set: { setLaunchAtLogin($0) }
                    ))

                    LabeledContent("Microphone") {
                        HStack(spacing: 6) {
                            Image(systemName: microphoneReady ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                                .foregroundStyle(microphoneReady ? .green : .orange)
                            Text(AppDiagnostics.microphoneStatus.capitalized)
                                .foregroundStyle(.secondary)
                        }
                    }

                    if !microphoneReady {
                        Button("Open Microphone Settings") {
                            AppDiagnostics.openMicrophoneSettings()
                        }
                    }
                    ReadinessRow(label: "Accessibility", ok: AppDiagnostics.accessibilityReady)
                    if !AppDiagnostics.accessibilityReady {
                        Button("Open Accessibility Settings") {
                            AppDiagnostics.openAccessibilitySettings()
                        }
                    }
                    ReadinessRow(label: "Screen Recording for text fallback",
                                 ok: AppDiagnostics.screenRecordingReady)
                    if !AppDiagnostics.screenRecordingReady {
                        Button("Open Screen Recording Settings") {
                            AppDiagnostics.openScreenRecordingSettings()
                        }
                    }
                }

                Section("Diagnostics") {
                    ReadinessRow(label: "Shortcut", ok: supervisor.hotkeyRegistered)
                    ReadinessRow(label: "Local worker", ok: supervisor.workerReady)
                    ReadinessRow(
                        label: "Python dependencies",
                        ok: AppDiagnostics.pythonDepsStatus() == "OK"
                    )

                    HStack {
                        Button("Refresh") {
                            AppDiagnostics.invalidatePythonDepsCache()
                            Task { await refreshDiagnostics() }
                        }
                        Button("Reveal Logs") {
                            NSWorkspace.shared.open(AppPaths.logDir)
                        }
                        Spacer()
                        Text(launchAtLoginStatus)
                            .font(.system(size: 11))
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            .formStyle(.grouped)

            HStack(spacing: 8) {
                if let error = settings.saveError {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(error)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else if settings.saveSuccessAt != nil {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("Saved")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Cancel") { cancel() }
                Button("Save") { saveSettings() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(settings.isSaving || !settings.isDirty)
            }
            .frame(height: 24)
        }
        .padding(20)
        .frame(minWidth: 500, minHeight: 460)
        .task {
            capturedHotkey = try? Hotkey.parse(settings.hotkey)
            capturedControlHotkey = try? Hotkey.parse(settings.controlHotkey)
            await refreshDiagnostics()
        }
    }

    private var microphoneReady: Bool {
        AppDiagnostics.microphoneStatus == "granted"
    }

    private func saveSettings() {
        do {
            try supervisor.configureHotkeyFromCurrentSettings()
            try settings.save()
            settings.markSaveSuccess()
            LogStore.shared.log("Settings saved and shortcut updated.")
        } catch {
            settings.revert()
            capturedHotkey = try? Hotkey.parse(settings.hotkey)
            capturedControlHotkey = try? Hotkey.parse(settings.controlHotkey)
            try? supervisor.configureHotkeyFromCurrentSettings()
            settings.markSaveError(error.localizedDescription)
        }
    }

    private func cancel() {
        settings.revert()
        capturedHotkey = try? Hotkey.parse(settings.hotkey)
        capturedControlHotkey = try? Hotkey.parse(settings.controlHotkey)
        NSApp.keyWindow?.performClose(nil)
    }

    private func refreshDiagnostics() async {
        if #available(macOS 13.0, *) {
            launchAtLoginStatus = AppDiagnostics.launchAtLoginStatus
            launchAtLoginEnabled = launchAtLoginStatus == "enabled"
        } else {
            launchAtLoginStatus = "Launch at Login unavailable"
            launchAtLoginEnabled = false
        }
    }

    private func setLaunchAtLogin(_ enabled: Bool) {
        guard #available(macOS 13.0, *) else {
            launchAtLoginEnabled = false
            return
        }

        do {
            try AppDiagnostics.setLaunchAtLogin(enabled)
            launchAtLoginStatus = AppDiagnostics.launchAtLoginStatus
            launchAtLoginEnabled = launchAtLoginStatus == "enabled"
        } catch {
            settings.markSaveError("Launch at Login: \(error.localizedDescription)")
        }
    }
}

private struct ReadinessRow: View {
    let label: String
    let ok: Bool

    var body: some View {
        LabeledContent(label) {
            HStack(spacing: 5) {
                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(ok ? .green : .red)
                Text(ok ? "Ready" : "Needs attention")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
        }
    }
}
