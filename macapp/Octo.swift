// The Octo app (docs/mac-app-plan.md): a window on the lab, the octopus in the menu bar, and the lab's banners.
// Built on the owner's Mac by `octo launcher install` (octo/launcher.py) with swiftc from Apple's command line tools
// and signed ad hoc, so it needs no Xcode project and no developer account. It's only a shell: the lab is the Python
// server on 127.0.0.1:8765 that the com.octo.lab login item runs, and this app never starts or owns it.
//
// Getting in (the Mac's key, octo/mackey.py): the app finds the lab the login item runs, asks it to prove it holds
// the key (/mac-hello, an HMAC of a random challenge), and only then opens /let-in?k=… in an in-memory web view, so
// the server sets its HttpOnly cookie as for a browser and nothing is written to disk. Nothing about the owner is in
// this bundle: /Applications is readable by every user on the Mac.
import AppKit
import AudioToolbox
import AVFoundation
import CoreAudio
import CryptoKit
import EventKit
import ServiceManagement
import UniformTypeIdentifiers
import UserNotifications
import WebKit

// OCTO_LAB_URL and OCTO_LAB_PLIST point a test build at a dev or demo lab (`open --env`); nobody else sets them.
let ENV = ProcessInfo.processInfo.environment
let LAB = URL(string: ENV["OCTO_LAB_URL"] ?? "http://127.0.0.1:8765")!

/// Bring Octo to the front, only ever because the owner asked (a click in the Dock, the menu bar, Spotlight, a menu
/// item). macOS 14's activate() is cooperative, only a request, and the request was refused when Octo had been menu-bar
/// only a moment before (19 Oct: opened from the Dock, the window stayed behind the app in front). The older call
/// still activates regardless (AppKit's header: "If YES, the app activates regardless").
func comeForward() { NSApp.activate(ignoringOtherApps: true) }

// MARK: - The lab: where its key is, whether it's really the lab, and asking it things

enum Lab {
    /// The lab's folder as octo/__init__.py finds it for the login item: OCTO_HOME, else .octo-home in its working
    /// directory, else that directory. Read each time, so the app follows the lab that's actually running.
    static func home() -> URL? {
        guard let p = plist() else { return nil }
        if let h = (p["EnvironmentVariables"] as? [String: String])?["OCTO_HOME"], !h.isEmpty { return path(h) }
        guard let wd = p["WorkingDirectory"] as? String else { return nil }
        let named = (try? String(contentsOf: path(wd).appendingPathComponent(".octo-home"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return path(named.isEmpty ? wd : named)
    }

    static func plist() -> [String: Any]? {
        let url = ENV["OCTO_LAB_PLIST"].map { URL(fileURLWithPath: $0) } ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/com.octo.lab.plist")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
    }

    static func path(_ s: String) -> URL { URL(fileURLWithPath: (s as NSString).expandingTildeInPath) }

    /// `octo ARGS` from the lab's own code folder (the login item's working directory): its exit status and what it
    /// printed, or nil when there's no lab to run it in. `input` is written to its stdin as one line (a password, with
    /// --password-stdin: never in its arguments, where ps shows them); `line` hears each line as it's printed.
    static func octo(_ args: [String], input: String? = nil,
                     line: (@MainActor @Sendable (String) -> Void)? = nil) async -> (status: Int32, text: String)? {
        guard let wd = plist()?["WorkingDirectory"] as? String else { return nil }
        let exe = path(wd).appendingPathComponent(".venv/bin/octo")
        guard FileManager.default.isExecutableFile(atPath: exe.path) else { return nil }
        return await Task.detached {
            let p = Process()
            p.executableURL = exe
            p.arguments = args
            p.environment = ENV.merging(["PYTHONUNBUFFERED": "1"]) { $1 }  // its lines as they come, not at the end
            let out = Pipe(), inp = Pipe()
            p.standardOutput = out
            p.standardError = out
            p.standardInput = inp
            guard (try? p.run()) != nil else { return nil }
            if let input { inp.fileHandleForWriting.write(Data((input + "\n").utf8)) }
            try? inp.fileHandleForWriting.close()
            var all = Data(), pending = Data()
            while case let d = out.fileHandleForReading.availableData, !d.isEmpty {
                all.append(d)
                pending.append(d)
                while let nl = pending.firstIndex(of: 10) {
                    let text = String(decoding: pending[pending.startIndex..<nl], as: UTF8.self)
                    pending.removeSubrange(pending.startIndex...nl)
                    if let line, !text.isEmpty { Task { @MainActor in line(text) } }
                }
            }
            p.waitUntilExit()
            return (p.terminationStatus, String(decoding: all, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines))
        }.value
    }

    /// The commit `octo deploy` last started the lab on (.git/octo-deployed in its code folder) and when; nil for a lab
    /// that was never deployed (a fresh install until its first update).
    static func deployed() -> (commit: String, when: String)? {
        guard let wd = plist()?["WorkingDirectory"] as? String else { return nil }
        let note = path(wd).appendingPathComponent(".git/octo-deployed")
        guard let commit = try? String(contentsOf: note, encoding: .utf8),
              let at = (try? FileManager.default.attributesOfItem(atPath: note.path))?[.modificationDate] as? Date
        else { return nil }
        let f = DateFormatter()
        f.dateFormat = "d MMM yyyy, HH:mm"
        return (String(commit.trimmingCharacters(in: .whitespacesAndNewlines).prefix(7)), f.string(from: at))
    }

    enum Problem: Error { case notInstalled, unreadable }

    static func key() -> Result<String, Problem> {
        guard let home = home() else { return .failure(.notInstalled) }
        guard let k = try? String(contentsOf: home.appendingPathComponent("data/mac.key"), encoding: .utf8)
        else { return .failure(.unreadable) }
        return .success(k.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    static let session: URLSession = {
        let conf = URLSessionConfiguration.ephemeral
        conf.httpShouldSetCookies = false
        conf.requestCachePolicy = .reloadIgnoringLocalCacheData
        conf.timeoutIntervalForRequest = 60
        return URLSession(configuration: conf)
    }()

    /// Whether 127.0.0.1:8765 is the owner's lab: while the lab is down another user's program could listen there,
    /// and it must never be sent the key. The lab answers HMAC-SHA256(key, label + challenge) (octo/mackey.py proof).
    static func proves(_ key: String) async -> Bool {
        let c = (0..<24).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
        let url = LAB.appendingPathComponent("mac-hello").appending(queryItems: [URLQueryItem(name: "c", value: c)])
        guard let (data, resp) = try? await session.data(for: URLRequest(url: url, timeoutInterval: 5)),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let proof = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["proof"] as? String
        else { return false }
        let mac = HMAC<SHA256>.authenticationCode(for: Data(("octo-mac-hello:" + c).utf8),
                                                  using: SymmetricKey(data: Data(key.utf8)))
        return proof == Data(mac).map { String(format: "%02x", $0) }.joined()
    }

    /// A GET of the lab's API with the key, once the lab has proved itself. nil when it didn't answer or isn't the lab.
    @MainActor static func get(_ path: String, _ query: [URLQueryItem] = [], timeout: TimeInterval = 10) async -> [String: Any]? {
        guard case .success(let key) = key(), await proven(key) else { return nil }
        var req = URLRequest(url: LAB.appendingPathComponent(path).appending(queryItems: query), timeoutInterval: timeout)
        req.setValue("octo_mac=\(key)", forHTTPHeaderField: "Cookie")
        guard let (data, resp) = try? await session.data(for: req), (resp as? HTTPURLResponse)?.statusCode == 200
        else { provenKey = nil; return nil }  // asked again next time: the lab may have restarted, or been replaced
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    /// A file POSTed to the lab like a memo dropped on the Conversations page (api.py upload): nil when it took it,
    /// else why not. Its own session: the lab answers only once the file is converted (a long call on speakers: ~1% of
    /// its length for the echo canceller, then FLAC).
    @MainActor static func upload(_ file: URL, query: [URLQueryItem]) async -> String? {
        guard case .success(let key) = key() else { return "the lab's key can't be read" }
        guard await proven(key) else { return "the lab isn't answering" }
        var req = URLRequest(url: LAB.appendingPathComponent("api/octo/recordings").appending(queryItems: query))
        req.httpMethod = "POST"
        req.setValue("octo_mac=\(key)", forHTTPHeaderField: "Cookie")
        req.setValue("1", forHTTPHeaderField: "X-Octo-Upload")
        let conf = URLSessionConfiguration.ephemeral
        conf.httpShouldSetCookies = false
        conf.timeoutIntervalForRequest = 900
        do {
            let (data, resp) = try await URLSession(configuration: conf).upload(for: req, fromFile: file)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200 || code == 409 { return nil }  // 409: already added
            return (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? "the lab answered \(code)"
        } catch {
            return error.localizedDescription
        }
    }

    /// A JSON POST to the lab's API with the key, once the lab has proved itself: whether it took it.
    @MainActor static func post(_ path: String, _ body: [String: Any]) async -> Bool {
        guard case .success(let key) = key(), await proven(key),
              let data = try? JSONSerialization.data(withJSONObject: body) else { return false }
        var req = URLRequest(url: LAB.appendingPathComponent(path), timeoutInterval: 30)
        req.httpMethod = "POST"
        req.setValue("octo_mac=\(key)", forHTTPHeaderField: "Cookie")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        guard let (_, resp) = try? await session.upload(for: req, from: data) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }

    @MainActor static var provenKey: String?
    @MainActor static func proven(_ key: String) async -> Bool {
        if provenKey == key { return true }
        guard await proves(key) else { return false }
        provenKey = key
        return true
    }

    static func letIn(_ key: String, to page: String) -> URL {
        LAB.appendingPathComponent("let-in").appending(queryItems: [URLQueryItem(name: "k", value: key),
                                                                    URLQueryItem(name: "to", value: "/#" + page)])
    }

    /// `launchctl kickstart -k`: the login item starts the lab again (this app never runs the lab itself).
    static func restart() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["kickstart", "-k", "gui/\(getuid())/com.octo.lab"]
        try? p.run()
    }
}

// MARK: - The octopus, from the recorder's own pixels (docs/phone/icons/src/octo.py, r = 9)

enum Octopus {
    static let awake = [
        "...............#####..............", ".............#########............", "...........#############..........",
        "..........###############.........", "..........###############.........", ".........#################........",
        ".........#################........", "........######.#####.######.......", "........####.....#.....####.......",
        "........####...#.#...#.####.......", "........###...###...###.###.......", "........####...#.#...#.####.......",
        "........####.....#.....####.......", "........######.#####.######.......", "........###################.......",
        "..........################........", "..........##############.###......", ".........###############..###.....",
        ".......#############..###...###...", "......#####.###...###...###..###..", "....##.###..###....###...###..##..",
        ".###..###...###....###....###.###.", "###...##.....##.....##....##...###", ".#...##......###.....##...##....#.",
        ".....###......###....###..###.....", "......#........#......#....#......",
    ]
    static let asleep = awake.enumerated().map { i, row in
        [7: "........###################.......", 8: "........###################.......",
         9: "........#####...###...#####.......", 10: "........####.....#.....####.......",
         11: "........###..###...###..###.......", 12: "........###.#####.#####.###.......",
         13: "........###################......."][i] ?? row
    }

    /// cell: points per pixel. dot: a small mark in the empty top-right corner, "something needs you".
    static func image(_ rows: [String], cell: CGFloat, dot: Bool = false, template: Bool = true,
                      color: NSColor = .black) -> NSImage {
        let w = CGFloat(rows[0].count) * cell, h = CGFloat(rows.count) * cell
        let img = NSImage(size: NSSize(width: w, height: h), flipped: true) { _ in
            color.setFill()
            for (y, row) in rows.enumerated() {
                for (x, ch) in row.enumerated() where ch == "#" {
                    NSRect(x: CGFloat(x) * cell, y: CGFloat(y) * cell, width: cell, height: cell).fill()
                }
            }
            if dot { NSBezierPath(ovalIn: NSRect(x: w - 7 * cell, y: 0, width: 7 * cell, height: 7 * cell)).fill() }
            return true
        }
        img.isTemplate = template
        return img
    }
}

// MARK: - The window

/// The header's colour in the lab's pages (ui/src/style.css --surface), so the title bar and the header read as one.
let surface = NSColor(name: nil) { a in
    a.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? NSColor(srgbRed: 0x1E / 255, green: 0x1D / 255, blue: 0x1A / 255, alpha: 1)
        : .white
}

final class LabWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate,
                       WKScriptMessageHandler {
    let window: NSWindow
    let web: WKWebView
    let cover = NSStackView()
    let title = NSTextField(labelWithString: "")
    let detail = NSTextField(wrappingLabelWithString: "")
    let restart = NSButton(title: "Restart the Lab", target: nil, action: nil)
    var loaded = false
    var waiting: Page?
    var retry: Timer?
    var onClose: (() -> Void)?
    var onMessage: ((String) -> Void)?  // the Settings page's Export… and Import… buttons

    typealias Page = String

    override init() {
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .nonPersistent()  // the cookie in memory only; no WebKit folders left behind
        web = WKWebView(frame: .zero, configuration: conf)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        super.init()
        conf.userContentController.add(self, name: "octo")  // window.webkit.messageHandlers.octo: pages know they're in the app
        window.title = "Octo"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = surface
        window.minSize = NSSize(width: 760, height: 520)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("Octo")
        web.navigationDelegate = self
        web.uiDelegate = self
        web.underPageBackgroundColor = surface
        web.setValue(false, forKey: "drawsBackground")  // no white flash before the page paints, in dark mode
        web.isHidden = true

        let art = NSImageView(image: Octopus.image(Octopus.asleep, cell: 3, template: false, color: .tertiaryLabelColor))
        title.font = .systemFont(ofSize: 17, weight: .semibold)
        detail.alignment = .center
        detail.textColor = .secondaryLabelColor
        detail.preferredMaxLayoutWidth = 360
        let again = NSButton(title: "Try Again", target: self, action: #selector(tryAgain))
        restart.target = self
        restart.action = #selector(restartLab)
        let buttons = NSStackView(views: [again, restart])
        cover.orientation = .vertical
        cover.spacing = 12
        cover.setViews([art, title, detail, buttons], in: .center)
        cover.setCustomSpacing(20, after: art)
        cover.isHidden = true

        let root = NSView()
        for v in [web, cover] as [NSView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            root.addSubview(v)
        }
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: root.topAnchor), web.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: root.leadingAnchor), web.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            cover.centerXAnchor.constraint(equalTo: root.centerXAnchor), cover.centerYAnchor.constraint(equalTo: root.centerYAnchor),
        ])
        window.contentView = root
    }

    func show(_ page: Page?) {
        NSApp.setActivationPolicy(.regular)  // in the Dock and ⌘-Tab while the window is open
        comeForward()
        window.makeKeyAndOrderFront(nil)
        DispatchQueue.main.async {  // again once the policy change has landed: before it, activation can be refused
            comeForward()
            self.window.makeKeyAndOrderFront(nil)
        }
        if loaded, let page {
            let js = "location.hash = " + String(data: try! JSONSerialization.data(withJSONObject: page, options: .fragmentsAllowed),
                                                 encoding: .utf8)!
            web.evaluateJavaScript(js)
        } else if !loaded {
            connect(page ?? waiting ?? "/")
        }
    }

    func connect(_ page: Page) {
        waiting = page
        Task { @MainActor in
            switch Lab.key() {
            case .failure(.notInstalled):
                return say("Octo isn't set up on this Mac", "The lab starts when you log in once it's installed. "
                           + "Run ./install.sh in Octo's folder.", canRestart: false)
            case .failure(.unreadable):
                return say("Octo can't read its key", "If another person on this Mac installed Octo, install it for "
                           + "your own account. Otherwise run octo doctor.", canRestart: false)
            case .success(let key):
                guard await Lab.proven(key) else {
                    return say("The lab isn't answering", "It starts when you log in. This window connects by itself "
                               + "once it answers.", canRestart: true)
                }
                web.load(URLRequest(url: Lab.letIn(key, to: page)))
            }
        }
    }

    func say(_ t: String, _ d: String, canRestart: Bool) {
        title.stringValue = t
        detail.stringValue = d
        restart.isHidden = !canRestart
        web.isHidden = true
        cover.isHidden = false
        retry?.invalidate()
        retry = Timer.scheduledTimer(withTimeInterval: 3, repeats: false) { [weak self] _ in
            guard let self, !self.loaded, self.window.isVisible else { return }
            self.connect(self.waiting ?? "/")
        }
    }

    @objc func tryAgain() { connect(waiting ?? "/") }
    @objc func restartLab() { Lab.restart(); connect(waiting ?? "/") }

    func windowWillClose(_ notification: Notification) {
        retry?.invalidate()
        onClose?()
    }

    // Only the lab's own pages load here; anything else opens in the default browser.
    static func isLab(_ url: URL?) -> Bool {
        guard let url else { return false }
        if url.scheme == "blob" || url.scheme == "about" { return true }
        return url.scheme == LAB.scheme && url.host == LAB.host && url.port == LAB.port
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { return decisionHandler(.download) }
        if Self.isLab(action.request.url) { return decisionHandler(.allow) }
        if let url = action.request.url, ["http", "https", "mailto", "macappstore"].contains(url.scheme) { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = ((response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? "")
            .lowercased().hasPrefix("attachment")
        decisionHandler(attachment || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.url : nil) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard Self.isLab(webView.url) else { return }
        loaded = true
        retry?.invalidate()
        cover.isHidden = true
        web.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        loaded = false
        Task { @MainActor in Lab.provenKey = nil }
        say("The lab isn't answering", "It starts when you log in. This window connects by itself once it answers.",
            canRestart: true)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    // target=_blank and window.open: the default browser (About Octo, Hugging Face, TickTick).
    /// A page asks for something only the app can do (export and import run outside the lab server: an import stops
    /// it). Only the lab's own pages: this window loads nothing else, and the origin is checked anyway.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard origin.host == LAB.host, origin.port == (LAB.port ?? 80), let what = message.body as? String else { return }
        onMessage?(what)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        alert(message, buttons: ["OK"]) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        alert(message, buttons: ["OK", "Cancel"]) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let field = NSTextField(string: defaultText ?? "")
        field.frame = NSRect(x: 0, y: 0, width: 260, height: 24)
        alert(prompt, buttons: ["OK", "Cancel"], accessory: field) {
            completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil)
        }
    }

    func alert(_ text: String, buttons: [String], accessory: NSView? = nil, then: @escaping (NSApplication.ModalResponse) -> Void) {
        let a = NSAlert()
        a.messageText = text
        buttons.forEach { a.addButton(withTitle: $0) }
        a.accessoryView = accessory
        a.beginSheetModal(for: window, completionHandler: then)
    }

    // <input type="file">: adding memos.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }
}

// MARK: - The app: menu bar, main menu, banners

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    let lab = LabWindow()
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    var state: [String: Any]?  // the lab's last /api/octo/mac-app; nil while it isn't answering
    var atLogin = false
    lazy var aboutBox = AboutWindow()
    let calls: AnyObject? = { if #available(macOS 14.4, *) { return Calls() } else { return nil } }()  // B19
    let calendar = MacCalendar()  // B23

    func applicationWillFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self  // before launch ends, or a banner's click is lost
        let event = NSAppleEventManager.shared().currentAppleEvent
        atLogin = event?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // With no window open, macOS quits an app on its own (automatic termination, measured 19 Oct: ~30 s after the
        // window closed); NSSupportsAutomaticTermination=false in Info.plist didn't stop it. The octopus must stay.
        ProcessInfo.processInfo.disableAutomaticTermination("The octopus stays in the menu bar")
        ProcessInfo.processInfo.disableSuddenTermination()
        mainMenu()
        menu.delegate = self
        item.menu = menu
        item.button?.setAccessibilityLabel("Octo")
        draw()
        lab.onClose = { NSApp.setActivationPolicy(.accessory) }
        lab.onMessage = { [weak self] what in
            if what == "export" { self?.exportLab() } else if what == "import" { self?.importLab() }
            if what == "calls", #available(macOS 14.4, *) { (self?.calls as? Calls)?.askPermissions() }
            if what == "calendar" { Task { @MainActor in await self?.calendar.read(force: true) } }
        }
        if #available(macOS 14.4, *), let calls = calls as? Calls {
            calls.post = { [weak self] text, info in Task { await self?.post(text, info: info) } }
            calls.changed = { [weak self] in self?.build() }
            calls.shown = { [weak self] text in
                self?.item.button?.title = text.isEmpty ? "" : " " + text
                self?.item.button?.imagePosition = .imageLeading
            }
            UNUserNotificationCenter.current().setNotificationCategories([UNNotificationCategory(
                identifier: "call", actions: [UNNotificationAction(identifier: "record", title: "Record")], intentIdentifiers: [])])
            calls.watch()
        }
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.refresh() }
        refresh()
        Task.detached { await self.listen() }
        let args = CommandLine.arguments
        let page = args.firstIndex(of: "--page").flatMap { $0 + 1 < args.count ? "/" + args[$0 + 1] : nil }
        if !atLogin && !args.contains("--background") { lab.show(page) }  // --background: reopened after an update
    }

    func applicationWillTerminate(_ notification: Notification) {
        if #available(macOS 14.4, *), let calls = calls as? Calls, let r = calls.recorder {
            _ = r.stop()  // the file is whole; it goes to the lab when Octo next starts
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        lab.show(nil)
        return true
    }

    // The menu bar: what the lab says, read every minute and when the menu opens.

    func refresh() {
        Task { @MainActor in
            state = await Lab.get("api/octo/mac-app")
            draw()
            if state?["calendar"] as? Bool == true { await calendar.read() }
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        build()
        refresh()
    }

    func draw() {
        let icon = state?["icon"] as? String
        item.button?.image = Octopus.image(icon == "asleep" ? Octopus.asleep : Octopus.awake, cell: 0.5, dot: icon == "needs")
        item.button?.appearsDisabled = state == nil
        build()
    }

    func build() {
        menu.removeAllItems()
        if let lines = state?["lines"] as? [[String: Any]] {
            for line in lines {
                let text = line["text"] as? String ?? ""
                let page = line["page"] as? String
                let mi = NSMenuItem(title: text, action: line["action"] as? String == "update" ? #selector(updateOcto)
                                        : line["needs"] as? Bool == true ? #selector(openLine(_:)) : nil, keyEquivalent: "")
                mi.target = self
                mi.representedObject = page
                menu.addItem(mi)
            }
            if lines.isEmpty { menu.addItem(NSMenuItem(title: "Nothing needs you", action: nil, keyEquivalent: "")) }
        } else {
            menu.addItem(NSMenuItem(title: "The lab isn't answering", action: nil, keyEquivalent: ""))
            menu.addItem(NSMenuItem(title: "It starts again when you log in", action: nil, keyEquivalent: ""))
            menu.addItem(withTitle: "Restart the Lab", action: #selector(restartLab), keyEquivalent: "").target = self
        }
        if #available(macOS 14.4, *), let calls = calls as? Calls {
            menu.addItem(.separator())
            calls.menuItems().forEach(menu.addItem)
        }
        menu.addItem(.separator())
        let open = menu.addItem(withTitle: "Open Octo", action: #selector(openWindow), keyEquivalent: "")
        open.target = self
        open.attributedTitle = NSAttributedString(string: "Open Octo", attributes: [.font: NSFont.menuFont(ofSize: 0).bold])
        menu.addItem(withTitle: "Open in Browser", action: #selector(openBrowser), keyEquivalent: "").target = self
        menu.addItem(.separator())
        let login = menu.addItem(withTitle: "Open at Login", action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Octo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    }

    @objc func openLine(_ sender: NSMenuItem) { lab.show(sender.representedObject as? String) }
    @objc func openWindow() { lab.show(nil) }
    @objc func restartLab() { Lab.restart(); DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.refresh() } }
    @objc func openPage(_ sender: NSMenuItem) { lab.show(sender.representedObject as? String) }

    /// `octo open`: the default browser let in with the key (it goes in that browser's history, as it always has).
    @objc func openBrowser() {
        Task { @MainActor in
            guard case .success(let key) = Lab.key(), await Lab.proven(key) else { return lab.show(nil) }
            NSWorkspace.shared.open(Lab.letIn(key, to: "/"))
        }
    }

    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else { try SMAppService.mainApp.register() }
        } catch {
            let a = NSAlert(error: error)
            a.runModal()
        }
    }

    // Banners: the lab hands this app what to post (octo/notify.py) while it holds this request open.

    func listen() async {
        var after: Int?  // the last text's number; the next request saying so is how the lab knows it arrived
        while true {
            var query = [URLQueryItem(name: "notify", value: await notifyMode())]
            if let after { query.append(URLQueryItem(name: "after", value: String(after))) }
            if let reply = await Lab.get("api/octo/mac-app/notice", query, timeout: 65) {
                // Not awaited: asking again at once is how the lab knows it arrived, even while macOS's first
                // "Allow notifications?" waits for the owner (otherwise osascript would post it too after 15 s).
                if let text = reply["text"] as? String {
                    let info = (reply["page"] as? String).map { ["page": $0] } ?? [:]  // B23: "before you talk" opens their page
                    Task { await self.post(text, info: info) }
                }
                after = reply["seq"] as? Int ?? after
            } else {
                try? await Task.sleep(nanoseconds: 5_000_000_000)  // the lab isn't answering: ask again shortly
            }
        }
    }

    /// "on", "ask" (macOS hasn't asked the owner: the lab still posts through osascript until it has) or "off".
    func notifyMode() async -> String {
        switch await UNUserNotificationCenter.current().notificationSettings().authorizationStatus {
        case .authorized, .provisional, .ephemeral: return "on"
        case .denied: return "off"
        default: return "ask"
        }
    }

    func post(_ text: String, info: [String: String] = [:]) async {
        let center = UNUserNotificationCenter.current()
        if await center.notificationSettings().authorizationStatus == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound])  // asked when there's something to say
        }
        let content = UNMutableNotificationContent()
        content.body = text  // macOS shows "Octo" and the icon above it
        content.userInfo = info
        if info["call"] != nil { content.categoryIdentifier = "call" }  // "Record this call?": a click, or Record, starts it (B19)
        try? await center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let app = response.notification.request.content.userInfo["call"] as? String
        let page = response.notification.request.content.userInfo["page"] as? String
        DispatchQueue.main.async {
            if let app, #available(macOS 14.4, *) { (self.calls as? Calls)?.start(app) } else if app == nil { self.lab.show(page ?? "/") }
        }
        done()
    }

    // The main menu: without an Edit menu, ⌘C and ⌘V don't work in the pages' fields.

    func mainMenu() {
        let bar = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let m = NSMenu(title: title)
            items.forEach(m.addItem)
            bar.addItem(withTitle: title, action: nil, keyEquivalent: "").submenu = m
            return m
        }
        func mi(_ t: String, _ a: Selector?, _ k: String = "", _ mods: NSEvent.ModifierFlags = .command,
                target: AnyObject? = nil, page: String? = nil) -> NSMenuItem {
            let m = NSMenuItem(title: t, action: a, keyEquivalent: k)
            m.keyEquivalentModifierMask = mods
            m.target = target
            m.representedObject = page
            return m
        }
        let web = lab.web
        _ = sub("Octo", [
            mi("About Octo", #selector(about), target: self), .separator(),
            mi("Settings…", #selector(openPage(_:)), ",", target: self, page: "/settings"), .separator(),
            mi("Hide Octo", #selector(NSApplication.hide(_:)), "h"),
            mi("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            mi("Show All", #selector(NSApplication.unhideAllApplications(_:))), .separator(),
            mi("Update Octo…", #selector(updateOcto), target: self), mi("Uninstall Octo…", #selector(uninstall), target: self),
            .separator(),
            mi("Quit Octo", #selector(NSApplication.terminate(_:)), "q"),
        ])
        _ = sub("File", [
            mi("Export Lab…", #selector(exportLab), "e", [.command, .shift], target: self),
            mi("Import Lab…", #selector(importLab), "i", [.command, .shift], target: self),
        ])
        _ = sub("Edit", [
            mi("Undo", Selector(("undo:")), "z"), mi("Redo", Selector(("redo:")), "z", [.command, .shift]), .separator(),
            mi("Cut", #selector(NSText.cut(_:)), "x"), mi("Copy", #selector(NSText.copy(_:)), "c"),
            mi("Paste", #selector(NSText.paste(_:)), "v"), mi("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        _ = sub("View", [
            mi("Reload Page", #selector(WKWebView.reload(_:)), "r", target: web), .separator(),
            mi("Actual Size", #selector(zoomReset), "0", target: self),
            mi("Zoom In", #selector(zoomIn), "+", target: self), mi("Zoom Out", #selector(zoomOut), "-", target: self),
            .separator(), mi("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        _ = sub("Go", [
            mi("Back", #selector(WKWebView.goBack(_:)), "[", target: web),
            mi("Forward", #selector(WKWebView.goForward(_:)), "]", target: web), .separator(),
            // ⌘1–⌘4 in the header's order (UX review, 21 Oct); the coach has no tab, so it comes after
            mi("Conversations", #selector(openPage(_:)), "1", target: self, page: "/"),
            mi("People", #selector(openPage(_:)), "2", target: self, page: "/people"),
            mi("Practice", #selector(openPage(_:)), "3", target: self, page: "/practice"),
            mi("Progress", #selector(openPage(_:)), "4", target: self, page: "/progress"), .separator(),
            mi("Coach", #selector(openPage(_:)), "5", target: self, page: "/coach"),
        ])
        NSApp.windowsMenu = sub("Window", [
            mi("Close", #selector(NSWindow.performClose(_:)), "w"), mi("Minimize", #selector(NSWindow.miniaturize(_:)), "m"),
            mi("Zoom", #selector(NSWindow.zoom(_:))), .separator(),
            mi("Bring All to Front", #selector(NSApplication.arrangeInFront(_:))),
        ])
        NSApp.helpMenu = sub("Help", [mi("Check This Mac", #selector(openPage(_:)), target: self, page: "/mac")])
        NSApp.mainMenu = bar
    }

    @objc func about() {
        aboutBox.onCheck = { [weak self] in self?.lab.show("/mac") }
        aboutBox.show()
    }
    /// `octo uninstall` (octo/uninstall.py): what it removes, one question about the conversations, then the same
    /// command the terminal runs. The app unregisters its own Open at Login first (only it can) and quits after.
    @objc func uninstall() {
        Task { @MainActor in
            comeForward()
            guard let listing = await Lab.octo(["uninstall", "--dry-run"]), listing.status == 0 else {
                let a = NSAlert()
                a.messageText = "Octo's lab isn't installed for this account"
                a.informativeText = "There's nothing for Uninstall to remove but this app: drag Octo from Applications to the Bin."
                a.runModal()
                return
            }
            let a = NSAlert()
            a.messageText = "Uninstall Octo?"
            a.informativeText = listing.text
            let delete = NSButton(checkboxWithTitle: "Also delete your conversations: recordings, transcripts, reviews, voiceprints",
                                  target: nil, action: nil)
            a.accessoryView = delete
            a.addButton(withTitle: "Uninstall")
            a.addButton(withTitle: "Cancel")
            a.buttons[0].hasDestructiveAction = true
            guard a.runModal() == .alertFirstButtonReturn else { return }
            if delete.state == .on {  // the terminal asks for "delete" typed out: here a second, plain question
                let sure = NSAlert()
                sure.messageText = "Delete your conversations too?"
                sure.informativeText = "They can't be brought back. To keep a copy first, choose Cancel and run "
                    + "octo export in Terminal: one encrypted file a new install can bring back with octo import."
                sure.addButton(withTitle: "Delete Them")
                sure.addButton(withTitle: "Keep Them")
                sure.addButton(withTitle: "Cancel")
                sure.buttons[0].hasDestructiveAction = true
                switch sure.runModal() {
                case .alertFirstButtonReturn: break
                case .alertSecondButtonReturn: delete.state = .off
                default: return
                }
            }
            if SMAppService.mainApp.status == .enabled { try? await SMAppService.mainApp.unregister() }
            let done = await Lab.octo(["uninstall", delete.state == .on ? "--delete-data" : "--keep-data", "--from-app"])
            let r = NSAlert()
            r.messageText = done == nil ? "Uninstall didn't start" : done!.status == 0 ? "Octo is uninstalled" : "Octo couldn't remove everything"
            r.informativeText = done?.text ?? "Nothing was removed. Run octo uninstall in Terminal to see why."
            r.runModal()
            guard done != nil else { return }
            UserDefaults.standard.removePersistentDomain(forName: Bundle.main.bundleIdentifier ?? "online.octolab.Octo")
            exit(0)  // not terminate: closing the window would save its frame to the preferences again
        }
    }

    /// `octo update` (octo/deploy.py): newer code on the lab's own repository (a beta tester's clone; the lab's menu line
    /// says when there is). Asked first; it waits for a running step, updates, and restarts the lab.
    @objc func updateOcto() {
        Task { @MainActor in
            comeForward()
            let a = NSAlert()
            a.messageText = "Update Octo?"
            a.informativeText = "The lab finishes the step it's on, takes the newest version from where it was installed "
                + "from, and restarts. Your conversations aren't touched."
            a.addButton(withTitle: "Update")
            a.addButton(withTitle: "Cancel")
            guard a.runModal() == .alertFirstButtonReturn else { return }
            let done = await Lab.octo(["update"])
            let r = NSAlert()
            r.messageText = done == nil ? "Update didn't start" : done!.status == 0 ? "Octo is up to date" : "Octo couldn't update"
            r.informativeText = done?.text ?? "Run octo update in Terminal to see why."
            r.runModal()
            refresh()
        }
    }

    @objc func zoomReset() { lab.web.pageZoom = 1 }
    @objc func zoomIn() { lab.web.pageZoom = min(lab.web.pageZoom + 0.1, 3) }
    @objc func zoomOut() { lab.web.pageZoom = max(lab.web.pageZoom - 0.1, 0.5) }
}

// MARK: - About Octo

/// Octo → About Octo (19 Oct, after Claude for Mac's): the icon, the name, the app's version and when it was built
/// here, which lab is running and when it was last updated, and two ways on: the page for people who ask what Octo is
/// (octo/pages.py, no sign-in needed; the header leaves its link out inside this app) and Check This Mac.
final class AboutWindow: NSObject {  // like LabWindow: AppKit calls it on the main thread
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 420),
                          styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
    var onCheck: (() -> Void)?

    override init() {
        super.init()
        window.title = "About Octo"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false
        window.backgroundColor = surface
    }

    func show() {  // built each time, so the lab's line is current
        let info = Bundle.main.infoDictionary ?? [:]
        let icon = NSImageView(image: NSApp.applicationIconImage)
        icon.imageScaling = .scaleProportionallyUpOrDown
        for a in [icon.widthAnchor, icon.heightAnchor] { a.constraint(equalToConstant: 96).isActive = true }
        let name = NSTextField(labelWithString: "Octo")
        let serif = NSFont.systemFont(ofSize: 30, weight: .semibold).fontDescriptor.withDesign(.serif)
        name.font = serif.flatMap { NSFont(descriptor: $0, size: 30) } ?? .systemFont(ofSize: 30, weight: .semibold)
        let when = DateFormatter()
        when.dateFormat = "d MMM yyyy, HH:mm"
        let built = Bundle.main.executableURL.flatMap {
            (try? FileManager.default.attributesOfItem(atPath: $0.path))?[.modificationDate] as? Date
        }
        var lines = ["Version \(info["CFBundleShortVersionString"] ?? "?") (\(String(describing: info["CFBundleVersion"] ?? "?").prefix(7)))"]
        if let built { lines.append("Built on this Mac \(when.string(from: built))") }
        if let lab = Lab.deployed() { lines.append("Lab \(lab.commit), updated \(lab.when)") }
        let detail = NSTextField(labelWithString: lines.joined(separator: "\n"))
        detail.alignment = .center
        detail.textColor = .secondaryLabelColor
        detail.font = .systemFont(ofSize: 13)
        detail.isSelectable = true  // the version, to paste into a bug report
        let page = button("What Octo Is", #selector(openAbout))
        let check = button("Check This Mac", #selector(checkMac))
        let stack = NSStackView(views: [icon, name, detail, page, check])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 10
        stack.setCustomSpacing(18, after: icon)
        stack.setCustomSpacing(8, after: name)
        stack.setCustomSpacing(26, after: detail)
        stack.edgeInsets = NSEdgeInsets(top: 44, left: 40, bottom: 32, right: 40)
        window.contentView = stack
        window.setContentSize(NSSize(width: 320, height: stack.fittingSize.height))
        window.center()
        comeForward()
        window.makeKeyAndOrderFront(nil)
    }

    private func button(_ title: String, _ action: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: action)
        b.controlSize = .large
        b.widthAnchor.constraint(equalToConstant: 220).isActive = true
        return b
    }

    @objc func openAbout() { NSWorkspace.shared.open(LAB.appendingPathComponent("about")) }
    @objc func checkMac() {
        window.close()
        onCheck?()
    }
}

// MARK: - This Mac's calendars (B23, octo/events.py)
// Read here, not in the lab: the lab runs as a login item under Homebrew's Python, which macOS can't easily ask for a
// privacy permission (as with Local Network, CLAUDE.md on the relay). This app asks for Calendars once, when the owner
// turns it on, and posts the window's events over its own key. Read only; nothing is written to any calendar.

final class MacCalendar {
    let store = EKEventStore()
    var asked = false
    var last = Date.distantPast

    /// Every minute while the owner wants it (the lab says so in /mac-app), and at once when the Settings page asks.
    @MainActor func read(force: Bool = false) async {
        if EKEventStore.authorizationStatus(for: .event) == .notDetermined, force || !asked {
            asked = true
            comeForward()
            _ = try? await store.requestFullAccessToEvents()
        }
        guard force || Date().timeIntervalSince(last) >= 300 else { return }
        last = Date()
        let status = EKEventStore.authorizationStatus(for: .event)
        var body: [String: Any] = ["access": status == .fullAccess ? "full" : status == .notDetermined ? "ask" : "denied"]
        if status == .fullAccess {
            let now = Date()
            let found = store.events(matching: store.predicateForEvents(withStart: now - 2 * 86400, end: now + 2 * 86400, calendars: nil))
            body["events"] = found.filter { !$0.isAllDay }.map { e -> [String: Any] in
                let people = (e.attendees ?? []) + (e.organizer.map { [$0] } ?? [])
                return ["id": e.calendarItemExternalIdentifier ?? e.eventIdentifier ?? "", "title": e.title ?? "",
                        "start": e.startDate.timeIntervalSince1970, "end": e.endDate.timeIntervalSince1970,
                        "invitees": people.filter { !$0.isCurrentUser && $0.participantStatus != .declined
                                                    && $0.participantType == .person }.map { p in
                            ["name": p.name ?? "",
                             "email": p.url.scheme == "mailto" ? (p.url.absoluteString.dropFirst(7).removingPercentEncoding ?? "") : ""]
                        }]
            }
        }
        if !(await Lab.post("api/octo/mac-app/calendar", body)) { last = .distantPast }  // the lab was down: next minute
    }
}

// MARK: - Export and import (octo/export.py, docs/export-plan.md)
// The same `octo export` / `octo import` the terminal runs, never the lab server (an import stops it; a macOS prompt
// held it for hours on 13 Oct), with the password on their stdin. The app gives them the Mac's own panels.

@MainActor extension App {
    static let exports = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Octo exports")

    @objc func exportLab() {
        Task { @MainActor in
            comeForward()
            guard let pw = Passwords.ask(new: true) else { return }
            try? FileManager.default.createDirectory(at: App.exports, withIntermediateDirectories: true)
            let save = NSSavePanel()
            save.directoryURL = App.exports
            let stamp = DateFormatter()
            stamp.dateFormat = "yyyy-MM-dd HHmm"
            save.nameFieldStringValue = "Octo export \(stamp.string(from: Date())).dmg"
            save.allowedContentTypes = [UTType(filenameExtension: "dmg") ?? .diskImage]
            save.message = "Octo exports isn't synced. Desktop and Documents may be, by iCloud."
            guard save.runModal() == .OK, let url = save.url else { return }
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            if ["Documents", "Desktop", "Library/Mobile Documents"].contains(where: { url.path.hasPrefix("\(home)/\($0)/") }) {
                let a = NSAlert()
                a.messageText = "This folder may be synced to iCloud"
                a.informativeText = "The export holds voiceprints, recordings and keys. If the folder syncs, they leave this Mac."
                a.addButton(withTitle: "Cancel")
                a.addButton(withTitle: "Put It There")
                guard a.runModal() == .alertSecondButtonReturn else { return }
            }
            if FileManager.default.fileExists(atPath: url.path) { try? FileManager.default.removeItem(at: url) }  // the panel asked
            let r = await working("Exporting your lab", "The lab stops for a few seconds while the copy is taken.") {
                await Lab.octo(["export", url.path, "--password-stdin", "--yes"], input: pw, line: $0)
            }
            guard let r, r.status == 0 else { return fail("The export didn't finish", r) }
            let done = NSAlert()
            done.messageText = "Your lab is exported"
            done.informativeText = r.text.range(of: "made and checked: ").map { "Made and checked: " + r.text[$0.upperBound...] } ?? r.text
            done.addButton(withTitle: "Show in Finder")
            done.addButton(withTitle: "OK")
            if done.runModal() == .alertFirstButtonReturn { NSWorkspace.shared.activateFileViewerSelecting([url]) }
            if lab.loaded { lab.web.reload() }  // the Settings page says when the last export was made
        }
    }

    @objc func importLab() {
        Task { @MainActor in
            comeForward()
            let open = NSOpenPanel()
            open.directoryURL = App.exports
            open.allowedContentTypes = [UTType(filenameExtension: "dmg") ?? .diskImage]
            open.message = "Choose an Octo export: a .dmg made by Export Lab… or octo export."
            open.prompt = "Choose"
            guard open.runModal() == .OK, let url = open.url else { return }
            guard let pw = Passwords.ask(new: false, file: url.lastPathComponent) else { return }
            let pre = await working("Opening the export", "Checking what's in it. Nothing changes yet.") {
                await Lab.octo(["import", url.path, "--preview", "--password-stdin"], input: pw, line: $0)
            }
            guard let pre, pre.status == 0, let last = pre.text.split(separator: "\n").last,
                  let info = try? JSONSerialization.jsonObject(with: Data(last.utf8)) as? [String: Any]
            else { return fail("The export couldn't be opened", pre) }
            let have = info["have"] as? Int ?? 0
            let ask = NSAlert()
            ask.messageText = have > 0 ? "Replace this lab with the export?" : "Bring this lab in?"
            ask.informativeText = (info["text"] as? String ?? "")
                + (have > 0 ? "\n\nThis lab's \(have) recordings are set aside for 14 days, not merged." : "")
                + "\n\nThe lab stops while it's swapped in, then starts again."
            ask.addButton(withTitle: have > 0 ? "Replace This Lab" : "Bring It In")
            ask.addButton(withTitle: "Cancel")
            ask.buttons[0].hasDestructiveAction = have > 0
            guard ask.runModal() == .alertFirstButtonReturn else { return }
            let r = await working("Bringing your lab in", "Copying and checking each file.") {
                await Lab.octo(["import", url.path, "--password-stdin", "--yes"] + (have > 0 ? ["--replace"] : []),
                               input: pw, line: $0)
            }
            guard let r, r.status == 0 else { return fail("The import didn't finish", r) }
            let done = NSAlert()
            done.messageText = "Your lab is in"
            done.informativeText = r.text.range(of: "brought in: ").map { "Brought in: " + r.text[$0.upperBound...] } ?? r.text
            done.runModal()
            refresh()
            if lab.loaded { lab.web.reload() }
        }
    }

    /// A small window while a command runs, showing the line it printed last. No Cancel: stopping an export or import
    /// halfway could leave the lab stopped.
    func working<T>(_ title: String, _ note: String,
                    _ run: (@escaping @MainActor @Sendable (String) -> Void) async -> T) async -> T {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 440, height: 120), styleMask: [.titled],
                            backing: .buffered, defer: false)
        panel.title = title
        let head = NSTextField(labelWithString: title)
        head.font = .boldSystemFont(ofSize: 13)
        let bar = NSProgressIndicator()
        bar.style = .bar
        bar.isIndeterminate = true
        bar.startAnimation(nil)
        let status = NSTextField(wrappingLabelWithString: note)
        status.textColor = .secondaryLabelColor
        status.preferredMaxLayoutWidth = 392
        status.lineBreakMode = .byTruncatingMiddle
        let stack = NSStackView(views: [head, bar, status])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 24, bottom: 20, right: 24)
        bar.widthAnchor.constraint(equalToConstant: 392).isActive = true
        panel.contentView = stack
        panel.level = .floating
        panel.center()
        panel.makeKeyAndOrderFront(nil)
        defer { panel.close() }
        return await run { line in status.stringValue = line }
    }

    func fail(_ title: String, _ r: (status: Int32, text: String)?) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = r.map { r in
            String(r.text.split(separator: "\n").last ?? "")
                .replacingOccurrences(of: #"^octo (export|import): "#, with: "", options: .regularExpression)
        } ?? "Octo's lab isn't installed for this account."
        a.runModal()
    }
}

/// The export's password: chosen twice (or suggested) for a new one, typed once to open one. Octo never keeps it.
@MainActor
enum Passwords {
    static let minimum = 14  // octo/export.py MIN_PASSWORD

    static func ask(new: Bool, file: String = "") -> String? {
        var note = new ? "Octo doesn't keep this password. Without it nobody can open the export, not even you: save it "
                         + "in your password manager." : "The password chosen when \(file) was made."
        while true {
            let a = NSAlert()
            a.messageText = new ? "Choose a password for the export" : "Password for the export"
            a.informativeText = note
            let one = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
            one.placeholderString = new ? "At least \(minimum) characters" : "Password"
            let two = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
            two.placeholderString = "Again"
            let shown = NSTextField(wrappingLabelWithString: "")
            shown.isSelectable = true
            shown.isHidden = true
            shown.preferredMaxLayoutWidth = 300
            let suggest = Suggest(fields: [one, two], shown: shown)
            let button = NSButton(title: "Suggest a Password", target: suggest, action: #selector(Suggest.fill))
            let views: [NSView] = new ? [one, two, button, shown] : [one]
            let stack = NSStackView(views: views)
            stack.orientation = .vertical
            stack.alignment = .leading
            stack.spacing = 8
            for f in [one, two] { f.widthAnchor.constraint(equalToConstant: 300).isActive = true }
            stack.frame = NSRect(x: 0, y: 0, width: 300, height: new ? 120 : 24)
            a.accessoryView = stack
            a.window.initialFirstResponder = one
            a.addButton(withTitle: new ? "Continue" : "Open")
            a.addButton(withTitle: "Cancel")
            guard a.runModal() == .alertFirstButtonReturn else { return nil }
            let pw = one.stringValue
            if !new { if pw.isEmpty { continue }; return pw }
            if pw.unicodeScalars.count < minimum {  // as Python's len() counts
                note = "That's \(pw.unicodeScalars.count) characters; it needs at least \(minimum). Or choose Suggest a Password."
            } else if two.stringValue != pw {
                note = "The two didn't match."
            } else {
                return pw
            }
        }
    }

    /// Five words from macOS's word list (octo/export.py passphrase), picked with the system's secure generator.
    static func passphrase() -> String {
        let words = ((try? String(contentsOfFile: "/usr/share/dict/words", encoding: .utf8)) ?? "").split(separator: "\n")
            .filter { (4...7).contains($0.count) && $0.allSatisfy { $0.isASCII && $0.isLowercase } }
        guard words.count > 1000 else { return UUID().uuidString }
        return (0..<5).map { _ in String(words.randomElement()!) }.joined(separator: "-")
    }
}

final class Suggest: NSObject {
    let fields: [NSSecureTextField]
    let shown: NSTextField
    init(fields: [NSSecureTextField], shown: NSTextField) {
        self.fields = fields
        self.shown = shown
    }
    @MainActor @objc func fill() {
        let p = Passwords.passphrase()
        fields.forEach { $0.stringValue = p }
        shown.stringValue = "\(p)\nSave it in your password manager now."
        shown.isHidden = false
    }
}

extension NSFont {
    var bold: NSFont { NSFontManager.shared.convert(self, toHaveTrait: .boldFontMask) }
}

// MARK: - Calls on this Mac (B19, docs/backlog.md; the spike: spikes/b19-calls/, docs/learnings.md "Calls on the Mac")
// "Record This Call" in the octopus menu, offered (never started) when a call app starts using the mic. The call
// app's sound by a Core Audio process tap and the mic beside it, in one private aggregate device on the mic's clock,
// into one 16 kHz 16-bit two-channel WAV: channel 1 the mic (the owner), channel 2 the call app (the others). On Stop,
// or when the app leaves the mic, the file goes to the lab like a memo (source "call"); the lab keeps both channels,
// and takes the others out of the mic when the call was on speakers (octo/calls.py). Nothing leaves the Mac.
// Process taps are macOS 14.4 and later; on an older Mac there's no call recording and the rest of the app is as it was.

enum CA {
    static let system = AudioObjectID(kAudioObjectSystemObject)
    static func addr(_ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal)
        -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }
    static func get<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ initial: T,
                       _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> T? {
        var a = addr(sel, scope), v = initial, size = UInt32(MemoryLayout<T>.size)
        return withUnsafeMutablePointer(to: &v) { AudioObjectGetPropertyData(obj, &a, 0, nil, &size, $0) } == noErr ? v : nil
    }
    static func string(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
        get(obj, sel, nil as Unmanaged<CFString>?).flatMap { $0?.takeRetainedValue() as String? }
    }
    static func ids(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> [AudioObjectID] {
        var a = addr(sel), size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(obj, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        return AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &ids) == noErr ? ids : []
    }

    /// Whether the Mac plays through speakers the mic can hear. Headphones only when it's clearly them (Bluetooth, or
    /// the headphone jack): the lab's echo canceller costs the owner's voice ~0.2 dB where there's no echo (learnings).
    static func onSpeakers() -> Bool {
        let out = get(system, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID(0)) ?? 0
        let transport = get(out, kAudioDevicePropertyTransportType, UInt32(0)) ?? 0
        if transport == kAudioDeviceTransportTypeBluetooth || transport == kAudioDeviceTransportTypeBluetoothLE { return false }
        if transport == kAudioDeviceTransportTypeBuiltIn {
            return get(out, kAudioDevicePropertyDataSource, UInt32(0), kAudioObjectPropertyScopeOutput) != 0x6864_706E  // 'hdpn'
        }
        return true
    }
}

/// Call apps, by the bundle id of the process that plays and hears their sound (the spike: Chrome's is its audio
/// helper, which also plays every other tab; Safari's is WebKit's GPU process; FaceTime's is avconferenced). Never
/// "anything on the mic": Siri's CoreSpeech always is.
let CALL_APPS: [(prefix: String, name: String)] = [
    ("us.zoom", "Zoom"), ("com.microsoft.teams", "Teams"), ("com.apple.avconferenced", "FaceTime"),
    ("com.apple.FaceTime", "FaceTime"), ("com.google.Chrome", "Chrome"), ("com.apple.WebKit.GPU", "Safari"),
    ("org.mozilla", "Firefox"), ("com.microsoft.edgemac", "Edge"), ("com.tinyspeck.slackmacgap", "Slack"),
    ("net.whatsapp", "WhatsApp"), ("com.hnc.Discord", "Discord"), ("com.cisco.webex", "Webex"),
]

struct CallError: Error { let text: String }

@available(macOS 14.4, *)
final class CallRecorder {
    let app: String
    let started = Date()
    let file: URL
    let speakersAtStart = CA.onSpeakers()
    private var tap = AudioObjectID(0), dev = AudioObjectID(0), ioProc: AudioDeviceIOProcID?, ext: ExtAudioFileRef?
    private let inter = UnsafeMutablePointer<Float>.allocate(capacity: 16384 * 2)
    private let mono = UnsafeMutablePointer<Float>.allocate(capacity: 16384)
    /// B21: the mic's samples for the live filler cue, set before start() when it's on, so the audio thread only reads it
    var micSamples: AsyncStream<[Float]>.Continuation?
    private(set) var rate: Float64 = 48000
    // Written on the audio thread, read on the main thread to tell the owner when a channel is silent
    var frames = 0, micPeak: Float = 0, appPeak: Float = 0

    init(app: String, processes: [AudioObjectID], file: URL) throws {
        self.app = app
        self.file = file
        let desc = CATapDescription(stereoMixdownOfProcesses: processes)
        desc.uuid = UUID()
        desc.isPrivate = true
        desc.muteBehavior = .unmuted  // the owner still hears the call
        var err = AudioHardwareCreateProcessTap(desc, &tap)
        guard err == noErr else { throw CallError(text: "macOS didn't let Octo hear \(app) (\(err)).") }
        guard let mic = CA.get(CA.system, kAudioHardwarePropertyDefaultInputDevice, AudioObjectID(0)), mic != 0,
              let micUID = CA.string(mic, kAudioDevicePropertyDeviceUID) else {
            close()
            throw CallError(text: "This Mac has no microphone to record from.")
        }
        // The mic as the clock and the tap drift-corrected to it: both arrive in one callback on one timeline
        let agg: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Octo call", kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: micUID, kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false, kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: micUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: desc.uuid.uuidString]],
        ]
        err = AudioHardwareCreateAggregateDevice(agg as CFDictionary, &dev)
        guard err == noErr else { close(); throw CallError(text: "Octo couldn't set up the recording (\(err)).") }
        let rate = CA.get(dev, kAudioDevicePropertyNominalSampleRate, Float64(0)) ?? 48000
        self.rate = rate
        // ExtAudioFile converts float at the device's rate to 16 kHz 16-bit itself
        var fileFmt = AudioStreamBasicDescription(mSampleRate: 16000, mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 4,
            mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 2, mBitsPerChannel: 16, mReserved: 0)
        var clientFmt = AudioStreamBasicDescription(mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kLinearPCMFormatFlagIsFloat | kLinearPCMFormatFlagIsPacked, mBytesPerPacket: 8,
            mFramesPerPacket: 1, mBytesPerFrame: 8, mChannelsPerFrame: 2, mBitsPerChannel: 32, mReserved: 0)
        err = ExtAudioFileCreateWithURL(file as CFURL, kAudioFileWAVEType, &fileFmt, nil, AudioFileFlags.eraseFile.rawValue, &ext)
        if err == noErr {
            err = ExtAudioFileSetProperty(ext!, kExtAudioFileProperty_ClientDataFormat,
                                          UInt32(MemoryLayout.size(ofValue: clientFmt)), &clientFmt)
        }
        guard err == noErr else { close(); throw CallError(text: "Octo couldn't write the recording (\(err)).") }
        ExtAudioFileWriteAsync(ext!, 0, nil)  // primes its buffers off the audio thread
        err = AudioDeviceCreateIOProcIDWithBlock(&ioProc, dev, nil) { [unowned self] _, input, _, _, _ in self.take(input) }
        guard err == noErr else { close(); throw CallError(text: "Octo couldn't set up the recording (\(err)).") }
    }

    /// May wait while macOS asks the owner about system audio (the spike: up to 90 s): call it off the main thread.
    func start() throws {
        let err = AudioDeviceStart(dev, ioProc)
        if err != noErr { close(); throw CallError(text: "The recording didn't start (\(err)).") }
    }

    /// The mic is the buffer with one channel and the tap the one with two (the spike, checked by ear and level);
    /// otherwise the mic's first and the tap's last. A buffer of several channels is averaged.
    private func take(_ input: UnsafePointer<AudioBufferList>) {
        let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        guard abl.count >= 2 else { return }
        let byCount = abl.first(where: { $0.mNumberChannels == 1 }).flatMap { m in abl.first(where: { $0.mNumberChannels == 2 }).map { (m, $0) } }
        let (micBuf, appBuf) = byCount ?? (abl[0], abl[abl.count - 1])
        let km = Int(max(1, micBuf.mNumberChannels)), ka = Int(max(1, appBuf.mNumberChannels))
        guard let m = micBuf.mData?.assumingMemoryBound(to: Float.self), let a = appBuf.mData?.assumingMemoryBound(to: Float.self)
        else { return }
        let n = min(Int(micBuf.mDataByteSize) / (4 * km), Int(appBuf.mDataByteSize) / (4 * ka), 16384)
        for i in 0..<n {
            var mv: Float = 0, av: Float = 0
            for c in 0..<km { mv += m[i * km + c] }
            for c in 0..<ka { av += a[i * ka + c] }
            mv /= Float(km)
            av /= Float(ka)
            inter[2 * i] = mv
            mono[i] = mv
            inter[2 * i + 1] = av
            micPeak = max(micPeak, abs(mv))
            appPeak = max(appPeak, abs(av))
        }
        var w = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer(mNumberChannels: 2, mDataByteSize: UInt32(n * 8), mData: inter))
        ExtAudioFileWriteAsync(ext!, UInt32(n), &w)
        frames += n
        micSamples?.yield(Array(UnsafeBufferPointer(start: mono, count: n)))  // shortcut: an array a callback; fine at ~100 a second
    }

    /// Stops and closes the file; whether the speakers were in use at either end of the call.
    func stop() -> Bool {
        if let ioProc { AudioDeviceStop(dev, ioProc) }
        close()
        return speakersAtStart || CA.onSpeakers()
    }

    private func close() {
        if let ioProc { AudioDeviceDestroyIOProcID(dev, ioProc) }
        ioProc = nil
        if let ext { ExtAudioFileDispose(ext) }
        ext = nil
        if dev != 0 { AudioHardwareDestroyAggregateDevice(dev) }
        dev = 0
        if tap != 0 { AudioHardwareDestroyProcessTap(tap) }
        tap = 0
    }

    /// Asks for the system-audio permission once (Setup): a moment's tap of everything, recording nothing. macOS has no
    /// way to ask whether it was given; a refused tap is only silence.
    static func askForSystemAudio() {
        let desc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        desc.uuid = UUID()
        desc.isPrivate = true
        var tap = AudioObjectID(0), dev = AudioObjectID(0)
        guard AudioHardwareCreateProcessTap(desc, &tap) == noErr else { return }
        defer { AudioHardwareDestroyProcessTap(tap) }
        let out = CA.get(CA.system, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID(0)) ?? 0
        guard let outUID = CA.string(out, kAudioDevicePropertyDeviceUID) else { return }
        let agg: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Octo permission", kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outUID, kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true, kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: desc.uuid.uuidString]],
        ]
        guard AudioHardwareCreateAggregateDevice(agg as CFDictionary, &dev) == noErr else { return }
        defer { AudioHardwareDestroyAggregateDevice(dev) }
        var proc: AudioDeviceIOProcID?
        guard AudioDeviceCreateIOProcIDWithBlock(&proc, dev, nil, { _, _, _, _, _ in }) == noErr, let proc else { return }
        defer { AudioDeviceDestroyIOProcID(dev, proc) }
        if AudioDeviceStart(dev, proc) == noErr {  // the prompt shows here, and this waits for the owner's answer
            Thread.sleep(forTimeInterval: 0.5)
            AudioDeviceStop(dev, proc)
        }
    }
}

@available(macOS 14.4, *)
final class Calls: NSObject {
    var post: (String, [String: String]) -> Void = { _, _ in }
    var changed: () -> Void = {}
    private(set) var recorder: CallRecorder?
    private var problem: String?  // said in the menu while recording: a channel that's silent
    var shown: (String) -> Void = { _ in }  // B21: the menu bar's text beside the octopus
    private var cue: AnyObject?  // FillerCue (macOS 26)
    private var cues = 0, cueNote: String?
    private var offered = Set<String>()
    private var watched = Set<AudioObjectID>()
    private var onMicNow = Set<String>()
    let folder = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("online.octolab.Octo/Calls")  // not "Octo": the lab's own folder is "octo"

    /// Listens on the main queue (the spike: a listener on a thread that isn't running its run loop hears nothing):
    /// the process list, and each process's input devices, which notify ~40 ms after the mic starts or stops (the
    /// "is running input" property never notified on macOS 27). Needs no permission.
    func watch() {
        var list = CA.addr(kAudioHardwarePropertyProcessObjectList)
        AudioObjectAddPropertyListenerBlock(CA.system, &list, .main) { [weak self] _, _ in self?.look() }
        look()
        sendLeftovers()
    }

    /// Every audio process of each call app now on the mic, by the app's name.
    func callsOnMic() -> [String: [AudioObjectID]] {
        var procs: [String: [AudioObjectID]] = [:], onMic = Set<String>()
        for id in CA.ids(CA.system, kAudioHardwarePropertyProcessObjectList) {
            let bundle = CA.string(id, kAudioProcessPropertyBundleID) ?? ""
            guard let name = CALL_APPS.first(where: { bundle.hasPrefix($0.prefix) })?.name else { continue }
            procs[name, default: []].append(id)
            if (CA.get(id, kAudioProcessPropertyIsRunningInput, UInt32(0)) ?? 0) != 0 { onMic.insert(name) }
        }
        return procs.filter { onMic.contains($0.key) }
    }

    private func look() {
        for id in CA.ids(CA.system, kAudioHardwarePropertyProcessObjectList) where !watched.contains(id) {
            watched.insert(id)
            var a = CA.addr(kAudioProcessPropertyDevices, kAudioObjectPropertyScopeInput)
            AudioObjectAddPropertyListenerBlock(id, &a, .main) { [weak self] _, _ in self?.look() }
        }
        let now = Set(callsOnMic().keys)
        for name in now.subtracting(offered) {
            offered.insert(name)  // once a call: stopping a recording mid-call doesn't bring the offer back
            if recorder != nil { continue }
            post("\(name) is using the mic. Record this call? Click to start, and tell the others it's recorded.", ["call": name])
        }
        for name in onMicNow.subtracting(now) {
            // Off the mic for 5 s, not a blip (Chrome went off and on in 0.13 s in the spike): the call is over
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
                guard let self, !Set(self.callsOnMic().keys).contains(name) else { return }
                self.offered.remove(name)
                if self.recorder?.app == name { self.stop() }
            }
        }
        onMicNow = now
        changed()
    }

    func menuItems() -> [NSMenuItem] {
        if let r = recorder {
            let mins = Int(Date().timeIntervalSince(r.started) / 60)
            var items = [NSMenuItem(title: "Recording the \(r.app) call · \(mins) min", action: nil, keyEquivalent: ""),
                         NSMenuItem(title: problem ?? "Tell the others it's recorded", action: nil, keyEquivalent: "")]
            if let note = cueNote ?? (cue == nil ? nil : "Your fillers this call: \(cues)") {
                items.append(NSMenuItem(title: note, action: nil, keyEquivalent: ""))
            }
            let stop = NSMenuItem(title: "Stop Recording the Call", action: #selector(stopFromMenu), keyEquivalent: "")
            stop.target = self
            items.append(stop)
            return items
        }
        let app = callsOnMic().keys.sorted().first
        let item = NSMenuItem(title: app.map { "Record This Call (\($0))" } ?? "Record This Call",
                              action: app == nil ? nil : #selector(recordFromMenu(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = app
        if app == nil { item.toolTip = "Offered when Zoom, Teams, FaceTime or a browser call is using the mic" }
        return [item]
    }

    @objc func recordFromMenu(_ sender: NSMenuItem) { if let app = sender.representedObject as? String { start(app) } }
    @objc func stopFromMenu() { stop() }

    /// Only ever because the owner asked: the menu, or a click on the offer.
    func start(_ app: String) {
        guard recorder == nil else { return }
        Task { @MainActor in
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                _ = await AVCaptureDevice.requestAccess(for: .audio)
            }
            guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
                return Calls.refused("Octo can't hear your microphone",
                    "Allow Octo in System Settings → Privacy & Security → Microphone, then record the call again.")
            }
            guard let procs = callsOnMic()[app] else { return Calls.say("\(app) isn't on a call any more", "") }
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let ms = Int(Date().timeIntervalSince1970 * 1000)
            do {
                let r = try CallRecorder(app: app, processes: procs, file: folder.appendingPathComponent("\(ms)-\(app)-recording.wav"))
                recorder = r
                problem = nil
                let live = await Lab.get("api/octo/fillers/live", timeout: 2)  // the lab is on this Mac: quick, or not there
                var mic: AsyncStream<[Float]>?
                if live?["mac"] as? Bool == true {
                    let (stream, cont) = AsyncStream<[Float]>.makeStream(bufferingPolicy: .bufferingNewest(500))  // ~5 s
                    r.micSamples = cont
                    mic = stream
                }
                changed()
                try await Task.detached { try r.start() }.value
                if let mic { listen(r, mic, phrases: live?["phrases"] as? [String] ?? []) }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                check(r)
            } catch {
                recorder = nil
                changed()
                Calls.say("Octo couldn't record the call", (error as? CallError)?.text ?? "\(error)")
            }
        }
    }

    /// Both channels should carry sound in the first seconds. A refused system-audio permission is only silence.
    private func check(_ r: CallRecorder) {
        guard recorder === r else { return }
        if r.frames == 0 {
            problem = "Nothing is being recorded"
            post("Octo isn't recording the call: nothing has arrived. Stop and record it again.", [:])
        } else if r.micPeak == 0 {
            problem = "Nothing heard from your mic"
            post("Octo hears nothing from your mic. Check System Settings → Privacy & Security → Microphone.", [:])
        } else if r.appPeak == 0 {
            problem = "Nothing heard from \(r.app) yet"
            post("Octo hears nothing from \(r.app) yet. If you didn't allow System Audio Recording, allow Octo in System "
                 + "Settings → Privacy & Security → Screen & System Audio Recording, then record again.", [:])
        }
        changed()
    }

    /// B21: the live filler cue on the owner's mic channel, never the call's. The recording doesn't wait for it.
    private func listen(_ r: CallRecorder, _ mic: AsyncStream<[Float]>, phrases: [String]) {
        cues = 0
        #if compiler(>=6.2)
        guard #available(macOS 26, *) else { cueNote = "The filler cue needs macOS 26"; return changed() }
        Task { @MainActor [self] in
            do {
                let c = try await FillerCue.start(phrases: phrases) { [weak self] p, late in self?.cued(p, late: late) }
                guard recorder === r else { return c.stop() }
                cue = c
                changed()
                guard let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: r.rate, channels: 1, interleaved: false)
                else { return }
                Task.detached {
                    for await xs in mic {
                        guard let b = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(xs.count)) else { continue }
                        b.frameLength = AVAudioFrameCount(xs.count)
                        xs.withUnsafeBufferPointer { b.floatChannelData![0].update(from: $0.baseAddress!, count: xs.count) }
                        c.feed(b)
                    }
                    c.stop()
                }
            } catch {
                r.micSamples?.finish()
                cueNote = (error as? FillerCue.Refused)?.text ?? "The filler cue didn't start: \(error.localizedDescription)"
                changed()
            }
        }
        #else
        cueNote = "The filler cue needs macOS 26"
        changed()
        #endif
    }

    /// The filler for 1.5 s, then the count for the call, as the recorder's screen (B20).
    private func cued(_ phrase: String, late: Bool) {
        guard recorder != nil else { return }
        cues += 1
        let n = cues
        shown(late ? "\(n)" : phrase)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self, self.recorder != nil, self.cues == n else { return }
            self.shown("\(n)")
        }
    }

    func stop() {
        guard let r = recorder else { return }
        recorder = nil
        r.micSamples?.finish()  // and the cue with it
        cue = nil
        cueNote = nil
        shown("")
        let speakers = r.stop()
        let done = r.file.deletingLastPathComponent().appendingPathComponent(
            r.file.lastPathComponent.replacingOccurrences(of: "-recording.wav", with: speakers ? "-speakers.wav" : "-headphones.wav"))
        try? FileManager.default.moveItem(at: r.file, to: done)
        changed()
        Task { @MainActor in await send(done) }
    }

    /// The file to the lab like a memo, then deleted here. Kept when the lab can't take it, and tried again when the
    /// app next starts. Its name says when it started, which app and where it was heard: "<ms>-<app>-<speakers|headphones>.wav".
    @MainActor func send(_ file: URL) async {
        let parts = file.deletingPathExtension().lastPathComponent.split(separator: "-")
        guard parts.count >= 3, let ms = Int(parts[0]) else { return }
        let output = parts.last == "headphones" ? "headphones" : "speakers"  // unsure (a crash mid-call): cancel the echo
        let app = parts[1]
        let mins = max(1, Int(((try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) / 64000 / 60))
        if let why = await Lab.upload(file, query: [
            URLQueryItem(name: "name", value: "\(app) call.wav"), URLQueryItem(name: "source", value: "call"),
            URLQueryItem(name: "output", value: output), URLQueryItem(name: "modified", value: String(ms)),
        ]) {
            post("Octo couldn't add the \(app) call to the lab: \(why). It's kept on this Mac and tried again when Octo next starts.", [:])
        } else {
            try? FileManager.default.removeItem(at: file)
            post("The \(app) call (\(mins) min) is in the lab.", [:])
        }
    }

    /// Calls not handed over yet: the lab was down, or Octo quit mid-call (stop() runs when it quits).
    func sendLeftovers() {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        Task { @MainActor in
            for f in files where f.pathExtension == "wav" { await send(f) }
        }
    }

    /// Setup's "Allow call recording": the mic, then system audio, each asked once by macOS.
    func askPermissions() {
        Task { @MainActor in
            let mic = AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined
                ? await AVCaptureDevice.requestAccess(for: .audio) : AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
            await Task.detached { CallRecorder.askForSystemAudio() }.value
            Calls.say(mic ? "Octo can record calls" : "Octo can't hear your microphone",
                      (mic ? "Microphone: allowed. " : "Allow Octo in System Settings → Privacy & Security → Microphone. ")
                      + "System audio: macOS asks once, and Octo can't see the answer. If you said no, allow Octo in System "
                      + "Settings → Privacy & Security → Screen & System Audio Recording, under System Audio Recording Only.\n\n"
                      + "Tell the others on every call you record that it's recorded.")
        }
    }

    static func say(_ title: String, _ text: String) {
        comeForward()
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }

    static func refused(_ title: String, _ text: String) {
        comeForward()
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.addButton(withTitle: "Open System Settings")
        a.addButton(withTitle: "Not Now")
        if a.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!)
        }
    }
}

@main enum Main {  // not top-level code: that's only allowed in a single-file build, and FillerCue.swift (B21) is a second
    static func main() {
        // `octo uninstall` runs the app with this before deleting it: only the app can undo its own Open at Login.
        if CommandLine.arguments.contains("--forget-login") {
            if SMAppService.mainApp.status == .enabled { try? SMAppService.mainApp.unregister() }
            exit(0)
        }
        let app = NSApplication.shared
        let delegate = App()
        app.delegate = delegate
        app.run()
    }
}
