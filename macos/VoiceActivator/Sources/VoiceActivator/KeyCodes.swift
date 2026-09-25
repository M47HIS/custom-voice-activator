import Carbon.HIToolbox
import Foundation

/// Mac virtual key code → human-readable token used in the hotkey string.
/// The string format MUST match what the Python parser in
/// `client/voice_client.py::_MODIFIER_MAP` and `_SPECIAL_KEYS` accept.
///
/// On recent macOS SDKs the `kVK_*` constants are typed as `Int`, so we
/// convert to `UInt16` once at table build time.
enum KeyCodes {
    private static func k(_ value: Int) -> UInt16 { UInt16(value) }

    /// Maps Carbon key codes for letters A–Z → "a"–"z".
    static let letterTokens: [UInt16: String] = [
        k(kVK_ANSI_A): "a", k(kVK_ANSI_B): "b", k(kVK_ANSI_C): "c",
        k(kVK_ANSI_D): "d", k(kVK_ANSI_E): "e", k(kVK_ANSI_F): "f",
        k(kVK_ANSI_G): "g", k(kVK_ANSI_H): "h", k(kVK_ANSI_I): "i",
        k(kVK_ANSI_J): "j", k(kVK_ANSI_K): "k", k(kVK_ANSI_L): "l",
        k(kVK_ANSI_M): "m", k(kVK_ANSI_N): "n", k(kVK_ANSI_O): "o",
        k(kVK_ANSI_P): "p", k(kVK_ANSI_Q): "q", k(kVK_ANSI_R): "r",
        k(kVK_ANSI_S): "s", k(kVK_ANSI_T): "t", k(kVK_ANSI_U): "u",
        k(kVK_ANSI_V): "v", k(kVK_ANSI_W): "w", k(kVK_ANSI_X): "x",
        k(kVK_ANSI_Y): "y", k(kVK_ANSI_Z): "z",
    ]

    /// Digit keys 0–9. The shifted variants of digits on US layouts (e.g. shift+1 = "!")
    /// are mapped back to the base digit token so the hotkey string remains
    /// usable cross-platform.
    static let digitTokens: [UInt16: String] = [
        k(kVK_ANSI_0): "0", k(kVK_ANSI_1): "1", k(kVK_ANSI_2): "2",
        k(kVK_ANSI_3): "3", k(kVK_ANSI_4): "4", k(kVK_ANSI_5): "5",
        k(kVK_ANSI_6): "6", k(kVK_ANSI_7): "7", k(kVK_ANSI_8): "8",
        k(kVK_ANSI_9): "9",
    ]

    /// Special keys. Names match the Python parser's `_SPECIAL_KEYS` dict.
    static let specialTokens: [UInt16: String] = [
        k(kVK_Space): "space",
        k(kVK_Tab): "tab",
        k(kVK_Return): "enter",
        k(kVK_Escape): "esc",
        k(kVK_Delete): "backspace",
        k(kVK_ForwardDelete): "delete",
        k(kVK_UpArrow): "up",
        k(kVK_DownArrow): "down",
        k(kVK_LeftArrow): "left",
        k(kVK_RightArrow): "right",
        k(kVK_Home): "home",
        k(kVK_End): "end",
        k(kVK_PageUp): "page_up",
        k(kVK_PageDown): "page_down",
    ]

    /// Function keys F1–F20. The Carbon SDK only exposes kVK_F1..F12, so we
    /// hardcode the standard virtual key codes (matching pynput's mapping)
    /// for F13–F20.
    static let functionTokens: [UInt16: String] = [
        k(kVK_F1): "f1", k(kVK_F2): "f2", k(kVK_F3): "f3",
        k(kVK_F4): "f4", k(kVK_F5): "f5", k(kVK_F6): "f6",
        k(kVK_F7): "f7", k(kVK_F8): "f8", k(kVK_F9): "f9",
        k(kVK_F10): "f10", k(kVK_F11): "f11", k(kVK_F12): "f12",
        // F13–F20 use the standard USB HID usage pages 0x7C–0x83.
        0x7C: "f13", 0x7D: "f14", 0x7E: "f15", 0x7F: "f16",
        0x80: "f17", 0x81: "f18", 0x82: "f19", 0x83: "f20",
    ]

    /// Look up the canonical token for a Carbon key code. Returns nil for
    /// unknown keys (e.g. media keys, numpad).
    static func token(forKeyCode code: UInt16) -> String? {
        if let t = letterTokens[code] { return t }
        if let t = digitTokens[code] { return t }
        if let t = specialTokens[code] { return t }
        if let t = functionTokens[code] { return t }
        return nil
    }

    static func keyCode(forToken token: String) -> UInt16? {
        let normalized = token.lowercased()
        for table in [letterTokens, digitTokens, specialTokens, functionTokens] {
            if let match = table.first(where: { $0.value == normalized }) {
                return match.key
            }
        }
        return nil
    }
}
