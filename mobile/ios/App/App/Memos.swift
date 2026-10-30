// Memos (docs/backlog.md B8, B9, B25): recordings made on this phone, and Voice Memos or Notes' call recordings shared
// to Octo (Share → Octo, or Share Audio), kept in an outbox on the phone until the paired Mac has them. Sent to POST /api/octo/recordings with the device
// token, over the session that trusts only the Mac's pinned authority (Lab.swift); the Mac turns them into
// conversations like any memo. The outbox is left out of iCloud backups: audio goes to the owner's Mac, nowhere else.
//
// JS: `registerPlugin('Memos')` (mobile/src/memos.ts), registered by OctoViewController (Lab.swift).
import AudioToolbox
import AVFoundation
import Capacitor
import Speech
import UIKit

enum Outbox {
    /// What the Mac accepts (octo/api.py AUDIO_TYPES).
    static let types: Set<String> = ["m4a", "mp3", "wav", "aac", "mp4", "caf", "aif", "aiff", "flac", "ogg", "opus", "3gp"]

    static let dir: URL = {
        var d = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Outbox", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        var v = URLResourceValues()
        v.isExcludedFromBackup = true
        try? d.setResourceValues(v)
        return d
    }()

    /// A file's id is "<ms>-<its name>", so two memos with the same name don't collide.
    static func newId(_ name: String) -> String { "\(Int(Date().timeIntervalSince1970 * 1000))-\(name)" }

    static func item(_ url: URL) -> [String: Any] {
        let v = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
        let id = url.lastPathComponent
        let name = id.split(separator: "-", maxSplits: 1).last.map(String.init) ?? id
        return ["id": id, "name": name, "size": v?.fileSize ?? 0,
                "modified": (v?.contentModificationDate ?? Date()).timeIntervalSince1970 * 1000]
    }

    /// Waiting to be sent, oldest first. A recording still being made starts with "." and isn't listed.
    static func items() -> [[String: Any]] {
        let files = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return files.filter { !$0.lastPathComponent.hasPrefix(".") }.map(item)
            .sorted { ($0["modified"] as? Double ?? 0) < ($1["modified"] as? Double ?? 0) }
    }

    /// The file for an id from JS, only ever inside the outbox.
    static func file(_ id: String) -> URL? {
        guard !id.isEmpty, !id.contains("/"), !id.hasPrefix(".") else { return nil }
        let u = dir.appendingPathComponent(id)
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }
}

// MARK: - Recording on this phone

final class PhoneRecorder: NSObject {
    private var rec: AVAudioRecorder?
    private var id = ""
    private(set) var startedAt: Date?
    // B21: the live filler cue hears the mic through an engine beside the recorder; the memo's file is the recorder's alone
    private var engine: AVAudioEngine?
    private var cue: AnyObject?  // FillerCue (iOS 26)

    var on: Bool { rec != nil }

    /// iOS 26 or later, on a phone that can recognise speech on itself.
    static var cueAvailable: Bool {
        if #available(iOS 26, *) { return SpeechTranscriber.isAvailable }
        return false
    }

    /// B21: listen for the owner's fillers while the memo records. Called once recording has started: the memo never waits
    /// for the cue, and a cue that can't start says why in `failed` and leaves the memo as it is.
    func listen(phrases: [String], haptic: Bool, heard: @escaping (String, Bool) -> Void, failed: @escaping (String) -> Void) {
        guard #available(iOS 26, *) else { return failed("The filler cue needs iOS 26 or later.") }
        if haptic { try? AVAudioSession.sharedInstance().setAllowHapticsAndSystemSoundsDuringRecording(true) }
        Task { @MainActor in
            do {
                let c = try await FillerCue.start(phrases: phrases) { phrase, late in
                    // shortcut: 1519 is iOS's short "peek" tap, which plays with the screen locked where UIKit's haptics
                    // don't; undocumented, so swap it for Core Haptics if a later iOS drops it
                    if haptic && !late { AudioServicesPlaySystemSound(1519) }
                    heard(phrase, late)
                }
                guard self.on else { return c.stop() }
                let e = AVAudioEngine()
                let input = e.inputNode
                input.installTap(onBus: 0, bufferSize: 4096, format: input.outputFormat(forBus: 0)) { buf, _ in c.feed(buf) }
                try e.start()
                // The memo comes first: if the engine took the mic from the recorder (unverified on a phone), it gives way
                if let rec, !rec.isRecording {
                    input.removeTap(onBus: 0)
                    e.stop()
                    rec.record()
                    c.stop()
                    return failed("The filler cue couldn't run beside the recording on this phone, so it's off.")
                }
                engine = e
                cue = c
            } catch {
                failed((error as? FillerCue.Refused)?.text ?? "The filler cue didn't start: \(error.localizedDescription)")
            }
        }
    }

    private func stopListening() {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        if #available(iOS 26, *) { (cue as? FillerCue)?.stop() }
        cue = nil
    }

    func start() throws {
        let s = AVAudioSession.sharedInstance()
        try s.setCategory(.record, mode: .default)
        try s.setActive(true)
        let stamp = DateFormatter()
        stamp.dateFormat = "yyyy-MM-dd HH.mm"
        id = Outbox.newId("Octo \(stamp.string(from: Date())).m4a")
        // AAC in an .m4a, as Voice Memos: its header carries when it was recorded (octo/audio.py recorded_at).
        // Mono 16 kHz is what the lab keeps of any recording.
        let r = try AVAudioRecorder(url: Outbox.dir.appendingPathComponent("." + id), settings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ])
        guard r.record() else { throw NSError(domain: "octo", code: 1, userInfo: [NSLocalizedDescriptionKey: "couldn't start"]) }
        rec = r
        startedAt = Date()
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted), name: AVAudioSession.interruptionNotification, object: s)
    }

    /// A phone call pauses the recording; carry on once it's over.
    @objc private func interrupted(_ n: Notification) {
        guard let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              AVAudioSession.InterruptionType(rawValue: raw) == .ended, let rec else { return }
        try? AVAudioSession.sharedInstance().setActive(true)
        rec.record()
        try? engine?.start()
    }

    /// Stop, and put the finished file in the outbox.
    func stop() -> URL? {
        guard let rec else { return nil }
        stopListening()
        rec.stop()
        self.rec = nil
        startedAt = nil
        NotificationCenter.default.removeObserver(self, name: AVAudioSession.interruptionNotification, object: nil)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        let done = Outbox.dir.appendingPathComponent(id)
        do { try FileManager.default.moveItem(at: rec.url, to: done) } catch { return nil }
        return done
    }
}

// MARK: - The plugin

@objc(MemosPlugin)
public class MemosPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "MemosPlugin"
    public let jsName = "Memos"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pending", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "take", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "send", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "remove", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "recording", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "mark", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "fillers", returnType: CAPPluginReturnPromise),
    ]

    private let recorder = PhoneRecorder()

    @objc func pending(_ call: CAPPluginCall) {
        call.resolve(["items": Outbox.items()])
    }

    /// A file iOS handed to Octo (Share → Octo from Voice Memos, or Files): moved into the outbox.
    @objc func take(_ call: CAPPluginCall) {
        guard let s = call.getString("url"), let url = URL(string: s), url.isFileURL else {
            call.reject("not a file")
            return
        }
        let name = url.lastPathComponent
        guard Outbox.types.contains(url.pathExtension.lowercased()) else {
            call.reject("not audio")
            return
        }
        let to = Outbox.dir.appendingPathComponent(Outbox.newId(name))
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            // Documents/Inbox is iOS's copy for us: move it. Anything else is someone else's file: copy it.
            if url.path.contains("/Documents/Inbox/") { try FileManager.default.moveItem(at: url, to: to) }
            else { try FileManager.default.copyItem(at: url, to: to) }
        } catch {
            call.reject(error.localizedDescription)
            return
        }
        call.resolve(Outbox.item(to))
    }

    /// Send one file to the Mac at `host`. 200: the Mac has it; 409: it had it already. Either way it leaves the
    /// phone. Anything else keeps it. 0: the Mac didn't answer.
    @objc func send(_ call: CAPPluginCall) {
        guard let id = call.getString("id"), let file = Outbox.file(id), let host = call.getString("host"),
              let ca = call.getString("ca"), let token = Keychain.load() else {
            call.reject("missing")
            return
        }
        let port = call.getInt("port") ?? 8766
        let item = Outbox.item(file)
        var q = URLComponents()
        q.queryItems = [URLQueryItem(name: "name", value: item["name"] as? String),
                        URLQueryItem(name: "source", value: "phone"),
                        URLQueryItem(name: "modified", value: String(Int(item["modified"] as? Double ?? 0)))]
        let h = host.contains(":") ? "[\(host)]" : host
        guard let url = URL(string: "https://\(h):\(port)/api/octo/recordings?\(q.percentEncodedQuery ?? "")") else {
            call.reject("bad address")
            return
        }
        var req = URLRequest(url: url, timeoutInterval: 60)
        req.httpMethod = "POST"
        req.setValue("octo_device=\(token)", forHTTPHeaderField: "Cookie")
        req.setValue("1", forHTTPHeaderField: "x-octo-upload")
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        // shortcut: a foreground upload with iOS's few minutes' grace if the app goes to the background; a long
        // recording sent over a slow link may be cut off and is tried again next time. A background URLSession
        // would carry on with the app closed, if that turns out to be needed.
        let grace = UIApplication.shared.beginBackgroundTask(withName: "octo.send")  // safe from any thread
        Task {
            defer { UIApplication.shared.endBackgroundTask(grace) }
            var status = 0
            var message = ""
            do {
                let (data, resp) = try await PinnedSession(ca: ca).session.upload(for: req, fromFile: file)
                status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { message = json["detail"] as? String ?? "" }
            } catch {
                print("[octo] memos: \(id) to \(host): \(error.localizedDescription)")
            }
            if status == 200 || status == 409 { try? FileManager.default.removeItem(at: file) }
            call.resolve(["status": status, "message": message])
        }
    }

    /// B16: mark this moment. The Mac's clock places it, so nothing is kept to send later. 0: the Mac didn't answer.
    /// With "undo", the mark of that id is taken back instead (within a minute; the Mac refuses any other).
    @objc func mark(_ call: CAPPluginCall) {
        guard let host = call.getString("host"), let ca = call.getString("ca"), let token = Keychain.load() else {
            call.reject("missing")
            return
        }
        let h = host.contains(":") ? "[\(host)]" : host
        let undo = call.getString("undo")?.addingPercentEncoding(withAllowedCharacters: .alphanumerics)
        guard let url = URL(string: "https://\(h):\(call.getInt("port") ?? 8766)/api/octo/marks" + (undo.map { "/\($0)/delete" } ?? "")) else {
            call.reject("bad address")
            return
        }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.httpMethod = "POST"
        req.setValue("octo_device=\(token)", forHTTPHeaderField: "Cookie")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        Task {
            var status = 0
            var id = ""
            do {
                let (data, resp) = try await PinnedSession(ca: ca).session.data(for: req)
                status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                let mark = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["mark"] as? [String: Any]
                id = mark?["id"] as? String ?? ""
            } catch {
                print("[octo] mark to \(host): \(error.localizedDescription)")
            }
            call.resolve(["status": status, "id": id])
        }
    }

    /// B21: the owner's filler list from the Mac (GET /api/octo/fillers/live). 0: the Mac didn't answer.
    @objc func fillers(_ call: CAPPluginCall) {
        guard let host = call.getString("host"), let ca = call.getString("ca"), let token = Keychain.load() else {
            call.reject("missing")
            return
        }
        let h = host.contains(":") ? "[\(host)]" : host
        guard let url = URL(string: "https://\(h):\(call.getInt("port") ?? 8766)/api/octo/fillers/live") else {
            call.reject("bad address")
            return
        }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.setValue("octo_device=\(token)", forHTTPHeaderField: "Cookie")
        Task {
            var status = 0
            var phrases: [String] = []
            do {
                let (data, resp) = try await PinnedSession(ca: ca).session.data(for: req)
                status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                phrases = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["phrases"] as? [String] ?? []
            } catch {
                print("[octo] fillers from \(host): \(error.localizedDescription)")
            }
            call.resolve(["status": status, "phrases": phrases])
        }
    }

    @objc func remove(_ call: CAPPluginCall) {
        if let id = call.getString("id"), let file = Outbox.file(id) { try? FileManager.default.removeItem(at: file) }
        call.resolve()
    }

    @objc func start(_ call: CAPPluginCall) {
        let go = {
            DispatchQueue.main.async {
                do {
                    let fresh = !self.recorder.on
                    if fresh { try self.recorder.start() }
                    call.resolve(["startedAt": (self.recorder.startedAt ?? Date()).timeIntervalSince1970 * 1000])
                    if fresh, let phrases = call.getArray("cue", String.self), !phrases.isEmpty {
                        self.recorder.listen(phrases: phrases, haptic: call.getBool("haptic") ?? false,
                            heard: { [weak self] p, late in self?.notifyListeners("filler", data: ["phrase": p, "late": late]) },
                            failed: { [weak self] why in self?.notifyListeners("fillerFailed", data: ["text": why]) })
                    }
                } catch {
                    call.reject(error.localizedDescription)
                }
            }
        }
        switch AVAudioSession.sharedInstance().recordPermission {
        case .granted: go()
        case .undetermined: AVAudioSession.sharedInstance().requestRecordPermission { ok in ok ? go() : call.reject("not allowed") }
        default: call.reject("not allowed")
        }
    }

    @objc func stop(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let file = self.recorder.stop() else {
                call.reject("not recording")
                return
            }
            call.resolve(Outbox.item(file))
        }
    }

    @objc func recording(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            var out: [String: Any] = ["on": self.recorder.on, "cueAvailable": PhoneRecorder.cueAvailable]
            if let t = self.recorder.startedAt { out["startedAt"] = t.timeIntervalSince1970 * 1000 }
            call.resolve(out)
        }
    }
}
