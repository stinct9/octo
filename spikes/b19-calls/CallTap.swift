// SPIKE (B19, docs/backlog.md): not part of the Octo app and not built by `octo launcher install`.
// Answers: can a Core Audio process tap take one app's sound, with the mic beside it, into one two-channel file
// (channel 1 the mic = the owner, channel 2 the app = the others) at 16 kHz; what permission it asks; whether
// buffers drop; and whether the Mac can tell when an app starts using the mic. Results: docs/learnings.md (B19).
//
//   CallTap procs                          every process Core Audio knows, and whether it's using the mic/speakers
//   CallTap watch SECONDS                  print when a process starts or stops using the mic (listeners, no polling)
//   CallTap record PID SECONDS OUT.wav     tap PID's sound + the default mic → 16 kHz 16-bit 2-channel WAV
//
// Built into an ad hoc signed .app by build.sh, because the "System Audio Recording" permission belongs to an app.
import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

setvbuf(stdout, nil, _IOLBF, 0)
let t0 = Date()
func log(_ s: String) { print(String(format: "[%7.2f] ", Date().timeIntervalSince(t0)) + s) }
func fail(_ s: String, _ err: OSStatus = 0) -> Never { log("FAIL \(s) \(err)"); exit(1) }

let system = AudioObjectID(kAudioObjectSystemObject)
func addr(_ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
func get<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ initial: T, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> T? {
    var a = addr(sel, scope), v = initial, size = UInt32(MemoryLayout<T>.size)
    return AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &v) == noErr ? v : nil
}
func getString(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    get(obj, sel, nil as CFString?).flatMap { $0 as String? }
}
func getIDs(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> [AudioObjectID] {
    var a = addr(sel, scope), size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(obj, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    return AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &ids) == noErr ? ids : []
}

struct Proc { let id: AudioObjectID; let pid: pid_t; let bundle: String; let input: Bool; let output: Bool }
func proc(_ id: AudioObjectID) -> Proc {
    Proc(id: id, pid: get(id, kAudioProcessPropertyPID, pid_t(-1)) ?? -1,
         bundle: getString(id, kAudioProcessPropertyBundleID) ?? "",
         input: (get(id, kAudioProcessPropertyIsRunningInput, UInt32(0)) ?? 0) != 0,
         output: (get(id, kAudioProcessPropertyIsRunningOutput, UInt32(0)) ?? 0) != 0)
}
func procs() -> [Proc] { getIDs(system, kAudioHardwarePropertyProcessObjectList).map(proc) }
func describe(_ p: Proc) -> String {
    let name = p.bundle.isEmpty ? (ProcessInfo.processInfo.processName(of: p.pid)) : p.bundle
    return "pid \(p.pid) \(name) mic=\(p.input ? 1 : 0) out=\(p.output ? 1 : 0)"
}
extension ProcessInfo {
    func processName(of pid: pid_t) -> String {
        var buf = [CChar](repeating: 0, count: 4096)
        return proc_pidpath(pid, &buf, UInt32(buf.count)) > 0 ? String(cString: buf) : "?"
    }
}

let args = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn") }
switch args.first {
case "perm":
    // The mic's permission as macOS reports it for whoever is "responsible" for this process. The system-audio one
    // has no public query: an app learns it only by whether the tap carries sound.
    let st = AVCaptureDevice.authorizationStatus(for: .audio)
    print("mic permission: \(["notDetermined", "restricted", "denied", "authorized"][st.rawValue])")

case "procs":
    for p in procs() { print(describe(p)) }

case "watch":
    // What "Record this call?" would hang on: a listener per process on IsRunningInput, and one on the process list
    // so processes that appear later get one too. Nothing is polled.
    let seconds = Double(args.dropFirst().first ?? "60") ?? 60
    var watched = Set<AudioObjectID>()
    let q = DispatchQueue(label: "watch")
    func watchAll() {
        for p in procs() where !watched.contains(p.id) {
            watched.insert(p.id)
            log((p.input ? "already on the mic: " : "new: ") + describe(p))
            for (sel, scope) in [(kAudioProcessPropertyIsRunningInput, kAudioObjectPropertyScopeGlobal),
                                 (kAudioProcessPropertyIsRunning, kAudioObjectPropertyScopeGlobal),
                                 (kAudioProcessPropertyDevices, kAudioObjectPropertyScopeInput)] {
                var a = addr(sel, scope)
                AudioObjectAddPropertyListenerBlock(p.id, &a, q) { _, _ in
                    let now = proc(p.id)
                    log("[\(sel == kAudioProcessPropertyIsRunningInput ? "piri" : sel == kAudioProcessPropertyIsRunning ? "pir?" : "pdv#")] "
                        + (now.input ? "ON the mic: " : "off the mic: ") + describe(now))
                }
            }
        }
    }
    q.sync(execute: watchAll)
    var list = addr(kAudioHardwarePropertyProcessObjectList)
    let st = AudioObjectAddPropertyListenerBlock(system, &list, q) { _, _ in log("process list changed"); watchAll() }
    if st != noErr { log("list listener: \(st)") }
    log("watching \(watched.count) processes for \(Int(seconds)) s")
    RunLoop.main.run(until: Date().addingTimeInterval(seconds))

case "record":
    guard args.count >= 4, let pid = pid_t(args[args.startIndex + 1]), let seconds = Double(args[args.startIndex + 2]) else {
        fail("usage: record PID SECONDS OUT.wav [tap-only]")
    }
    let out = URL(fileURLWithPath: args[args.startIndex + 3])
    let tapOnly = args.last == "tap-only"  // no mic, so only the system-audio permission can be asked
    guard let target = procs().first(where: { $0.pid == pid })?.id else {
        fail("pid \(pid) has no Core Audio process object (it hasn't opened audio yet)")
    }
    log("target \(describe(proc(target)))")

    // The tap: a stereo mixdown of that one process (stereo so its buffer is told apart from the mono mic below).
    let desc = CATapDescription(stereoMixdownOfProcesses: [target])
    desc.uuid = UUID()
    desc.isPrivate = true
    desc.muteBehavior = .unmuted
    var tap = AudioObjectID(0)
    var err = AudioHardwareCreateProcessTap(desc, &tap)
    if err != noErr { fail("AudioHardwareCreateProcessTap", err) }
    if let f = get(tap, kAudioTapPropertyFormat, AudioStreamBasicDescription()) {
        log("tap \(tap) format \(f.mSampleRate) Hz \(f.mChannelsPerFrame) ch")
    }

    // One private aggregate device: the default mic as the clock, the tap drift-corrected to it, so both arrive in
    // the same callback on one timeline.
    guard let mic = get(system, kAudioHardwarePropertyDefaultInputDevice, AudioObjectID(0)),
          let micUID = getString(mic, kAudioDevicePropertyDeviceUID) else { fail("no default input") }
    let out0 = get(system, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID(0)) ?? 0
    let clockUID = tapOnly ? getString(out0, kAudioDevicePropertyDeviceUID)! : micUID
    log("mic \(getString(mic, kAudioObjectPropertyName) ?? micUID), output \(getString(out0, kAudioObjectPropertyName) ?? "?")")
    let agg: [String: Any] = [
        kAudioAggregateDeviceNameKey: "Octo call spike",
        kAudioAggregateDeviceUIDKey: UUID().uuidString,
        kAudioAggregateDeviceMainSubDeviceKey: clockUID,
        kAudioAggregateDeviceIsPrivateKey: true,
        kAudioAggregateDeviceIsStackedKey: false,
        kAudioAggregateDeviceTapAutoStartKey: true,
        kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: clockUID]],
        kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: desc.uuid.uuidString]],
    ]
    var dev = AudioObjectID(0)
    err = AudioHardwareCreateAggregateDevice(agg as CFDictionary, &dev)
    if err != noErr { fail("AudioHardwareCreateAggregateDevice", err) }
    let rate = get(dev, kAudioDevicePropertyNominalSampleRate, Float64(0)) ?? 48000
    log("aggregate \(dev) at \(rate) Hz")

    // The file: ExtAudioFile converts float at the device's rate to 16 kHz 16-bit itself.
    var fileFmt = AudioStreamBasicDescription(mSampleRate: 16000, mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 4,
        mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)
    var clientFmt = AudioStreamBasicDescription(mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kLinearPCMFormatFlagIsFloat | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 8,
        mFramesPerPacket: 1, mBytesPerFrame: 8, mChannelsPerFrame: 2, mBitsPerChannel: 32, mReserved: 0)
    var file: ExtAudioFileRef?
    err = ExtAudioFileCreateWithURL(out as CFURL, kAudioFileWAVEType, &fileFmt, nil, AudioFileFlags.eraseFile.rawValue, &file)
    if err != noErr { fail("ExtAudioFileCreateWithURL", err) }
    err = ExtAudioFileSetProperty(file!, kExtAudioFileProperty_ClientDataFormat, UInt32(MemoryLayout.size(ofValue: clientFmt)), &clientFmt)
    if err != noErr { fail("client format", err) }
    ExtAudioFileWriteAsync(file!, 0, nil)  // primes its buffers off the audio thread

    // Counters, read on the main thread once a minute (a spike: plain vars, the races are harmless for counting).
    var callbacks = 0, frames = 0, gaps = 0, gapFrames = 0.0, writeErrs = 0, layoutLogged = false
    var nextSample = -1.0, micPeak: Float = 0, appPeak: Float = 0, micSq = 0.0, appSq = 0.0, sqN = 0
    let maxFrames = 16384
    let inter = UnsafeMutablePointer<Float>.allocate(capacity: maxFrames * 2)
    var overloads = 0
    var ovl = addr(kAudioDeviceProcessorOverload)
    AudioObjectAddPropertyListenerBlock(dev, &ovl, DispatchQueue.main) { _, _ in overloads += 1 }

    var ioProc: AudioDeviceIOProcID?
    err = AudioDeviceCreateIOProcIDWithBlock(&ioProc, dev, nil) { _, input, inTime, _, _ in
        let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        if !layoutLogged {
            layoutLogged = true
            log("buffers: " + abl.map { "\($0.mNumberChannels)ch \($0.mDataByteSize)B" }.joined(separator: ", "))
        }
        // The mic is the mono buffer, the tap the stereo one (checked by ear and by level, learnings).
        let micBuf = abl.first(where: { $0.mNumberChannels == 1 })
        guard let appBuf = abl.first(where: { $0.mNumberChannels == 2 }), micBuf != nil || tapOnly else { return }
        let n = min(micBuf.map { Int($0.mDataByteSize) / 4 } ?? maxFrames, Int(appBuf.mDataByteSize) / 8, maxFrames)
        let st = inTime.pointee.mSampleTime
        if nextSample >= 0, st != nextSample { gaps += 1; gapFrames += st - nextSample }
        nextSample = st + Double(n)
        let m = micBuf?.mData?.assumingMemoryBound(to: Float.self), a = appBuf.mData!.assumingMemoryBound(to: Float.self)
        for i in 0..<n {
            let mv = m?[i] ?? 0, av = (a[2 * i] + a[2 * i + 1]) * 0.5
            inter[2 * i] = mv; inter[2 * i + 1] = av
            micPeak = max(micPeak, abs(mv)); appPeak = max(appPeak, abs(av))
            micSq += Double(mv * mv); appSq += Double(av * av)
        }
        sqN += n
        var w = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer(mNumberChannels: 2, mDataByteSize: UInt32(n * 8), mData: inter))
        if ExtAudioFileWriteAsync(file!, UInt32(n), &w) != noErr { writeErrs += 1 }
        callbacks += 1; frames += n
    }
    if err != noErr { fail("AudioDeviceCreateIOProcIDWithBlock", err) }
    err = AudioDeviceStart(dev, ioProc)
    if err != noErr { fail("AudioDeviceStart", err) }
    log("recording \(Int(seconds)) s → \(out.path)")

    func db(_ x: Double) -> String { x > 0 ? String(format: "%.1f", 10 * log10(x)) : "-inf" }
    func report() {
        log("callbacks \(callbacks) frames \(frames) (\(String(format: "%.1f", Double(frames) / rate)) s) gaps \(gaps) (\(Int(gapFrames)) frames) "
            + "overloads \(overloads) writeErrs \(writeErrs) | mic rms \(db(micSq / Double(max(sqN, 1)))) dBFS peak \(String(format: "%.3f", micPeak))"
            + " | app rms \(db(appSq / Double(max(sqN, 1)))) dBFS peak \(String(format: "%.3f", appPeak))")
        micPeak = 0; appPeak = 0; micSq = 0; appSq = 0; sqN = 0
    }
    var stop = false
    signal(SIGINT) { _ in exit(130) }
    let end = Date().addingTimeInterval(seconds)
    var nextReport = Date().addingTimeInterval(min(10, seconds))
    while !stop {
        RunLoop.main.run(until: min(end, nextReport))
        if Date() >= nextReport { report(); nextReport = Date().addingTimeInterval(60) }
        stop = Date() >= end
    }
    AudioDeviceStop(dev, ioProc)
    AudioDeviceDestroyIOProcID(dev, ioProc!)
    report()
    ExtAudioFileDispose(file!)
    AudioHardwareDestroyAggregateDevice(dev)
    AudioHardwareDestroyProcessTap(tap)
    log("done: \((try? FileManager.default.attributesOfItem(atPath: out.path)[.size] as? Int) ?? 0) bytes")

default:
    print("CallTap procs | watch SECONDS | record PID SECONDS OUT.wav")
    exit(2)
}
