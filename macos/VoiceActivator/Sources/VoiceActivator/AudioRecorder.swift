import AVFoundation
import Foundation

// MARK: - AudioRecorder

/// Records 16 kHz, 16-bit, mono Linear PCM WAV using AVAudioRecorder.
/// Does **not** use AVAudioEngine or AVAudioSession (macOS target only).
final class AudioRecorder: @unchecked Sendable {

    let settings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatLinearPCM),
        AVSampleRateKey: 16_000.0,
        AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsBigEndianKey: false,
        AVLinearPCMIsFloatKey: false,
    ]

    var isRecording: Bool {
        recorder?.isRecording == true
    }
    func enableMetering() {
        recorder?.isMeteringEnabled = true
    }
    func averagePower() -> Float {
        recorder?.updateMeters()
        return recorder?.averagePower(forChannel: 0) ?? -160
    }
    private var recorder: AVAudioRecorder?
    private var fileURL: URL?

    // MARK: - Public API

    /// Starts a new recording to a unique temporary WAV file.
    func startRecording() throws {
        guard !isRecording else {
            throw AudioRecorderError.alreadyRecording
        }

        let tempDir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let uuid = UUID().uuidString
        let url = tempDir.appendingPathComponent("voice_module_recording_\(uuid).wav")

        // Ensure parent directory exists.
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)

        let recorder = try AVAudioRecorder(url: url, settings: settings)
        guard recorder.prepareToRecord() else {
            throw AudioRecorderError.prepareFailed
        }
        guard recorder.record() else {
            throw AudioRecorderError.recordFailed
        }

        self.recorder = recorder
        self.fileURL = url
    }

    /// Stops the current recording and returns the file URL.
    /// Returns `nil` when no recording is active.
    func stopRecording() -> URL? {
        guard let recorder, recorder.isRecording else {
            return nil
        }
        recorder.stop()
        let url = fileURL
        self.recorder = nil
        self.fileURL = nil
        return url
    }

    deinit {
        // Best-effort cleanup of any in-progress recording.
        if let recorder, recorder.isRecording {
            recorder.stop()
        }
        if let url = fileURL {
            try? FileManager.default.removeItem(at: url)
        }
    }
}

// MARK: - Errors

enum AudioRecorderError: LocalizedError {
    case alreadyRecording
    case prepareFailed
    case recordFailed

    var errorDescription: String? {
        switch self {
        case .alreadyRecording:
            return "A recording is already in progress."
        case .prepareFailed:
            return "AVAudioRecorder failed to prepare for recording."
        case .recordFailed:
            return "AVAudioRecorder failed to start recording."
        }
    }
}
