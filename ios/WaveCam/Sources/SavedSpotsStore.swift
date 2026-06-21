import Foundation
import Observation

/// A reusable filming spot. Location + base height are recalled verbatim; `lastHeadingDeg`
/// is a STARTING GUESS only — the operator confirms/refines heading on site (never an
/// auto-lock). iOS-local (UserDefaults); not shared/persisted server-side in v2.
struct SavedSpot: Codable, Identifiable, Equatable {
    var id: UUID = UUID()
    var name: String
    var lat: Double
    var lon: Double
    var baseHeightM: Double
    var lastHeadingDeg: Double?
}

@Observable
final class SavedSpotsStore {
    private let defaults: UserDefaults
    private let key = "wavecam.savedSpots.v1"
    private(set) var spots: [SavedSpot] = []

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        load()
    }

    func add(_ spot: SavedSpot) { spots.append(spot); persist() }

    func update(_ spot: SavedSpot) {
        guard let i = spots.firstIndex(where: { $0.id == spot.id }) else { return }
        spots[i] = spot
        persist()
    }

    func remove(_ spot: SavedSpot) {
        spots.removeAll { $0.id == spot.id }
        persist()
    }

    private func load() {
        guard let data = defaults.data(forKey: key),
              let decoded = try? JSONDecoder().decode([SavedSpot].self, from: data) else { return }
        spots = decoded
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(spots) { defaults.set(data, forKey: key) }
    }
}
