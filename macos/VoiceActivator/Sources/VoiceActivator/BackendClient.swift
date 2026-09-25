import Combine
import Foundation
import os.log

// MARK: - Errors

enum BackendError: Error, LocalizedError {
    case notReachable(String)
    case http(Int, String)
    case decode(String)
    case missingAuthToken

    var errorDescription: String? {
        switch self {
        case .notReachable(let msg): return "Backend not reachable: \(msg)"
        case .http(let code, let msg): return "Backend HTTP \(code): \(msg)"
        case .decode(let msg): return "Decode error: \(msg)"
        case .missingAuthToken: return "Auth token missing. Cannot call protected endpoint."
        }
    }
}

// MARK: - BackendClient

/// Owns all network I/O to the headless FastAPI backend:
///  * REST: authenticated settings, actions, and config; public loopback health status
///  * WebSocket: /ws
///
/// The auth token is provisioned locally and never fetched over an
/// unauthenticated network bootstrap.
@MainActor
final class BackendClient: ObservableObject {
    @Published private(set) var status: BackendStatus = BackendStatus(state: "offline")
    @Published private(set) var settings: BackendSettings = .default
    @Published private(set) var actions: [Action] = []
    @Published private(set) var isReachable: Bool = false
    @Published private(set) var lastError: String? = nil

    private(set) var authToken: String? = nil
    private let baseURL: URL
    private let session: URLSession
    private var webSocketTask: URLSessionWebSocketTask?
    private var wsContinuations: [UUID: AsyncStream<URLSessionWebSocketTask.Message>.Continuation] = [:]
    private let logger = Logger(subsystem: "com.voicemodule.activator", category: "backend")
    private var statusPollTask: Task<Void, Never>?

    init(baseURL: URL = URL(string: "http://127.0.0.1:8080")!) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 5
        cfg.timeoutIntervalForResource = 10
        cfg.waitsForConnectivity = false
        self.session = URLSession(configuration: cfg)
    }

    // MARK: - Auth token

    /// Load a token that the local user provisioned explicitly.
    func ensureAuthToken() async {
        if let cached = readCachedToken(), !cached.isEmpty {
            self.authToken = cached
            LogStore.shared.log("Auth token loaded from cache.")
            return
        }
        LogStore.shared.warn("No local backend auth token is configured.")
    }

    private func readCachedToken() -> String? {
        guard FileManager.default.fileExists(atPath: AppPaths.authTokenFile.path) else { return nil }
        do {
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: AppPaths.authTokenFile.path
            )
        } catch { return nil }
        return try? String(contentsOf: AppPaths.authTokenFile, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - REST

    func fetchSettings() async throws -> BackendSettings {
        let url = baseURL.appendingPathComponent("/api/settings")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        try authorize(&req)
        let (data, response) = try await session.data(for: req)
        try Self.validate(response: response, data: data)
        do {
            let s = try JSONDecoder().decode(BackendSettings.self, from: data)
            self.settings = s
            return s
        } catch {
            throw BackendError.decode("settings: \(error.localizedDescription)")
        }
    }

    func saveSettings(_ newSettings: BackendSettings) async throws {
        let url = baseURL.appendingPathComponent("/api/settings")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        try authorize(&req)
        req.httpBody = try JSONEncoder().encode(newSettings)
        let (data, response) = try await session.data(for: req)
        try Self.validate(response: response, data: data)
        do {
            let s = try JSONDecoder().decode(BackendSettings.self, from: data)
            self.settings = s
        } catch {
            // Backend may return the updated object; if not, just keep local copy.
            self.settings = newSettings
        }
    }

    func fetchActions() async throws -> [Action] {
        let url = baseURL.appendingPathComponent("/api/actions")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        try authorize(&req)
        let (data, response) = try await session.data(for: req)
        try Self.validate(response: response, data: data)
        do {
            let a = try JSONDecoder().decode([Action].self, from: data)
            self.actions = a
            return a
        } catch {
            throw BackendError.decode("actions: \(error.localizedDescription)")
        }
    }

    func fetchStatus() async throws -> BackendStatus {
        let url = baseURL.appendingPathComponent("/api/status")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        let (data, response) = try await session.data(for: req)
        try Self.validate(response: response, data: data)
        do {
            let s = try JSONDecoder().decode(BackendStatus.self, from: data)
            self.status = s
            self.isReachable = true
            self.lastError = nil
            return s
        } catch {
            throw BackendError.decode("status: \(error.localizedDescription)")
        }
    }

    func fetchConfig() async throws -> BackendConfig {
        let url = baseURL.appendingPathComponent("/api/config")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        try authorize(&req)
        let (data, response) = try await session.data(for: req)
        try Self.validate(response: response, data: data)
        do {
            return try JSONDecoder().decode(BackendConfig.self, from: data)
        } catch {
            throw BackendError.decode("config: \(error.localizedDescription)")
        }
    }

    private static func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw BackendError.notReachable("no HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "<binary>"
            throw BackendError.http(http.statusCode, body)
        }
    }

    private func authorize(_ request: inout URLRequest) throws {
        guard let token = authToken, !token.isEmpty else {
            throw BackendError.missingAuthToken
        }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }

    // MARK: - WebSocket

    /// Open (or reopen) the WebSocket and return an AsyncStream of status
    /// messages. The caller is expected to consume the stream; cancellation
    /// of the consuming Task tears down the underlying socket.
    func connectWebSocket() -> AsyncStream<URLSessionWebSocketTask.Message> {
        guard let token = authToken, !token.isEmpty else {
            LogStore.shared.warn("WebSocket disabled because no backend auth token is configured.")
            return AsyncStream { continuation in continuation.finish() }
        }
        let wsURL = URL(string: "ws://127.0.0.1:8080/ws")!
        var request = URLRequest(url: wsURL)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)
        self.webSocketTask = task
        task.resume()

        let id = UUID()
        return AsyncStream<URLSessionWebSocketTask.Message> { continuation in
            self.wsContinuations[id] = continuation
            self.receiveLoop(task: task)
            continuation.onTermination = { [weak self] _ in
                guard let client = self else {
                    task.cancel(with: .normalClosure, reason: nil)
                    return
                }
                Task { @MainActor [client, id, task] in
                    client.wsContinuations[id] = nil
                    task.cancel(with: .normalClosure, reason: nil)
                }
            }
        }
    }

    private func receiveLoop(task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                // Hop to MainActor to mutate continuations and reschedule.
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    for (_, cont) in self.wsContinuations {
                        cont.yield(message)
                    }
                    self.receiveLoop(task: task)
                }
            case .failure(let error):
                LogStore.shared.warn("WebSocket receive error: \(error.localizedDescription)")
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    for (_, cont) in self.wsContinuations {
                        cont.finish()
                    }
                }
            }
        }
    }

    func disconnectWebSocket() {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil
    }

    // MARK: - Polling (used by supervisor)

    /// Start a background task that polls /api/status every 2s. Used to detect
    /// when the backend container comes online after we start it.
    func startStatusPolling(interval: TimeInterval = 2) {
        statusPollTask?.cancel()
        statusPollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    _ = try await self.fetchStatus()
                } catch {
                    self.isReachable = false
                    self.lastError = error.localizedDescription
                }
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func stopStatusPolling() {
        statusPollTask?.cancel()
        statusPollTask = nil
    }
}
