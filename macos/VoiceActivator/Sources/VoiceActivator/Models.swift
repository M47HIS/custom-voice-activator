import Foundation

// MARK: - Wire models (match the FastAPI backend payloads)

struct BackendSettings: Codable, Equatable {
    var hotkey: String
    var mode: String  // "hold" or "toggle"
    var action: String

    static let `default` = BackendSettings(
        hotkey: "cmd+shift+space",
        mode: "hold",
        action: "clipboard"
    )
}

struct Action: Codable, Identifiable, Hashable {
    var name: String
    var description: String?
    var type: String
    var config: [String: AnyCodable]?

    var id: String { name }
}

/// Backend status as returned by /api/status and broadcast over /ws.
struct BackendStatus: Codable, Equatable {
    var state: String  // idle | listening | transcribing
    var engine: String?
    var language: String?
    var connected_clients: Int?
    var actions_loaded: Int?
    var last_activity: Double?
}

/// /api/config response.
struct BackendConfig: Codable {
    var engine: String?
    var language: String?
    var ws_url: String?
    var actions: [Action]?
    var settings: BackendSettings?
    var auth_token: String?
}

// MARK: - AnyCodable

/// Minimal `AnyCodable` shim. Used so we can round-trip the heterogeneous
/// `config` field on actions without forcing a fixed schema. We only need
/// encode support (we post it back); decode is best-effort.
struct AnyCodable: Codable, Hashable {
    let value: AnyHashable

    init(_ value: AnyHashable) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self) { value = AnyHashable(v); return }
        if let v = try? c.decode(Int.self) { value = AnyHashable(v); return }
        if let v = try? c.decode(Double.self) { value = AnyHashable(v); return }
        if let v = try? c.decode(String.self) { value = AnyHashable(v); return }
        if let v = try? c.decode([AnyCodable].self) { value = AnyHashable(v); return }
        if let v = try? c.decode([String: AnyCodable].self) { value = AnyHashable(v); return }
        if c.decodeNil() { value = AnyHashable(NSNull()); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported AnyCodable value")
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch value.base {
        case let v as Bool: try c.encode(v)
        case let v as Int: try c.encode(v)
        case let v as Double: try c.encode(v)
        case let v as String: try c.encode(v)
        case let v as [AnyCodable]: try c.encode(v)
        case let v as [String: AnyCodable]: try c.encode(v)
        default:
            // Last resort: encode its String description. The backend tolerates
            // unknown field types as long as the schema is valid JSON.
            try c.encode(String(describing: value.base))
        }
    }
}
