import AppKit
import ApplicationServices
import Foundation
import Vision

/// The model can choose only from this observed, bounded set of native actions.
@MainActor
final class ControlEngine {
    private var elements: [AXUIElement] = []
    private var ocrPoints: [Int: CGPoint] = [:]
    private var observedPID: pid_t?

    func observe() -> [[String: Any]] {
        elements.removeAll()
        ocrPoints.removeAll()
        observedPID = nil
        guard let app = NSWorkspace.shared.frontmostApplication else { return [] }
        observedPID = app.processIdentifier
        let root = AXUIElementCreateApplication(app.processIdentifier)
        var rows: [[String: Any]] = []
        func walk(_ element: AXUIElement, depth: Int) {
            guard depth < 6, rows.count < 60 else { return }
            let role = attribute(element, kAXRoleAttribute) ?? ""
            if role == kAXTextFieldRole as String || role == kAXTextAreaRole as String
                || role == kAXButtonRole as String || role == kAXMenuItemRole as String
                || role == kAXCheckBoxRole as String || role == "AXLink" {
                let title = attribute(element, kAXTitleAttribute)
                    ?? attribute(element, kAXDescriptionAttribute) ?? ""
                // Secure text fields are deliberately excluded from model context and actions.
                let subrole = attribute(element, kAXSubroleAttribute)?.lowercased() ?? ""
                if !subrole.contains("secure") && !title.lowercased().contains("password") {
                    let value = role == kAXTextAreaRole as String || role == kAXTextFieldRole as String
                        ? String((attribute(element, kAXValueAttribute) ?? "").prefix(180)) : ""
                    let index = elements.count
                    elements.append(element)
                    rows.append(["id": index, "role": role, "title": String(title.prefix(100)), "value": value])
                }
            }
            var children: CFTypeRef?
            guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children) == .success,
                  let list = children as? [AXUIElement] else { return }
            for child in list { walk(child, depth: depth + 1) }
        }
        walk(root, depth: 0)
        if rows.count < 2 { addOCRRows(for: app, into: &rows) }
        rows.insert(["app": app.localizedName ?? "", "bundle": app.bundleIdentifier ?? "",
                     "pid": app.processIdentifier], at: 0)
        return rows
    }

    func perform(_ action: [String: Any]) -> String? {
        guard AXIsProcessTrusted() else { return "Grant Accessibility access to Voice AI in System Settings." }
        guard let kind = action["kind"] as? String else { return "Invalid action." }
        if ["new_document", "type_text", "click"].contains(kind),
           NSWorkspace.shared.frontmostApplication?.processIdentifier != observedPID {
            return "The active app changed; please try again."
        }
        switch kind {
        case "open_app":
            guard let name = action["value"] as? String, !name.isEmpty,
                  !name.contains("/"), !name.contains("\n") else {
                return "Application name is invalid."
            }
            let paths = ["/Applications", "/System/Applications", NSHomeDirectory() + "/Applications"]
            let diskURL = paths
                .map { URL(fileURLWithPath: $0).appendingPathComponent(name + ".app") }
                .first { FileManager.default.fileExists(atPath: $0.path) }
            guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: name)
                ?? NSWorkspace.shared.runningApplications.first(where: {
                    $0.localizedName?.caseInsensitiveCompare(name) == .orderedSame
                })?.bundleURL ?? diskURL else {
                return "Application was not found."
            }
            NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
        case "open_url":
            guard let value = action["value"] as? String,
                  let url = URL(string: value),
                  ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
                  NSWorkspace.shared.open(url) else { return "Could not open the web address." }
        case "new_document":
            key(45, modifiers: .maskCommand) // Command-N
        case "type_text":
            guard let value = action["value"] as? String, !value.isEmpty,
                  let app = NSWorkspace.shared.frontmostApplication else { return "No text or active app." }
            let root = AXUIElementCreateApplication(app.processIdentifier)
            var focused: CFTypeRef?
            guard AXUIElementCopyAttributeValue(root, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
                  let element = focused as! AXUIElement?,
                  let role = attribute(element, kAXRoleAttribute),
                  role == kAXTextAreaRole as String || role == kAXTextFieldRole as String else {
                return "Focus a text field first."
            }
            let title = attribute(element, kAXTitleAttribute)?.lowercased() ?? ""
            let subrole = attribute(element, kAXSubroleAttribute)?.lowercased() ?? ""
            guard !title.contains("password"), !title.contains("secure"),
                  !subrole.contains("secure") else { return "Secure fields are not supported." }
            let chars = Array(value.utf16)
            guard chars.count <= 1000 else { return "Text is too long for one voice action." }
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
                return "Could not create keyboard event."
            }
            down.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: chars)
            up.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: chars)
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
        case "click":
            guard let index = action["id"] as? Int else { return "Target is no longer available." }
            if elements.indices.contains(index) {
                guard AXUIElementPerformAction(elements[index], kAXPressAction as CFString) == .success else {
                    return "The target did not accept a press."
                }
            } else if let point = ocrPoints[index] {
                guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                                         mouseCursorPosition: point, mouseButton: .left),
                      let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                                       mouseCursorPosition: point, mouseButton: .left) else {
                    return "Could not click the observed text."
                }
                down.post(tap: .cghidEventTap)
                up.post(tap: .cghidEventTap)
            } else {
                return "Target is no longer available."
            }
        default:
            return "Unsupported action."
        }
        return nil
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value as? String
    }

    private func addOCRRows(for app: NSRunningApplication, into rows: inout [[String: Any]]) {
        guard CGPreflightScreenCaptureAccess(),
              let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]],
              let window = windows.first(where: {
                  ($0[kCGWindowOwnerPID as String] as? Int32) == app.processIdentifier
                    && ($0[kCGWindowLayer as String] as? Int) == 0
              }),
              let number = window[kCGWindowNumber as String] as? CGWindowID,
              let bounds = window[kCGWindowBounds as String] as? [String: Any],
              let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary),
              let image = CGWindowListCreateImage(.null, .optionIncludingWindow, number,
                                                  [.boundsIgnoreFraming, .bestResolution]) else { return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        guard (try? VNImageRequestHandler(cgImage: image).perform([request])) != nil else { return }
        for observation in (request.results ?? []).prefix(20) {
            guard let text = observation.topCandidates(1).first?.string, !text.isEmpty else { continue }
            let id = elements.count + ocrPoints.count
            let box = observation.boundingBox
            ocrPoints[id] = CGPoint(x: frame.minX + box.midX * frame.width,
                                    y: frame.minY + (1 - box.midY) * frame.height)
            rows.append(["id": id, "role": "OCRText", "title": String(text.prefix(100))])
        }
    }

    private func key(_ code: CGKeyCode, modifiers: CGEventFlags) {
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else { return }
        down.flags = modifiers
        up.flags = modifiers
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}
