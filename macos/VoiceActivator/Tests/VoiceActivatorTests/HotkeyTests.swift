import XCTest
@testable import VoiceAI

final class HotkeyTests: XCTestCase {
    func testHotkeyRoundTripAndValidation() throws {
        XCTAssertEqual(try Hotkey.parse("cmd+shift+space").encode(), "cmd+shift+space")
        XCTAssertEqual(try Hotkey.parse("cmd+shift+space").displayName, "⇧⌘Space")
        XCTAssertThrowsError(try Hotkey.parse("space"))
        XCTAssertThrowsError(try Hotkey.parse("cmd"))
    }

    func testTransientStatesShowOverlay() {
        XCTAssertFalse(MenuState.idle.showsOverlay)
        XCTAssertTrue(MenuState.listening.showsOverlay)
        XCTAssertTrue(MenuState.transcribing.showsOverlay)
        XCTAssertTrue(MenuState.success.showsOverlay)
        XCTAssertTrue(MenuState.error("boom").showsOverlay)
        XCTAssertTrue(MenuState.success.isTransient)
        XCTAssertTrue(MenuState.error("boom").isTransient)
        XCTAssertFalse(MenuState.listening.isTransient)
        XCTAssertEqual(MenuState.success.label, "Copied to clipboard")
        XCTAssertEqual(BackendSettings.default.action, "clipboard")
    }

    func testPidLooksLikeVoiceClientRejectsUnrelatedProcess() {
        // The test runner itself must never be mistaken for the worker.
        XCTAssertFalse(ProcessSupervisor.pidLooksLikeVoiceClient(ProcessInfo.processInfo.processIdentifier))
        // Nonexistent PIDs must not pass either.
        XCTAssertFalse(ProcessSupervisor.pidLooksLikeVoiceClient(999_999_999))
    }

    func testUnknownClicksNeedConfirmationButPhotoBoothCaptureDoesNot() {
        XCTAssertTrue(ProcessSupervisor.needsControlConfirmation(
            kind: "click", title: "Yes", request: "do it", bundle: "com.apple.Safari"))
        XCTAssertTrue(ProcessSupervisor.needsControlConfirmation(
            kind: "click", title: "Take Photo", request: "take a picture", bundle: "com.apple.Safari"))
        XCTAssertFalse(ProcessSupervisor.needsControlConfirmation(
            kind: "click", title: "Take Photo", request: "take a picture", bundle: "com.apple.PhotoBooth"))
    }

    func testModelNavigationNeedsConfirmationAndSceneOmitsFieldValues() {
        XCTAssertTrue(ProcessSupervisor.needsControlConfirmation(
            kind: "open_url", title: "https://example.com", request: "do this",
            bundle: "com.apple.Safari", modelGenerated: true))
        XCTAssertTrue(ProcessSupervisor.needsControlConfirmation(
            kind: "type_text", title: "private text", request: "do this",
            bundle: "com.apple.Safari", modelGenerated: true))
        XCTAssertFalse(ProcessSupervisor.needsControlConfirmation(
            kind: "open_url", title: "https://example.com", request: "open example.com",
            bundle: "com.apple.Safari"))
        let observed: [[String: Any]] = [["id": 1, "title": "Search", "value": "private text"]]
        let sent = ProcessSupervisor.modelScene(observed)
        XCTAssertNil(sent[0]["value"])
        XCTAssertEqual(sent[0]["title"] as? String, "Search")
        XCTAssertEqual(observed[0]["value"] as? String, "private text")
    }
}
