import AppKit
import SwiftUI

struct MenuContentView: View {
    @EnvironmentObject var supervisor: ProcessSupervisor
    @EnvironmentObject var settings: SettingsStore

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: supervisor.statusIconName)
                    .font(.system(size: 20))
                    .foregroundStyle(supervisor.menuState.tint)
                    .frame(width: 26)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Voice AI")
                        .font(.system(size: 13, weight: .semibold))
                    Text(supervisor.menuState.label)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer()
            }

            Divider()

            Button {
                openSettings()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "keyboard")
                        .foregroundStyle(.secondary)
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Shortcut")
                        Text("Click to change")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    Text(shortcutLabel)
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .font(.system(size: 12))
            .accessibilityLabel("Change keyboard shortcut, currently \(shortcutLabel)")

            if supervisor.client != .running {
                Button("Restart local worker") {
                    Task { try? await supervisor.restartClient() }
                }
                .controlSize(.small)
            }
            if case .controlConfirm = supervisor.menuState {
                Button("Confirm action") { supervisor.confirmPendingControl() }
                    .controlSize(.small)
            }

            Divider()

            menuButton("Settings…", systemImage: "gearshape") {
                openSettings()
            }

            menuButton("Quit Voice AI", systemImage: "power") {
                NSApp.terminate(nil)
            }
            .keyboardShortcut("q")
        }
        .padding(14)
        .frame(width: 300)
    }

    private var shortcutLabel: String {
        (try? Hotkey.parse(settings.hotkey).displayName) ?? settings.hotkey
    }

    private func openSettings() {
        StatusBarController.shared.dismissPopover()
        NSApp.activate(ignoringOtherApps: true)
        SettingsWindowController.shared.show()
    }

    private func menuButton(
        _ title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: systemImage)
                    .frame(width: 18)
                Text(title)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .font(.system(size: 13))
        .frame(height: 24)
    }
}
