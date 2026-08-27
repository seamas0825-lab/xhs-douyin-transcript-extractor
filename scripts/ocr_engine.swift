import Foundation
import AVFoundation
import Vision
import CoreVideo

struct SubtitleCue: Codable {
    var start: String
    var end: String
    var text: String
}

struct OCRResult: Codable {
    var has_subtitles: Bool
    var cues: [SubtitleCue]
    var transcript: String
}

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: ocr_engine <video_path>\n", stderr)
    exit(1)
}

let videoInput = CommandLine.arguments[1]
let videoURL = URL(fileURLWithPath: videoInput)

let asset = AVURLAsset(url: videoURL)
let reader: AVAssetReader
do {
    reader = try AVAssetReader(asset: asset)
} catch {
    fputs("Error creating AVAssetReader: \(error)\n", stderr)
    exit(1)
}

guard let videoTrack = asset.tracks(withMediaType: .video).first else {
    fputs("No video track found\n", stderr)
    exit(1)
}

// Dynamically compute sample interval for strict 5 fps regardless of source fps (24, 30, 60, 120)
var sourceFPS: Double = Double(videoTrack.nominalFrameRate)
if sourceFPS <= 0 {
    let minDur = videoTrack.minFrameDuration
    if minDur.timescale > 0 && minDur.value > 0 {
        sourceFPS = Double(minDur.timescale) / Double(minDur.value)
    } else {
        sourceFPS = 30.0
    }
}

let targetSampleRate: Double = 5.0 // 5 samples per second
let sampleInterval = max(1, Int(round(sourceFPS / targetSampleRate)))

let outputSettings: [String: Any] = [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
]
let trackOutput = AVAssetReaderTrackOutput(track: videoTrack, outputSettings: outputSettings)
trackOutput.alwaysCopiesSampleData = false
reader.add(trackOutput)
reader.startReading()

let textRequest = VNRecognizeTextRequest()
textRequest.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
textRequest.recognitionLevel = .accurate
textRequest.usesLanguageCorrection = true
// Focus on lower 55% of the frame (covers 16:9 bottom subtitles & 9:16 vertical video subtitle bands)
textRequest.regionOfInterest = CGRect(x: 0.02, y: 0.01, width: 0.96, height: 0.55)

let requestHandler = VNSequenceRequestHandler()

var rawReadings: [(time: Double, text: String)] = []
var sampleCount = 0

// Fast change-detection cache
var lastFrameSignature: [UInt8] = []
var lastRecognizedText: String = ""

func computeFrameSignature(pixelBuffer: CVPixelBuffer) -> [UInt8] {
    CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
    
    guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
        return []
    }
    
    let width = CVPixelBufferGetWidth(pixelBuffer)
    let height = CVPixelBufferGetHeight(pixelBuffer)
    let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
    
    // Sample a 16x8 grid in the lower 40% subtitle area
    let startY = Int(Double(height) * 0.55)
    let endY = Int(Double(height) * 0.95)
    let gridX = 16
    let gridY = 8
    
    var signature = [UInt8](repeating: 0, count: gridX * gridY)
    let stepX = max(1, width / gridX)
    let stepY = max(1, (endY - startY) / gridY)
    
    let buffer = baseAddress.assumingMemoryBound(to: UInt8.self)
    
    var idx = 0
    for gy in 0..<gridY {
        let py = startY + gy * stepY
        for gx in 0..<gridX {
            let px = gx * stepX
            let offset = py * bytesPerRow + px * 4
            // Extract grayscale luminance (B=0, G=1, R=2)
            let b = UInt32(buffer[offset])
            let g = UInt32(buffer[offset + 1])
            let r = UInt32(buffer[offset + 2])
            let luma = UInt8((r * 299 + g * 587 + b * 114) / 1000)
            signature[idx] = luma
            idx += 1
        }
    }
    return signature
}

func signatureDiff(sig1: [UInt8], sig2: [UInt8]) -> Double {
    if sig1.count != sig2.count || sig1.isEmpty { return 1.0 }
    var totalDiff = 0
    for i in 0..<sig1.count {
        totalDiff += abs(Int(sig1[i]) - Int(sig2[i]))
    }
    return Double(totalDiff) / Double(sig1.count * 255)
}

while let sampleBuffer = trackOutput.copyNextSampleBuffer() {
    sampleCount += 1
    if sampleCount % sampleInterval != 0 {
        continue
    }
    
    let time = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
    let seconds = CMTimeGetSeconds(time)
    
    guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
        continue
    }
    
    let currentSig = computeFrameSignature(pixelBuffer: pixelBuffer)
    let diff = signatureDiff(sig1: lastFrameSignature, sig2: currentSig)
    
    // If the subtitle band has minimal change (< 2.5% diff), reuse the previous OCR result directly
    if diff < 0.025 && !lastRecognizedText.isEmpty {
        rawReadings.append((time: seconds, text: lastRecognizedText))
        lastFrameSignature = currentSig
        continue
    }
    
    try? requestHandler.perform([textRequest], on: pixelBuffer, orientation: .up)
    
    var line = ""
    if let results = textRequest.results {
        var candidates: [(text: String, y: CGFloat)] = []
        for obs in results {
            let text = obs.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if text.count >= 2 {
                candidates.append((text: text, y: obs.boundingBox.origin.y))
            }
        }
        
        if !candidates.isEmpty {
            candidates.sort { $0.y < $1.y }
            line = candidates.map { $0.text }.joined(separator: " ")
            rawReadings.append((time: seconds, text: line))
        }
    }
    
    lastRecognizedText = line
    lastFrameSignature = currentSig
}

func cleanText(_ s: String) -> String {
    let toRemove = CharacterSet(charactersIn: " ，。！？、：；“”‘’\"'()[]-~")
    return s.components(separatedBy: toRemove).joined()
}

func levenshteinDistance(_ a: String, _ b: String) -> Int {
    let aChars = Array(a)
    let bChars = Array(b)
    let m = aChars.count
    let n = bChars.count
    
    if m == 0 { return n }
    if n == 0 { return m }
    
    var matrix = [[Int]](repeating: [Int](repeating: 0, count: n + 1), count: m + 1)
    
    for i in 0...m { matrix[i][0] = i }
    for j in 0...n { matrix[0][j] = j }
    
    for i in 1...m {
        for j in 1...n {
            if aChars[i - 1] == bChars[j - 1] {
                matrix[i][j] = matrix[i - 1][j - 1]
            } else {
                matrix[i][j] = min(
                    matrix[i - 1][j] + 1,
                    matrix[i][j - 1] + 1,
                    matrix[i - 1][j - 1] + 1
                )
            }
        }
    }
    return matrix[m][n]
}

func similarity(_ a: String, _ b: String) -> Double {
    let ca = cleanText(a)
    let cb = cleanText(b)
    if ca == cb { return 1.0 }
    if ca.isEmpty || cb.isEmpty { return 0.0 }
    if ca.contains(cb) || cb.contains(ca) { return 0.85 }
    
    let maxLen = max(ca.count, cb.count)
    if maxLen == 0 { return 1.0 }
    let dist = levenshteinDistance(ca, cb)
    return 1.0 - (Double(dist) / Double(maxLen))
}

func formatTime(_ sec: Double) -> String {
    let hrs = Int(sec) / 3600
    let mins = (Int(sec) % 3600) / 60
    let secs = Int(sec) % 60
    let millis = Int((sec - Double(Int(sec))) * 1000)
    return String(format: "%02d:%02d:%02d.%03d", hrs, mins, secs, millis)
}

struct RawCue {
    var startTime: Double
    var endTime: Double
    var text: String
}

var cues: [RawCue] = []
var currentCue: RawCue? = nil

for reading in rawReadings {
    let cleaned = cleanText(reading.text)
    if cleaned.count < 2 {
        continue
    }
    
    if let cur = currentCue {
        let sim = similarity(cur.text, reading.text)
        let timeDiff = reading.time - cur.endTime
        
        if sim >= 0.60 && timeDiff <= 1.2 {
            var updatedText = cur.text
            if reading.text.count > cur.text.count {
                updatedText = reading.text
            }
            currentCue = RawCue(startTime: cur.startTime, endTime: reading.time + 0.25, text: updatedText)
        } else {
            if cur.endTime - cur.startTime >= 0.25 {
                cues.append(cur)
            }
            currentCue = RawCue(startTime: reading.time, endTime: reading.time + 0.25, text: reading.text)
        }
    } else {
        currentCue = RawCue(startTime: reading.time, endTime: reading.time + 0.25, text: reading.text)
    }
}

if let cur = currentCue, cur.endTime - cur.startTime >= 0.25 {
    cues.append(cur)
}

let formattedCues = cues.map { cue in
    SubtitleCue(
        start: formatTime(cue.startTime),
        end: formatTime(cue.endTime),
        text: cue.text
    )
}

let fullTranscript = formattedCues.map { $0.text }.joined(separator: " ")

let finalResult = OCRResult(
    has_subtitles: !formattedCues.isEmpty,
    cues: formattedCues,
    transcript: fullTranscript
)

let encoder = JSONEncoder()
encoder.outputFormatting = .prettyPrinted
if let data = try? encoder.encode(finalResult), let str = String(data: data, encoding: .utf8) {
    print(str)
}
