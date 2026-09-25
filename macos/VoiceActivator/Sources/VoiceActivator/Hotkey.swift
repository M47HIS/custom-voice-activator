import AppKit
import Carbon.HIToolbox
import Foundation

/// In-memory representation of a parsed hotkey. Use `Hotkey.parse(_:)` and
/// `Hotkey.encode(_:)` to round-trip with the Python parser format.
struct Hotkey: Equatable, Hashable {
    var modifiers: Modifiers
    var key: String  // canonical token, e.g. "space", "a", "f5"

    struct Modifiers: OptionSet, Equatable, Hashable {
        let rawValue: Int
        static let command = Modifiers(rawValue: 1 << 0)
        static let control = Modifiers(rawValue: 1 << 1)
        static let option  = Modifiers(rawValue: 1 << 2)
        static let shift   = Modifiers(rawValue: 1 << 3)
        static let all: Modifiers = [.command, .control, .option, .shift]
    }

    var hasAnyModifier: Bool { !modifiers.isEmpty }

    var displayName: String {
        var parts: [String] = []
        if modifiers.contains(.control) { parts.append("⌃") }
        if modifiers.contains(.option) { parts.append("⌥") }
        if modifiers.contains(.shift) { parts.append("⇧") }
        if modifiers.contains(.command) { parts.append("⌘") }
        parts.append(key == "space" ? "Space" : key.uppercased())
        return parts.joined()
    }

    /// Parse the Python-style hotkey string. Throws if no key or no modifier.
    static func parse(_ raw: String) throws -> Hotkey {
        let parts = raw
            .lowercased()
            .split(separator: "+")
            .map { $0.trimmingCharacters(in: .whitespaces) }

        var mods = Modifiers()
        var key: String?

        for p in parts {
            switch p {
            case "cmd", "command": mods.insert(.command)
            case "ctrl", "control": mods.insert(.control)
            case "alt", "option": mods.insert(.option)
            case "shift": mods.insert(.shift)
            default:
                if key == nil { key = p }
            }
        }

        guard let k = key, !k.isEmpty else {
            throw NSError(
                domain: "Hotkey",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Hotkey string is missing a trigger key."]
            )
        }
        guard !mods.isEmpty else {
            throw NSError(
                domain: "Hotkey",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Hotkey requires at least one modifier (cmd/ctrl/alt/shift)."]
            )
        }
        return Hotkey(modifiers: mods, key: k)
    }

    /// Serialize back to the Python-style string. Order is canonical:
    /// cmd, ctrl, alt, shift, then the key.
    func encode() -> String {
        var parts: [String] = []
        if modifiers.contains(.command) { parts.append("cmd") }
        if modifiers.contains(.control) { parts.append("ctrl") }
        if modifiers.contains(.option)  { parts.append("alt") }
        if modifiers.contains(.shift)   { parts.append("shift") }
        parts.append(key)
        return parts.joined(separator: "+")
    }

    /// Build a Hotkey from an NSEvent (keyDown). Returns nil if the event
    /// does not carry a meaningful key or has no modifier.
    static func from(event: NSEvent) -> Hotkey? {
        var mods = Modifiers()
        let flags = event.modifierFlags
        if flags.contains(.command) { mods.insert(.command) }
        if flags.contains(.control) { mods.insert(.control) }
        if flags.contains(.option)  { mods.insert(.option) }
        if flags.contains(.shift)   { mods.insert(.shift) }
        guard !mods.isEmpty else { return nil }

        let keyCode = UInt16(event.keyCode)
        guard let token = KeyCodes.token(forKeyCode: keyCode) else { return nil }
        return Hotkey(modifiers: mods, key: token)
    }
}
