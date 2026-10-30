// Export exactly one selected identity; requests and passwords arrive on stdin.
import Foundation
import Security
import CryptoKit

struct Request: Decodable {
    let fingerprint: String
    let password: String
    let destination: String
}

func fail(_ status: OSStatus) -> Never {
    FileHandle.standardError.write(Data("Keychain export failed (status \(status)).\n".utf8))
    exit(1)
}

do {
    let request = try JSONDecoder().decode(Request.self, from: FileHandle.standardInput.readDataToEndOfFile())
    let query: [String: Any] = [kSecClass as String: kSecClassIdentity,
                              kSecMatchLimit as String: kSecMatchLimitAll,
                              kSecReturnRef as String: true]
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    guard status == errSecSuccess, let identities = result as? [SecIdentity] else { fail(status) }
    for identity in identities {
        var certificate: SecCertificate?
        guard SecIdentityCopyCertificate(identity, &certificate) == errSecSuccess,
              let certificate = certificate else { continue }
        let data = SecCertificateCopyData(certificate) as Data
        let fingerprint = Insecure.SHA1.hash(data: data).map { String(format: "%02X", $0) }.joined()
        if fingerprint != request.fingerprint.uppercased() { continue }
        let passphrase = request.password as CFString
        var parameters = SecItemImportExportKeyParameters()
        parameters.version = UInt32(SEC_KEY_IMPORT_EXPORT_PARAMS_VERSION)
        parameters.passphrase = Unmanaged.passUnretained(passphrase)
        var exported: CFData?
        let exportStatus = SecItemExport(identity, .formatPKCS12, [], &parameters, &exported)
        guard exportStatus == errSecSuccess, let exported = exported else { fail(exportStatus) }
        try (exported as Data).write(to: URL(fileURLWithPath: request.destination), options: .atomic)
        exit(0)
    }
    fail(errSecItemNotFound)
} catch {
    FileHandle.standardError.write(Data("Invalid export request or output path.\n".utf8))
    exit(1)
}
