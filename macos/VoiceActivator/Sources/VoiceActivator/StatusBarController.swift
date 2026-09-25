import AppKit
import Combine
import SwiftUI

@MainActor
final class StatusBarController: NSObject {
    static let shared = StatusBarController()

    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    private var overlayPanel: NSPanel?
    private var cancellables = Set<AnyCancellable>()

    private override init() {
        super.init()
        popover.behavior = .transient
        popover.animates = !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        popover.contentSize = NSSize(width: 300, height: 205)
    }

    func install(
        supervisor: ProcessSupervisor,
        backend: BackendClient,
        settings: SettingsStore
    ) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem = item
        item.button?.imagePosition = .imageOnly
        item.button?.target = self
        item.button?.action = #selector(togglePopover(_:))
        item.button?.sendAction(on: [.leftMouseUp])

        let content = MenuContentView()
            .environmentObject(supervisor)
            .environmentObject(backend)
            .environmentObject(settings)
        popover.contentViewController = NSHostingController(rootView: AnyView(content))

        supervisor.$menuState
            .receive(on: RunLoop.main)
            .sink { [weak self] state in
                self?.applyIcon(name: state.iconName, label: state.label)
                self?.updateOverlay(for: state)
            }
            .store(in: &cancellables)

        applyIcon(name: supervisor.statusIconName, label: supervisor.menuState.label)
    }

    func dismissPopover() {
        popover.performClose(nil)
    }

    @objc private func togglePopover(_ sender: NSStatusBarButton) {
        if popover.isShown {
            popover.performClose(sender)
        } else {
            popover.show(relativeTo: sender.bounds, of: sender, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    private func applyIcon(name: String, label: String) {
        guard let button = statusItem?.button else { return }
        let config = NSImage.SymbolConfiguration(pointSize: 16, weight: .regular)
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "Voice AI: \(label)")
            ?? NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Voice AI")
        image?.isTemplate = true
        button.image = image?.withSymbolConfiguration(config)
        button.toolTip = "Voice AI: \(label)"
    }

    private func updateOverlay(for state: MenuState) {
        guard state.showsOverlay else {
            overlayPanel?.orderOut(nil)
            return
        }

        let panel = overlayPanel ?? makeOverlayPanel()
        panel.contentViewController = NSHostingController(rootView: RecordingOverlayView(state: state))
        positionOverlay(panel)
        panel.orderFrontRegardless()
    }

    private func makeOverlayPanel() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 280, height: 56),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        overlayPanel = panel
        return panel
    }

    private func positionOverlay(_ panel: NSPanel) {
        guard let button = statusItem?.button, let window = button.window else { return }
        let anchor = window.convertToScreen(button.frame)
        panel.setFrameOrigin(NSPoint(
            x: anchor.midX - panel.frame.width / 2,
            y: anchor.minY - panel.frame.height - 8
        ))
    }
}

private struct RecordingOverlayView: View {
    let state: MenuState

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: state.iconName)
                .foregroundStyle(state.tint)
            Text(state.label)
                .font(.system(size: 13, weight: .medium))
                .lineLimit(2)
        }
        .padding(.horizontal, 14)
        .frame(width: 280, height: 56)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}
