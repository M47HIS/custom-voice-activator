import Carbon
import Foundation

final class HotkeyManager {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private let hotKeyID: EventHotKeyID

    init(id: UInt32 = 1) {
        hotKeyID = EventHotKeyID(signature: HotkeyManager.signature, id: id)
    }

    var onPressed: (() -> Void)?
    var onReleased: (() -> Void)?

    deinit {
        unregister()
        if let handlerRef {
            RemoveEventHandler(handlerRef)
        }
    }

    func register(_ hotkey: Hotkey) throws {
        unregister()
        installHandlerIfNeeded()

        guard let keyCode = KeyCodes.keyCode(forToken: hotkey.key) else {
            throw NSError(
                domain: "HotkeyManager",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Unsupported hotkey key: \(hotkey.key)"]
            )
        }

        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(
            UInt32(keyCode),
            carbonModifiers(for: hotkey.modifiers),
            hotKeyID,
            GetEventDispatcherTarget(),
            0,
            &ref
        )
        guard status == noErr, let ref else {
            throw NSError(
                domain: "HotkeyManager",
                code: Int(status),
                userInfo: [NSLocalizedDescriptionKey: "Could not register hotkey (\(status))."]
            )
        }
        hotKeyRef = ref
    }

    func unregister() {
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
            self.hotKeyRef = nil
        }
    }

    private func installHandlerIfNeeded() {
        guard handlerRef == nil else { return }

        var eventTypes = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]

        let selfPtr = UnsafeMutableRawPointer(Unmanaged.passUnretained(self).toOpaque())
        InstallEventHandler(
            GetEventDispatcherTarget(),
            { _, event, userData in
                guard let event, let userData else { return noErr }
                let manager = Unmanaged<HotkeyManager>.fromOpaque(userData).takeUnretainedValue()
                var eventID = EventHotKeyID()
                let idStatus = GetEventParameter(
                    event, EventParamName(kEventParamDirectObject),
                    EventParamType(typeEventHotKeyID), nil,
                    MemoryLayout<EventHotKeyID>.size, nil, &eventID
                )
                guard idStatus == noErr,
                      eventID.signature == manager.hotKeyID.signature,
                      eventID.id == manager.hotKeyID.id else { return noErr }
                let kind = GetEventKind(event)
                if kind == UInt32(kEventHotKeyPressed) {
                    manager.onPressed?()
                } else if kind == UInt32(kEventHotKeyReleased) {
                    manager.onReleased?()
                }
                return noErr
            },
            2,
            &eventTypes,
            selfPtr,
            &handlerRef
        )
    }

    private func carbonModifiers(for modifiers: Hotkey.Modifiers) -> UInt32 {
        var result: UInt32 = 0
        if modifiers.contains(.command) { result |= UInt32(cmdKey) }
        if modifiers.contains(.control) { result |= UInt32(controlKey) }
        if modifiers.contains(.option) { result |= UInt32(optionKey) }
        if modifiers.contains(.shift) { result |= UInt32(shiftKey) }
        return result
    }

    private static let signature: OSType = {
        let bytes = Array("VACT".utf8)
        return bytes.reduce(0) { ($0 << 8) + OSType($1) }
    }()
}
