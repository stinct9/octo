// B21: the live filler cue while the phone records a memo or the Mac records a call (docs/backlog.md B21; learnings,
// Speech, 22 Oct). Apple's SpeechAnalyzer hears the owner as they talk; each phrase from their filler list it hears is
// a cue. One file for both apps: the Mac app compiles it beside Octo.swift (octo/launcher.py), the phone's Xcode project
// refers to it here.
//
// Audio stays on the device: SpeechAnalyzer has no server mode, its model is Apple's on-device one (downloaded once
// from Apple, like a dictation language), and Octo won't start the cue unless that model is installed or installs.
// The words it hears are only counted here: never stored, never sent. The lab's own transcript stays the record.
import AVFoundation
import Foundation
import Speech

/// The owner's filler list matched as the lab matches it (octo/fillers.py `matcher` and `find`): whole words, longest
/// phrase first, a hesitation however long it's held (umm, hmmm), and "uh huh" / "mm hmm" not counted (listening).
struct FillerMatcher {
    static let hesitations: Set<String> = ["um", "uh", "ah", "er", "erm", "hmm", "mm", "eh"]
    let phrases: [String]
    private let re: NSRegularExpression?

    init(_ list: [String]) {
        phrases = list.sorted { ($0.count, $1) > ($1.count, $0) }  // longest first, then a-z
        let parts = phrases.map { p -> String in
            if FillerMatcher.hesitations.contains(p) {
                var seen = Set<Character>()
                return p.filter { seen.insert($0).inserted }.map { NSRegularExpression.escapedPattern(for: String($0)) + "+" }.joined()
            }
            return p.split(separator: " ").map { NSRegularExpression.escapedPattern(for: String($0)) }.joined(separator: "[\\s,]+")
        }
        re = parts.isEmpty ? nil : try? NSRegularExpression(
            pattern: "(?<![\\w'])(?:" + parts.map { "(\($0))" }.joined(separator: "|") + ")(?![\\w'])", options: [.caseInsensitive])
    }

    /// Each filler in `text`, as the phrase it counts as.
    func find(_ text: String) -> [String] {
        guard let re else { return [] }
        let s = text as NSString
        return re.matches(in: text, range: NSRange(location: 0, length: s.length)).compactMap { m in
            guard let g = (1...phrases.count).first(where: { m.range(at: $0).location != NSNotFound }) else { return nil }
            let key = phrases[g - 1]
            let after = s.substring(from: m.range.upperBound).prefix(8).lowercased()
            let before = String(s.substring(to: m.range.location).suffix(6)).lowercased()
            if ["uh", "mm", "hmm"].contains(key), after.range(of: "^[\\s,-]*(huh|hmm)\\b", options: .regularExpression) != nil { return nil }
            if ["hmm", "mm"].contains(key), before.range(of: "\\b(uh|mm)[\\s,-]*$", options: .regularExpression) != nil { return nil }
            return key
        }
    }

    func counts(_ text: String) -> [String: Int] { find(text).reduce(into: [:]) { $0[$1, default: 0] += 1 } }
}

/// Turns the analyzer's results into cues, each filler once. A volatile result is Apple's best guess so far at
/// everything since the last final one, revised about once a second; a final one settles the start of that stretch.
/// So: a cue for each filler a guess has more of than were cued for the stretch, and a final one takes its own off.
struct FillerTally {
    let matcher: FillerMatcher
    private var cued: [String: Int] = [:]  // cued in the stretch not yet final

    init(_ matcher: FillerMatcher) { self.matcher = matcher }

    /// The cues this result adds.
    mutating func take(_ text: String, final: Bool) -> [String] {
        var out: [String] = []
        for (p, n) in matcher.counts(text) where n > cued[p, default: 0] {
            out += Array(repeating: p, count: n - cued[p, default: 0])
            cued[p] = n
        }
        if final { for (p, n) in matcher.counts(text) { cued[p] = max(0, cued[p, default: 0] - n) } }
        return out
    }
}

// SpeechAnalyzer is in macOS 26's SDK (Swift 6.2, Xcode 26): a Mac on macOS 14 or 15 has older tools, and the app
// must still build there, without the cue (install review, 5 Nov).
#if compiler(>=6.2)
@available(iOS 26, macOS 26, *)
final class FillerCue {
    struct Refused: Error { let text: String }

    private let analyzer: SpeechAnalyzer
    private let input: AsyncStream<AnalyzerInput>.Continuation
    private let format: AVAudioFormat
    private var converter: AVAudioConverter?
    private var reader: Task<Void, Never>?

    /// Ready to hear, or Refused in plain words. `cue` is called on the main thread with each filler heard, and whether
    /// it's late: only Apple's settled pass heard it, up to ~15 s after (mostly "uh"; every "you know" on the owner's
    /// pitch memos came within 1.5 s). A late one counts, but it's too late to flash.
    static func start(phrases: [String], cue: @escaping (String, Bool) -> Void) async throws -> FillerCue {
        guard SpeechTranscriber.isAvailable else { throw Refused(text: "This device can't recognise speech on itself, so the cue is off.") }
        guard !phrases.isEmpty else { throw Refused(text: "Your filler list is empty.") }
        let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale.current) ?? Locale(identifier: "en-US")
        // Volatile results with fastResults: a word shows ~0.4 s after it's said (the spike, 22 Oct)
        let t = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults, .fastResults],
                                  attributeOptions: [])
        do {
            if let req = try await AssetInventory.assetInstallationRequest(supporting: [t]) { try await req.downloadAndInstall() }
        } catch {
            throw Refused(text: "The on-device speech model isn't installed and couldn't be fetched: \(error.localizedDescription)")
        }
        guard await AssetInventory.status(forModules: [t]) == .installed else {
            throw Refused(text: "The on-device speech model isn't installed, so the cue is off.")
        }
        guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [t]) else {
            throw Refused(text: "The speech model takes no audio format Octo can give it.")
        }
        let (stream, cont) = AsyncStream<AnalyzerInput>.makeStream()
        let analyzer = SpeechAnalyzer(modules: [t])
        try await analyzer.start(inputSequence: stream)
        let me = FillerCue(analyzer: analyzer, input: cont, format: fmt)
        me.reader = Task {
            var tally = FillerTally(FillerMatcher(phrases))
            do {
                for try await r in t.results {
                    for p in tally.take(String(r.text.characters), final: r.isFinal) { await MainActor.run { cue(p, r.isFinal) } }
                }
            } catch {
                print("[octo] filler cue: \(error.localizedDescription)")
            }
        }
        return me
    }

    private init(analyzer: SpeechAnalyzer, input: AsyncStream<AnalyzerInput>.Continuation, format: AVAudioFormat) {
        self.analyzer = analyzer
        self.input = input
        self.format = format
    }

    /// Audio from the owner's microphone, in any format; called from one audio thread.
    func feed(_ buf: AVAudioPCMBuffer) {
        if converter == nil || converter!.inputFormat != buf.format { converter = AVAudioConverter(from: buf.format, to: format) }
        guard let converter, let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity:
            AVAudioFrameCount(Double(buf.frameLength) * format.sampleRate / buf.format.sampleRate) + 64) else { return }
        var given = false
        converter.convert(to: out, error: nil) { _, st in
            if given { st.pointee = .noDataNow; return nil }
            given = true
            st.pointee = .haveData
            return buf
        }
        if out.frameLength > 0 { input.yield(AnalyzerInput(buffer: out)) }
    }

    func stop() {
        input.finish()
        let (a, r) = (analyzer, reader)
        Task { await a.cancelAndFinishNow(); r?.cancel() }
    }
}
#endif
