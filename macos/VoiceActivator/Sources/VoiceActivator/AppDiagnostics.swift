import AppKit
import ApplicationServices
import AVFoundation
import Foundation
import ServiceManagement

enum AppDiagnostics {
    static var microphoneStatus: String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not requested"
        @unknown default: return "unknown"
        }
    }

    static func openMicrophoneSettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
    }
    static var accessibilityReady: Bool { AXIsProcessTrusted() }
    static var screenRecordingReady: Bool { CGPreflightScreenCaptureAccess() }
    static func openAccessibilitySettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }
    static func openScreenRecordingSettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
    }

    // MARK: - Python deps check

    private static var _pythonDepsCache: String? = nil

    static func pythonDepsStatus() -> String {
        if let cached = _pythonDepsCache { return cached }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: ProcessSupervisor.resolvedPythonPath())
        proc.arguments = ["-c", "import sounddevice, pynput, websocket, requests, mlx_audio, laya_mlx; print('OK')"]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        do {
            try proc.run()
            proc.waitUntilExit()
        } catch {
            _pythonDepsCache = "Error: \(error.localizedDescription)"
            return _pythonDepsCache!
        }
        let data = (try? pipe.fileHandleForReading.readToEnd()) ?? Data()
        let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let result = (proc.terminationStatus == 0 && output.contains("OK")) ? "OK" : "Missing"
        _pythonDepsCache = result
        return result
    }

    static func invalidatePythonDepsCache() {
        _pythonDepsCache = nil
    }

    @available(macOS 13.0, *)
    static var launchAtLoginStatus: String {
        switch SMAppService.mainApp.status {
        case .enabled: return "enabled"
        case .requiresApproval: return "requires approval"
        case .notRegistered: return "disabled"
        case .notFound: return "not available"
        @unknown default: return "unknown"
        }
    }

    @available(macOS 13.0, *)
    static func setLaunchAtLogin(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
    }

    private static func openSettings(_ raw: String) {
        guard let url = URL(string: raw) else { return }
        NSWorkspace.shared.open(url)
    }
}
