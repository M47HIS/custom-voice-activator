// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VoiceAI",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "VoiceAI", targets: ["VoiceAI"]),
    ],
    targets: [
        .executableTarget(
            name: "VoiceAI",
            path: "Sources/VoiceActivator"
        ),
        .testTarget(
            name: "VoiceActivatorTests",
            dependencies: ["VoiceAI"],
            path: "Tests/VoiceActivatorTests"
        ),
    ]
)
