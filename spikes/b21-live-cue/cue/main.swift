// B21: macapp/FillerCue.swift end to end, as the apps run it: a file's audio fed at the pace it was spoken, in the file's
// own format (FillerCue converts), each cue printed with when it came. Counts only, never text.
// Build: swiftc -O -parse-as-library -o cue main.swift ../../../macapp/FillerCue.swift; run: ./cue FILE PHRASE...
import AVFoundation

@main enum Main {
    static func main() async throws {
        let args = CommandLine.arguments
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: args[1]))
        let phrases = Array(args.dropFirst(2))
        guard #available(macOS 26, *) else { return print("needs macOS 26") }
        let t0 = Date()
        var cues: [String: Int] = [:], late = 0
        let cue = try await FillerCue.start(phrases: phrases) { p, isLate in
            cues[p, default: 0] += 1
            if isLate { late += 1 }
        }
        let fmt = file.processingFormat
        let step = AVAudioFrameCount(fmt.sampleRate / 50)  // 20 ms, about what an audio callback gives
        var sent = 0.0
        while file.framePosition < file.length {
            let b = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: step)!
            try file.read(into: b)
            cue.feed(b)
            sent += Double(b.frameLength) / fmt.sampleRate
            let ahead = sent - Date().timeIntervalSince(t0)
            if ahead > 0 { try await Task.sleep(nanoseconds: UInt64(ahead * 1e9)) }
        }
        try await Task.sleep(nanoseconds: 3_000_000_000)  // the last guesses
        cue.stop()
        print("fed \(Int(sent)) s at \(Int(fmt.sampleRate)) Hz; cues", cues.sorted { $0.key < $1.key }, "late", late)
    }
}
