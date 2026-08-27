import Foundation
import AVFoundation
import Vision

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
    fputs("Usage: ocr_engine <video_path_or_url>\n", stderr)
    exit(1)
}

let videoInput = CommandLine.arguments[1]
let videoURL = URL(fileURLWithPath: videoInput)

let asset = AVAsset(url: videoURL)
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
// Cover the lower 55% of the video to handle both 16:9 horizontal and 9:16 vertical video subtitle layouts
textRequest.regionOfInterest = CGRect(x: 0.02, y: 0.01, width: 0.96, height: 0.55)

let requestHandler = VNSequenceRequestHandler()

var rawReadings: [(time: Double, text: String)] = []
var sampleCount = 0
let sampleInterval = 6 // ~5fps for 30fps

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
    
    try? requestHandler.perform([textRequest], on: pixelBuffer, orientation: .up)
    
    if let results = textRequest.results {
        var candidates: [(text: String, y: CGFloat)] = []
        for obs in results {
            let text = obs.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            // Filter noise
            if text.count >= 2 {
                candidates.append((text: text, y: obs.boundingBox.origin.y))
            }
        }
        
        if !candidates.isEmpty {
            // Sort by vertical position (bottom-most subtitle line)
            candidates.sort { $0.y < $1.y }
            let line = candidates.map { $0.text }.joined(separator: " ")
            rawReadings.append((time: seconds, text: line))
        }
    }
}

func cleanText(_ s: String) -> String {
    return s.replacingOccurrences(of: " ", with: "")
        .replacingOccurrences(of: "，", with: "")
        .replacingOccurrences(of: "。", with: "")
        .replacingOccurrences(of: "！", with: "")
        .replacingOccurrences(of: "？", with: "")
}

func similarity(_ a: String, _ b: String) -> Double {
    let ca = cleanText(a)
    let cb = cleanText(b)
    if ca == cb { return 1.0 }
    if ca.isEmpty || cb.isEmpty { return 0.0 }
    if ca.contains(cb) || cb.contains(ca) { return 0.85 }
    
    let setA = Set(ca)
    let setB = Set(cb)
    let intersection = setA.intersection(setB).count
    let union = setA.union(setB).count
    return Double(intersection) / Double(union)
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
    if let cur = currentCue {
        let sim = similarity(cur.text, reading.text)
        let timeDiff = reading.time - cur.endTime
        
        if sim >= 0.65 && timeDiff <= 1.2 {
            var updatedText = cur.text
            if reading.text.count > cur.text.count {
                updatedText = reading.text
            }
            currentCue = RawCue(startTime: cur.startTime, endTime: reading.time + 0.2, text: updatedText)
        } else {
            if cur.endTime - cur.startTime >= 0.25 {
                cues.append(cur)
            }
            currentCue = RawCue(startTime: reading.time, endTime: reading.time + 0.2, text: reading.text)
        }
    } else {
        currentCue = RawCue(startTime: reading.time, endTime: reading.time + 0.2, text: reading.text)
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
