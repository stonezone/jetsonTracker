// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "watchTrackerAppFeature",
    platforms: [.watchOS(.v11)],
    products: [
        .library(
            name: "watchTrackerAppFeature",
            targets: ["watchTrackerAppFeature"]
        ),
    ],
    dependencies: [
        .package(path: "../")
    ],
    targets: [
        .target(
            name: "watchTrackerAppFeature",
            dependencies: [
                .product(name: "LocationCore", package: "gps-relay-framework"),
                .product(name: "WatchLocationProvider", package: "gps-relay-framework")
            ]
        ),
        .testTarget(
            name: "watchTrackerAppFeatureTests",
            dependencies: [
                "watchTrackerAppFeature"
            ]
        ),
    ]
)
