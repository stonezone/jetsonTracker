import SwiftUI
import MapKit

/// UIKit bridge: an MKMapView with hybrid (satellite + labels) imagery, feeding a
/// MapPlacementModel. Kept separate from MapPlacementView (SRP: UIKit/MapKit bridge
/// vs the SwiftUI screen). The map center (under a fixed crosshair in the screen) is
/// the placement point: base coords in .base mode, look-at coords in .headingLookAt.
struct MapKitContainer: UIViewRepresentable {
    let model: MapPlacementModel
    let initialLat: Double
    let initialLon: Double

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> MKMapView {
        let mv = MKMapView()
        mv.delegate = context.coordinator
        mv.mapType = .hybrid
        mv.isRotateEnabled = false        // North-up (V6): fixed frame for the arrow mode
        mv.isPitchEnabled = false
        mv.showsCompass = false
        let center = CLLocationCoordinate2D(latitude: initialLat, longitude: initialLon)
        mv.setRegion(MKCoordinateRegion(center: center, latitudinalMeters: 150, longitudinalMeters: 150),
                     animated: false)
        // In heading mode the base is already locked — show it so the operator can
        // pan the crosshair to a distant look-at landmark relative to it.
        if model.mode == .headingLookAt, let bla = model.baseLat, let blo = model.baseLon {
            let pin = MKPointAnnotation()
            pin.coordinate = CLLocationCoordinate2D(latitude: bla, longitude: blo)
            pin.title = "Base"
            mv.addAnnotation(pin)
        }
        return mv
    }

    func updateUIView(_ mv: MKMapView, context: Context) {}

    final class Coordinator: NSObject, MKMapViewDelegate {
        let model: MapPlacementModel
        init(model: MapPlacementModel) { self.model = model }

        func mapViewDidFinishLoadingMap(_ mapView: MKMapView) { model.tilesLoaded = true }
        func mapViewDidFailLoadingMap(_ mapView: MKMapView, withError error: Error) { model.tilesLoaded = false }

        func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
            let rect = mapView.visibleMapRect
            let west = MKMapPoint(x: rect.minX, y: rect.midY)
            let east = MKMapPoint(x: rect.maxX, y: rect.midY)
            let metersAcross = west.distance(to: east)   // meters (replaces MKMetersBetweenMapPoints)
            let c = mapView.centerCoordinate
            switch model.mode {
            case .base:
                model.baseLat = c.latitude; model.baseLon = c.longitude
            case .headingLookAt:
                model.lookAtLat = c.latitude; model.lookAtLon = c.longitude
            case .headingArrow:
                break
            }
            model.lastErrorRadiusM = model.errorRadiusM(metersAcross: metersAcross,
                                                        screenWidthPoints: Double(max(mapView.bounds.width, 1)))
        }
    }
}
