// B21 spike: does Apple's on-device speech recognition keep fillers, and how soon does a word show?
// Feeds an audio file in real time (as a live mic would) to one recognizer and prints every result with the wall
// time it arrived. Usage: live <speech|dictation|sf> <locale> <file> [--fast] [--context]
import AVFoundation
import Foundation
import Speech

let args = CommandLine.arguments
let mode = args[1], locale = Locale(identifier: args[2]), url = URL(fileURLWithPath: args[3])
let fast = args.contains("--fast"), context = args.contains("--context")
let t0 = Date()
func now() -> Double { Date().timeIntervalSince(t0) }
func emit(_ d: [String: Any]) {
    let j = try! JSONSerialization.data(withJSONObject: d, options: [.sortedKeys])
    print(String(data: j, encoding: .utf8)!)
    fflush(stdout)
}

/// The file as buffers of 100 ms at `fmt`, handed over at the pace they were spoken.
func feed(_ fmt: AVAudioFormat, _ give: (AVAudioPCMBuffer) -> Void) throws {
    let file = try AVAudioFile(forReading: url)
    let conv = AVAudioConverter(from: file.processingFormat, to: fmt)!
    let inBuf = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.processingFormat.sampleRate / 10))!
    var sent = 0.0
    while file.framePosition < file.length {
        try file.read(into: inBuf)
        if inBuf.frameLength == 0 { break }
        let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(fmt.sampleRate / 10) + 64)!
        var done = false
        conv.convert(to: out, error: nil) { _, st in
            if done { st.pointee = .noDataNow; return nil }
            done = true; st.pointee = .haveData; return inBuf
        }
        give(out)
        sent += Double(inBuf.frameLength) / file.processingFormat.sampleRate
        let ahead = sent - now()
        if ahead > 0 { Thread.sleep(forTimeInterval: ahead) }
    }
}

func ranges(_ t: AttributedString) -> [[String: Any]] {
    t.runs.compactMap { run in
        guard let r = run.audioTimeRange else { return nil }
        return ["w": String(t[run.range].characters), "s": r.start.seconds, "e": r.end.seconds]
    }
}

@available(macOS 26, *)
func analyzer() async throws {
    let module: any SpeechModule
    let results: AsyncThrowingStream<(String, Bool, [[String: Any]]), Error>
    if mode == "speech" {
        let t = SpeechTranscriber(locale: locale, transcriptionOptions: [],
                                  reportingOptions: fast ? [.volatileResults, .fastResults] : [.volatileResults],
                                  attributeOptions: [.audioTimeRange])
        module = t
        results = AsyncThrowingStream { c in Task { do { for try await r in t.results { c.yield((String(r.text.characters), r.isFinal, ranges(r.text))) }; c.finish() } catch { c.finish(throwing: error) } } }
    } else {
        let t = DictationTranscriber(locale: locale, contentHints: [], transcriptionOptions: [],
                                     reportingOptions: fast ? [.volatileResults, .frequentFinalization] : [.volatileResults],
                                     attributeOptions: [.audioTimeRange])
        module = t
        results = AsyncThrowingStream { c in Task { do { for try await r in t.results { c.yield((String(r.text.characters), r.isFinal, ranges(r.text))) }; c.finish() } catch { c.finish(throwing: error) } } }
    }
    let status = await AssetInventory.status(forModules: [module])
    emit(["event": "assets", "status": "\(status)"])
    if let req = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
        try await req.downloadAndInstall()
        emit(["event": "installed"])
    }
    let an = SpeechAnalyzer(modules: [module])
    if context {
        let ctx = AnalysisContext()
        ctx.contextualStrings[.general] = ["um", "uh", "ah", "er", "hmm", "you know", "kind of", "sort of", "like", "basically"]
        try await an.setContext(ctx)
    }
    let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [module])!
    emit(["event": "format", "rate": fmt.sampleRate, "ch": fmt.channelCount])
    let (stream, cont) = AsyncStream<AnalyzerInput>.makeStream()
    try await an.start(inputSequence: stream)
    let reader = Task { for try await (text, final, rs) in results { emit(["t": now(), "final": final, "text": text, "words": rs]) } }
    let started = now()
    emit(["event": "start", "t": started])
    try feed(fmt) { cont.yield(AnalyzerInput(buffer: $0)) }
    emit(["event": "fed", "t": now()])
    cont.finish()
    try await an.finalizeAndFinishThroughEndOfInput()
    try await reader.value
    emit(["event": "end", "t": now()])
}

func sf() async throws {
    let r = SFSpeechRecognizer(locale: locale)!
    emit(["event": "sf", "onDevice": r.supportsOnDeviceRecognition, "available": r.isAvailable])
    guard r.supportsOnDeviceRecognition else { emit(["event": "refused: not on device"]); return }
    let req = SFSpeechAudioBufferRecognitionRequest()
    req.requiresOnDeviceRecognition = true
    req.shouldReportPartialResults = true
    req.addsPunctuation = false
    let done = DispatchSemaphore(value: 0)
    let task = r.recognitionTask(with: req) { res, err in
        if let res {
            let ws = res.bestTranscription.segments.map { ["w": $0.substring, "s": $0.timestamp, "e": $0.timestamp + $0.duration] as [String: Any] }
            emit(["t": now(), "final": res.isFinal, "text": res.bestTranscription.formattedString, "words": ws])
            if res.isFinal { done.signal() }
        }
        if let err { emit(["event": "error", "text": "\(err)"]); done.signal() }
    }
    let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    emit(["event": "start", "t": now()])
    try feed(fmt) { req.append($0) }
    req.endAudio()
    _ = done.wait(timeout: .now() + 60)
    _ = task
    emit(["event": "end", "t": now()])
}

let sem = DispatchSemaphore(value: 0)
Task {
    do {
        if mode == "sf" {
            let auth = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
            emit(["event": "auth", "status": auth.rawValue])
            try await sf()
        } else if #available(macOS 26, *) { try await analyzer() }
    } catch { emit(["event": "error", "text": "\(error)"]) }
    sem.signal()
}
sem.wait()
