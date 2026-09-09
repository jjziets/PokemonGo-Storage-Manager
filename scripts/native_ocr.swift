// Persistent Apple Vision worker. Requests are a 4-byte big-endian JSON header
// length, the UTF-8 header, then tightly packed RGB bytes. Responses are JSONL.
// Only one image is retained per request; the scanner owns its bounded window.
import Foundation
import Vision
import CoreGraphics

private let input = FileHandle.standardInput
private let output = FileHandle.standardOutput
private let maximumBytes = 64 * 1024 * 1024

private struct Request: Decodable {
    let id: Int
    let frame_id: String
    let width: Int
    let height: Int
    let payload_bytes: Int
    let mode: String
}

private enum WorkerError: Error { case invalidRequest, invalidImage, truncatedInput }

private func readExactly(_ count: Int) throws -> Data? {
    var data = Data()
    data.reserveCapacity(count)
    while data.count < count {
        let part = try input.read(upToCount: count - data.count) ?? Data()
        if part.isEmpty {
            if data.isEmpty { return nil }
            throw WorkerError.truncatedInput
        }
        data.append(part)
    }
    return data
}

private func respond(_ response: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])
    data.append(0x0a)
    try output.write(contentsOf: data)
}

private func recognize(_ request: Request, pixels: Data) throws -> [String: Any] {
    guard let provider = CGDataProvider(data: pixels as CFData),
          let image = CGImage(
            width: request.width, height: request.height,
            bitsPerComponent: 8, bitsPerPixel: 24, bytesPerRow: request.width * 3,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false,
            intent: .defaultIntent
          ) else { throw WorkerError.invalidImage }
    let recognition = VNRecognizeTextRequest()
    recognition.recognitionLevel = request.mode == "accurate" ? .accurate : .fast
    recognition.recognitionLanguages = ["en-US"]
    recognition.usesLanguageCorrection = false
    recognition.minimumTextHeight = 0.008
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    let start = DispatchTime.now().uptimeNanoseconds
    try handler.perform([recognition])
    let elapsed = Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000
    let observations = (recognition.results ?? []).compactMap { observation -> [String: Any]? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        // API coordinates match PIL/calibration: top-left origin, pixel units.
        return ["text": candidate.string, "confidence": candidate.confidence,
                "bbox": [box.minX * Double(request.width),
                         (1 - box.maxY) * Double(request.height),
                         box.width * Double(request.width),
                         box.height * Double(request.height)]]
    }
    return ["id": request.id, "frame_id": request.frame_id,
            "width": request.width, "height": request.height,
            "ocr_ms": elapsed, "observations": observations]
}

do {
    while let prefix = try readExactly(4) {
        let headerSize = prefix.reduce(0) { ($0 << 8) | Int($1) }
        guard (1...16384).contains(headerSize),
              let header = try readExactly(headerSize) else { throw WorkerError.invalidRequest }
        let request = try JSONDecoder().decode(Request.self, from: header)
        guard (1...8192).contains(request.width), (1...8192).contains(request.height),
              request.payload_bytes == request.width * request.height * 3,
              request.payload_bytes <= maximumBytes,
              ["fast", "accurate"].contains(request.mode),
              let pixels = try readExactly(request.payload_bytes) else {
            throw WorkerError.invalidRequest
        }
        // Bound temporary Vision allocations even in a long-lived scan.
        try autoreleasepool {
            do {
                try respond(recognize(request, pixels: pixels))
            } catch {
                try respond(["id": request.id, "frame_id": request.frame_id,
                             "error": String(describing: error)])
            }
        }
    }
} catch {
    // Protocol corruption cannot be resynchronized safely. The parent notices
    // EOF and discards this request instead of associating another frame's text.
    exit(1)
}
