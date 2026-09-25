import AppKit
import Carbon.HIToolbox
import SwiftUI

/// NSViewRepresentable that becomes the first responder while the user is
/// recording a hotkey. It captures the next keyDown, including modifiers,
/// and forwards a `Hotkey` via the binding.
struct KeyCaptureView: NSViewRepresentable {
    @Binding var hotkey: Hotkey?
    var placeholder: String = "Click then press a hotkey..."
    var onCommit: (() -> Void)? = nil

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSView {
        let view = CaptureView()
        view.coordinator = context.coordinator
        view.placeholder = placeholder
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        if let v = nsView as? CaptureView {
            context.coordinator.parent = self
            v.placeholder = placeholder
            v.needsDisplay = true
        }
    }

    final class Coordinator: NSObject {
        var parent: KeyCaptureView
        init(_ parent: KeyCaptureView) { self.parent = parent }

        func handle(_ hk: Hotkey) {
            parent.hotkey = hk
            parent.onCommit?()
        }
    }

    final class CaptureView: NSView {
        weak var coordinator: Coordinator?
        var placeholder: String = ""
        private var isCapturing = false

        override var acceptsFirstResponder: Bool { true }

        override func becomeFirstResponder() -> Bool {
            isCapturing = true
            needsDisplay = true
            return true
        }

        override func resignFirstResponder() -> Bool {
            isCapturing = false
            needsDisplay = true
            return true
        }

        override func draw(_ dirtyRect: NSRect) {
            NSColor.windowBackgroundColor.setFill()
            dirtyRect.fill()

            let display = isCapturing
                ? "Type shortcut"
                : coordinator?.parent.hotkey?.displayName ?? placeholder
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedSystemFont(ofSize: 13, weight: .medium),
                .foregroundColor: isCapturing
                    ? NSColor.controlAccentColor
                    : coordinator?.parent.hotkey == nil
                    ? NSColor.secondaryLabelColor
                    : NSColor.labelColor
            ]
            let s = NSAttributedString(string: display, attributes: attrs)
            let size = s.size()
            let rect = NSRect(
                x: (bounds.width - size.width) / 2,
                y: (bounds.height - size.height) / 2,
                width: size.width,
                height: size.height
            )
            s.draw(in: rect)

            // Border
            (isCapturing ? NSColor.controlAccentColor : NSColor.separatorColor).setStroke()
            let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 6, yRadius: 6)
            path.lineWidth = isCapturing ? 2 : 1
            path.stroke()

            setAccessibilityRole(.button)
            setAccessibilityLabel("Keyboard shortcut")
            setAccessibilityValue(display)
            setAccessibilityHelp("Click, then type a shortcut with at least one modifier key.")
        }

        override func mouseDown(with event: NSEvent) {
            window?.makeFirstResponder(self)
        }

        override func keyDown(with event: NSEvent) {
            if event.keyCode == 53 {
                window?.makeFirstResponder(nil)
                return
            }
            // We accept the event by NOT calling super. First, try to map to
            // a Hotkey.
            if let hk = Hotkey.from(event: event) {
                coordinator?.handle(hk)
                window?.makeFirstResponder(nil)
            } else {
                // Flash a hint by redrawing. Common case: user pressed a
                // modifier-only combo (e.g. just Shift).
                needsDisplay = true
                NSSound.beep()
            }
        }

        // Required to receive keyDown.
        override func performKeyEquivalent(with event: NSEvent) -> Bool {
            guard isCapturing else { return false }
            keyDown(with: event)
            return true
        }
    }
}
