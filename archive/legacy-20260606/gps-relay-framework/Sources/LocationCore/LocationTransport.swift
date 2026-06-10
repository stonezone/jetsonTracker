import Foundation

public protocol LocationTransport {
    func open()
    func push(_ update: RelayUpdate)
    func close()
}
