import Foundation
import os.log

/// Lightweight file-backed logger. The macOS unified log system is great for
/// development, but support requests usually come with "can you send me the
/// log file?", so we mirror every line to ~/Library/Logs/VoiceModule/menu-bar.log.
///
/// Thread-safe: the `log`/`warn`/`error` methods can be called from any
/// thread. File I/O is serialized through an internal queue.
final class LogStore: @unchecked Sendable {
    static let shared = LogStore()

    private let logger = Logger(subsystem: "com.voicemodule.activator", category: "menu-bar")
    private let handle: FileHandle?
    private let queue = DispatchQueue(label: "com.voicemodule.activator.log", qos: .utility)

    private init() {
        _ = AppPaths.ensureDirectories()
        if !FileManager.default.fileExists(atPath: AppPaths.logFile.path) {
            FileManager.default.createFile(atPath: AppPaths.logFile.path, contents: nil)
        }
        do {
            try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                                  ofItemAtPath: AppPaths.logFile.path)
            self.handle = try FileHandle(forWritingTo: AppPaths.logFile)
        } catch { self.handle = nil }
        // Seek to end so we append rather than truncate.
        _ = try? handle?.seekToEnd()
        log("Voice AI started. Log file: \(AppPaths.logFile.path)")
    }

    deinit {
        try? handle?.close()
    }

    func log(_ message: String) {
        logger.info("\(message, privacy: .private)")
        appendToFile(message: message, level: "INFO")
    }

    func warn(_ message: String) {
        logger.warning("\(message, privacy: .private)")
        appendToFile(message: message, level: "WARN")
    }

    func error(_ message: String) {
        logger.error("\(message, privacy: .private)")
        appendToFile(message: message, level: "ERROR")
    }

    private func appendToFile(message: String, level: String) {
        let line = "[\(Self.timestamp())] [\(level)] \(message)\n"
        let data = Data(line.utf8)
        queue.async { [handle] in
            try? handle?.write(contentsOf: data)
        }
    }

    private static func timestamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss.SSS"
        f.timeZone = TimeZone.current
        return f.string(from: Date())
    }
}
