import Foundation
import Security

/// Minimal Keychain wrapper for the single WaveCam auth token. The token is a
/// secret, so it belongs in the Keychain rather than UserDefaults/@AppStorage.
/// Stored as a generic password keyed by `service` + `account`, readable after
/// first unlock so launch-time configuration works without an explicit unlock.
enum KeychainStore {
    static let tokenAccount = "auth.token"

    private static let service = Bundle.main.bundleIdentifier ?? "com.stonezone.WaveCam"

    @discardableResult
    static func save(_ value: String, account: String) -> Bool {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let updated = SecItemUpdate(query as CFDictionary,
                                    [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return true }
        if updated == errSecItemNotFound {
            var insert = query
            insert[kSecValueData as String] = data
            // Tighter than AfterFirstUnlock: the LAN token is only readable while the
            // device is unlocked and never migrates off this device via backup.
            insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            return SecItemAdd(insert as CFDictionary, nil) == errSecSuccess
        }
        return false
    }

    static func load(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return value
    }

    @discardableResult
    static func delete(account: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    /// One-time move of a token previously persisted in UserDefaults into the
    /// Keychain, then clears the legacy copy. Safe to call on every launch.
    static func migrateLegacyToken(legacyDefaultsKey: String) {
        let defaults = UserDefaults.standard
        guard let legacy = defaults.string(forKey: legacyDefaultsKey), !legacy.isEmpty else {
            return
        }
        if load(account: tokenAccount) == nil {
            // Don't drop the legacy token unless it's safely in the Keychain.
            guard save(legacy, account: tokenAccount) else { return }
        }
        defaults.removeObject(forKey: legacyDefaultsKey)
    }
}
