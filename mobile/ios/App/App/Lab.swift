// The lab inside the app (docs/phone-plan.md §4): the lab's own pages, loaded from the owner's Mac, in a web
// view that trusts only that Mac's certificate authority. The authority is pinned by its SHA-256, which arrives
// in the QR code the Mac shows; the Mac sends the authority in every handshake (octo/phone.py), so nothing is
// installed on the phone and no other site is affected. The device token from pairing is kept in the Keychain
// (this device only) and given to the web view as the lab's cookie for each of the Mac's addresses.
//
// JS: `registerPlugin('Lab')` (mobile/src/lab.ts). Registered by OctoViewController (below), which
// SceneDelegate uses instead of Capacitor's own view controller.
import AVFoundation
import BackgroundTasks
import Capacitor
import CryptoKit
import UIKit
import UserNotifications
import WebKit

class OctoViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(LabPlugin())
        bridge?.registerPluginInstance(MemosPlugin())  // Memos.swift
    }
}

// MARK: - Trust: only the pinned authority, with the system's own checks (name, dates, constraints)

enum Pin {
    static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Accepts a server only if its chain holds the authority with this SHA-256 and the server's certificate
    /// verifies against that authority alone (signature, host name, dates, the authority's name constraints).
    static func accept(_ trust: SecTrust, ca: String) -> Bool {
        guard !ca.isEmpty, let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let anchor = chain.first(where: { sha256(SecCertificateCopyData($0) as Data) == ca.lowercased() })
        else { return false }
        SecTrustSetAnchorCertificates(trust, [anchor] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, true)
        var error: CFError?
        return SecTrustEvaluateWithError(trust, &error)
    }

    static func handle(_ challenge: URLAuthenticationChallenge, ca: String,
                       _ done: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            done(.performDefaultHandling, nil)
            return
        }
        let ok = accept(trust, ca: ca)
        if !ok { print("[octo] pin: \(challenge.protectionSpace.host) refused: not this Mac's authority") }
        if ok { done(.useCredential, URLCredential(trust: trust)) } else { done(.cancelAuthenticationChallenge, nil) }
    }
}

final class PinnedSession: NSObject, URLSessionDelegate {
    let ca: String
    lazy var session: URLSession = {
        let conf = URLSessionConfiguration.ephemeral
        conf.httpShouldSetCookies = false
        conf.waitsForConnectivity = false
        return URLSession(configuration: conf, delegate: self, delegateQueue: nil)
    }()

    init(ca: String) { self.ca = ca }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pin.handle(challenge, ca: ca, completionHandler)
    }

    /// One request: (status, body), or nil when the Mac didn't answer (or wasn't this Mac).
    func send(_ host: String, _ port: Int, _ path: String, token: String? = nil, json: [String: Any]? = nil,
              timeout: TimeInterval) async -> (Int, Data)? {
        let h = host.contains(":") ? "[\(host)]" : host
        guard let url = URL(string: "https://\(h):\(port)\(path)") else { return nil }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        if let token { req.setValue("octo_device=\(token)", forHTTPHeaderField: "Cookie") }
        if let json {
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: json)
        }
        let t0 = Date()
        do {
            let (data, resp) = try await session.data(for: req)
            return ((resp as? HTTPURLResponse)?.statusCode ?? 0, data)
        } catch {
            if (error as? URLError)?.code != .cancelled {  // the others, once one answered
                let ns = error as NSError
                let stream = ns.userInfo["_kCFStreamErrorCodeKey"] ?? "-"
                let under = (ns.userInfo[NSUnderlyingErrorKey] as? NSError).map { "\($0.domain) \($0.code)" } ?? "-"
                print("[octo] lab: \(host) \(path): \(error.localizedDescription) after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s (\(ns.code); stream \(stream); under \(under))")
            }
            return nil
        }
    }

    /// The same request to every address at once; the first answer of `ok` wins. Otherwise the answers seen.
    func first(_ hosts: [String], _ port: Int, _ path: String, token: String? = nil, json: [String: Any]? = nil,
               timeout: TimeInterval, ok: Int = 200) async -> (host: String?, status: Int, body: Data?, seen: [Int]) {
        await withTaskGroup(of: (String, (Int, Data)?).self) { group in
            for host in hosts {
                group.addTask { (host, await self.send(host, port, path, token: token, json: json, timeout: timeout)) }
            }
            var seen: [Int] = []
            for await (host, answer) in group {
                guard let (status, data) = answer else { continue }
                seen.append(status)
                if status == ok {
                    group.cancelAll()
                    return (host, status, data, seen)
                }
            }
            return (nil, seen.first ?? 0, nil, seen)
        }
    }
}

// MARK: - The Keychain: the device token, on this device only (not in backups that leave it)

enum Keychain {
    static let service = "app.octo.lab"

    static func save(_ token: String) {
        clear()
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: "token", kSecValueData as String: Data(token.utf8),
                                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        SecItemAdd(q as CFDictionary, nil)
    }

    static func load() -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: "token", kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear() {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service] as CFDictionary)
    }
}

// MARK: - The lab's pages

final class LabWeb: NSObject, WKNavigationDelegate, WKUIDelegate {
    let web: WKWebView
    var ca = ""
    var hosts: [String] = []
    weak var plugin: LabPlugin?
    private let refresh = UIRefreshControl()

    override init() {
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .default()
        conf.allowsInlineMediaPlayback = true
        conf.applicationNameForUserAgent = "OctoApp/1"
        web = WKWebView(frame: .zero, configuration: conf)
        super.init()
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.contentInsetAdjustmentBehavior = .never
        refresh.addTarget(self, action: #selector(pulled), for: .valueChanged)
        web.scrollView.refreshControl = refresh
    }

    @objc private func pulled() { web.reload() }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pin.handle(challenge, ca: ca, completionHandler)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(LabWeb.policy(action, hosts: hosts))
    }

    /// The lab's own pages stay in the app; anything else (a link out) opens in Safari.
    static func policy(_ action: WKNavigationAction, hosts: [String]) -> WKNavigationActionPolicy {
        if let url = action.request.url, let host = url.host, !hosts.contains(host), url.scheme == "https" || url.scheme == "http" {
            UIApplication.shared.open(url)
            return .cancel
        }
        return .allow
    }

    // target=_blank (About Octo, the case page): a sheet over the lab with Done, so the lab stays where it was.
    // Loaded in this view, as before 9 Oct, the page replaced the lab and only a swipe from the edge came back.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard action.targetFrame == nil, let url = action.request.url else { return nil }
        guard let host = url.host, hosts.contains(host), let vc = plugin?.bridge?.viewController else {
            if LabWeb.policy(action, hosts: hosts) == .allow { webView.load(action.request) }
            return nil
        }
        let sheet = LabSheet(configuration: configuration, ca: ca, hosts: hosts)
        vc.present(UINavigationController(rootViewController: sheet), animated: true)
        return sheet.web  // WebKit loads the page into it
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        refresh.endRefreshing()
        plugin?.notifyListeners("loaded", data: ["url": webView.url?.absoluteString ?? ""])
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        refresh.endRefreshing()
        let e = error as NSError
        if e.code == NSURLErrorCancelled { return }
        plugin?.notifyListeners("failed", data: ["message": e.localizedDescription, "code": e.code])
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        refresh.endRefreshing()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    func setCookies(token: String, then: @escaping () -> Void) {
        let store = web.configuration.websiteDataStore.httpCookieStore
        let group = DispatchGroup()
        for host in hosts {
            let props: [HTTPCookiePropertyKey: Any] = [
                .domain: host, .path: "/", .name: "octo_device", .value: token, .secure: "TRUE",
                .expires: Date().addingTimeInterval(365 * 86400), HTTPCookiePropertyKey("HttpOnly"): "TRUE",
            ]
            if let cookie = HTTPCookie(properties: props) {
                group.enter()
                store.setCookie(cookie) { group.leave() }
            }
        }
        group.notify(queue: .main, execute: then)
    }

    func clearCookies(then: @escaping () -> Void) {
        let store = web.configuration.websiteDataStore.httpCookieStore
        store.getAllCookies { cookies in
            let group = DispatchGroup()
            for c in cookies where c.name == "octo_device" {
                group.enter()
                store.delete(c) { group.leave() }
            }
            group.notify(queue: .main, execute: then)
        }
    }
}

/// A page the lab opens in a new tab, in a sheet: Done (or a swipe down) goes back to the lab. Its web view is made
/// from the configuration WebKit hands over, so it has the lab's cookie; it trusts the same pinned authority.
final class LabSheet: UIViewController, WKNavigationDelegate, WKUIDelegate {
    let web: WKWebView
    private let ca: String
    private let hosts: [String]
    private var titleSeen: NSKeyValueObservation?

    init(configuration: WKWebViewConfiguration, ca: String, hosts: [String]) {
        web = WKWebView(frame: .zero, configuration: configuration)
        self.ca = ca
        self.hosts = hosts
        super.init(nibName: nil, bundle: nil)
        web.navigationDelegate = self
        web.uiDelegate = self
    }

    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        navigationItem.rightBarButtonItem = UIBarButtonItem(systemItem: .done, primaryAction: UIAction { [weak self] _ in
            self?.dismiss(animated: true)
        })
        titleSeen = web.observe(\.title, options: [.initial, .new]) { [weak self] web, _ in
            self?.navigationItem.title = web.title
        }
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            web.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
    }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pin.handle(challenge, ca: ca, completionHandler)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(LabWeb.policy(action, hosts: hosts))
    }

    // Another new-tab link from inside the sheet: here, not a sheet on a sheet.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.targetFrame == nil, LabWeb.policy(action, hosts: hosts) == .allow { webView.load(action.request) }
        return nil
    }

    func webViewDidClose(_ webView: WKWebView) { dismiss(animated: true) }
}

// MARK: - The QR code, with AVFoundation

/// The camera and a line saying what it sees: where to find the code, that a code isn't Octo's, or after 6 s that
/// nothing has been seen (and that the link can be pasted instead). On 8 Oct the camera came up and nothing happened,
/// with VisionKit's scanner and then this one: the Mac's code was cropped to its top-left corner (fixed in
/// octo/phone_api.py), and the scanner said nothing about it. AVFoundation's QR reader, on the phone's virtual
/// camera as the Camera app uses it, so iOS switches to the ultra-wide (macro) lens when the code is close.
final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    struct Problem: LocalizedError { let errorDescription: String? }

    var done: ((Result<String, Error>) -> Void)?
    private let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "app.octo.scan")
    private let hint = UILabel()
    private let hintBack = UIVisualEffectView(effect: UIBlurEffect(style: .systemThinMaterialDark))
    private var preview: AVCaptureVideoPreviewLayer?
    private var other: String?
    private var seenAny = false

    static func camera() -> AVCaptureDevice? {
        let types: [AVCaptureDevice.DeviceType] = [.builtInTripleCamera, .builtInDualWideCamera, .builtInDualCamera,
                                                   .builtInWideAngleCamera]
        return AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .video, position: .back).devices.first
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        title = "Scan the code on your Mac"
        navigationItem.leftBarButtonItem = UIBarButtonItem(systemItem: .cancel, primaryAction: UIAction { [weak self] _ in
            self?.finish(.failure(Problem(errorDescription: "cancelled")))
        })
        hint.text = "On your Mac: Octo → Phone → Show the code. Hold the phone a hand's width from the screen."
        hint.numberOfLines = 0
        hint.textAlignment = .center
        hint.textColor = .white
        hint.font = .preferredFont(forTextStyle: .body)
        hint.adjustsFontForContentSizeCategory = true
        hintBack.layer.cornerRadius = 14
        hintBack.clipsToBounds = true
        hintBack.contentView.addSubview(hint)
        view.addSubview(hintBack)

        guard let device = Self.camera(), let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else {
            print("[octo] scan: no camera")
            DispatchQueue.main.async { self.finish(.failure(Problem(errorDescription: "unsupported"))) }
            return
        }
        session.beginConfiguration()
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else {
            session.commitConfiguration()
            print("[octo] scan: can't read codes")
            DispatchQueue.main.async { self.finish(.failure(Problem(errorDescription: "unsupported"))) }
            return
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]  // only once the output is in the session: before, .qr isn't offered
        session.commitConfiguration()
        do {
            try device.lockForConfiguration()
            // A virtual device starts at its widest lens; start at the main one ("1x") and let iOS switch for macro.
            if let main = device.virtualDeviceSwitchOverVideoZoomFactors.first {
                device.videoZoomFactor = CGFloat(truncating: main)
            }
            if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
            device.unlockForConfiguration()
        } catch {
            print("[octo] scan: camera settings: \(error)")
        }
        print("[octo] scan: camera \(device.localizedName), types \(output.metadataObjectTypes)")
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.insertSublayer(layer, at: 0)
        preview = layer
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
        let guide = view.safeAreaLayoutGuide.layoutFrame
        let width = guide.width - 32
        let size = hint.sizeThatFits(CGSize(width: width - 32, height: .greatestFiniteMagnitude))
        hintBack.frame = CGRect(x: guide.minX + 16, y: guide.maxY - size.height - 40, width: width, height: size.height + 24)
        hint.frame = hintBack.bounds.insetBy(dx: 16, dy: 12)
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        queue.async { if !self.session.isRunning { self.session.startRunning() } }
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            guard let self, self.done != nil, !self.seenAny else { return }
            print("[octo] scan: nothing seen in 6 s")
            self.hint.text = "No code seen yet. Hold the phone 20 to 30 cm from the screen, the whole code in view. " +
                "Or go back and paste the link instead."
            self.view.setNeedsLayout()
        }
    }

    override func viewDidDisappear(_ animated: Bool) {
        super.viewDidDisappear(animated)
        queue.async { if self.session.isRunning { self.session.stopRunning() } }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        for case let code as AVMetadataMachineReadableCodeObject in objects {
            if let text = code.stringValue { saw(text) }
        }
    }

    private func saw(_ text: String) {
        guard done != nil else { return }
        seenAny = true
        if text.hasPrefix("octo://pair") {
            print("[octo] scan: an Octo code")
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            finish(.success(text))
            return
        }
        if text != other {
            other = text
            print("[octo] scan: another code (\(text.prefix(12))…)")
            hint.text = "That code isn't from Octo. On your Mac: Octo → Phone → Show the code."
            view.setNeedsLayout()
        }
    }

    private func finish(_ result: Result<String, Error>) {
        guard let done else { return }
        self.done = nil
        queue.async { self.session.stopRunning() }
        let callback = { done(result) }
        if let nav = navigationController, nav.presentingViewController != nil {
            nav.dismiss(animated: true, completion: callback)
        } else {
            callback()
        }
    }
}

// MARK: - "A review is ready": the phone's own notifications (8 Oct; no server of ours)

/// Counts only, from the Mac's own list of what's waiting (`octo/notify.py` keys, in the lab's hello). iOS runs
/// the check when it chooses (Background App Refresh: a few times a day, more often for an app used often), over
/// the same pinned connection as the Lab tab. Each time the app reaches the Mac, what's waiting counts as seen,
/// so only what's new since is told.
enum LabRefresh {
    static let task = "app.octo.refresh"
    private static let reachKey = "octo.lab.reach"  // where the Mac answered last: hosts, port, ca
    private static let seenKey = "octo.lab.seen"    // the keys already seen or told
    private static let wantKey = "octo.notify"      // the owner's choice: true (allowed), false (not now)

    /// At launch, before it ends (iOS's rule for background tasks).
    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: task, using: nil) { t in
            guard let t = t as? BGAppRefreshTask else { return }
            schedule()
            let work = Task { await check(); t.setTaskCompleted(success: true) }
            t.expirationHandler = { work.cancel() }
        }
    }

    static func schedule() {
        guard UserDefaults.standard.bool(forKey: wantKey) else { return }
        let r = BGAppRefreshTaskRequest(identifier: task)
        r.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        do { try BGTaskScheduler.shared.submit(r) } catch { print("[octo] refresh: \(error)") }
    }

    /// The app reached the Mac: remember where, and that what's waiting has been seen.
    static func saw(hosts: [String], port: Int, ca: String, waiting: [String]?) {
        let d = UserDefaults.standard
        d.set(["hosts": hosts, "port": port, "ca": ca], forKey: reachKey)
        if let waiting { d.set(waiting, forKey: seenKey) }
        schedule()
    }

    static func forget() {
        for k in [reachKey, seenKey] { UserDefaults.standard.removeObject(forKey: k) }
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: task)
    }

    /// "ask" (not asked yet), "on", "off" (not now), "denied" (refused in Settings); and whether iOS lets the app
    /// check in the background at all (Settings → General → Background App Refresh).
    static func state() async -> [String: Any] {
        let auth = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        let want = UserDefaults.standard.object(forKey: wantKey) as? Bool
        let state: String
        switch auth {
        case .denied: state = "denied"
        case .notDetermined: state = want == false ? "off" : "ask"
        default: state = want == false ? "off" : "on"
        }
        let refresh = await MainActor.run { UIApplication.shared.backgroundRefreshStatus == .available }
        return ["state": state, "refresh": refresh]
    }

    static func allow() async -> [String: Any] {
        let granted = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
        if granted {
            UserDefaults.standard.set(true, forKey: wantKey)
            schedule()
        }
        return await state()
    }

    static func notNow() {
        UserDefaults.standard.set(false, forKey: wantKey)
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: task)
    }

    /// Ask the Mac what's waiting and tell what's new, in counts.
    static func check() async {
        let d = UserDefaults.standard
        guard d.bool(forKey: wantKey), let cfg = d.dictionary(forKey: reachKey), let ca = cfg["ca"] as? String,
              let hosts = cfg["hosts"] as? [String], let port = cfg["port"] as? Int, let token = Keychain.load() else { return }
        let auth = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        guard auth == .authorized || auth == .provisional else { return }
        let r = await PinnedSession(ca: ca).first(hosts, port, "/api/octo/phone/hello", token: token, timeout: 10)
        guard let body = r.body, let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let waiting = json["waiting"] as? [String] else {
            print("[octo] refresh: no answer from the Mac (\(r.status))")
            return
        }
        let seen = Set(d.stringArray(forKey: seenKey) ?? [])
        d.set(waiting, forKey: seenKey)  // only what's still waiting is kept: waiting again later is told again
        guard let text = message(Set(waiting).subtracting(seen)) else { return }
        let content = UNMutableNotificationContent()
        content.title = "Octo"
        content.body = text
        content.sound = .default
        content.threadIdentifier = "octo.lab"
        try? await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content,
                                                                               trigger: nil))
    }

    /// Counts only, in the Mac's own words (`octo/notify.py` message).
    static func message(_ new: Set<String>, now: Date = Date()) -> String? {
        func count(_ kind: String) -> Int { new.filter { $0.hasPrefix(kind + ":") }.count }
        var parts: [String] = count("week") > 0 ? ["Your week is ready"] : []
        let review = count("review"), confirm = count("confirm"), failed = count("failed")
        if review > 0 { parts.append("\(review) conversation\(review > 1 ? "s" : "") ready to review") }
        if confirm > 0 { parts.append("\(confirm) with speakers to confirm") }
        if failed > 0 { parts.append("\(failed) failed: Try again on your Mac") }
        if count("coach") > 0 { parts.append("the coach's note is ready") }
        if let last = new.compactMap({ $0.hasPrefix("quiet:") ? Double($0.dropFirst(6)) : nil }).max() {
            parts.append("recorder not seen for \(Int((now.timeIntervalSince1970 - last) / 3600)) h")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// A tap on one of those opens the Lab tab. The delegate is set at launch (AppDelegate), before the plugin
/// exists, so a tap that starts the app isn't lost; no banner while the app is open (it's on the screen).
final class LabNotifications: NSObject, UNUserNotificationCenterDelegate {
    static let shared = LabNotifications()
    private var pending = false
    weak var plugin: LabPlugin? {
        didSet {
            if pending, let plugin {
                pending = false
                plugin.opened()
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        DispatchQueue.main.async {
            if let plugin = self.plugin { plugin.opened() } else { self.pending = true }
            done()
        }
    }
}

// App Transport Security (Info.plist, NSAllowsArbitraryLoads): iOS exempts only the home network's addresses from its
// own rules, and Tailscale's 100.64.0.0/10 isn't one, so it refused the Mac's own authority there with a fatal alert
// (-9802) after this app had accepted it (8 Oct). Every connection this app makes is to the paired Mac and passes
// Pin.handle (PinnedSession and LabWeb's challenge), which accepts only the authority pinned from its QR code; the
// app loads nothing else. (NSAllowsLocalNetworking had to go: with it, iOS ignores NSAllowsArbitraryLoads.)

// MARK: - The plugin

@objc(LabPlugin)
public class LabPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "LabPlugin"
    public let jsName = "Lab"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pair", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "hello", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "show", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "place", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "hide", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "reload", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "token", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "forget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "scan", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "paste", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "notifications", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "allowNotifications", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "notNow", returnType: CAPPluginReturnPromise),
    ]

    private var lab: LabWeb?

    override public func load() {
        LabNotifications.shared.plugin = self
    }

    /// From a notification's tap: the app opens on the Lab tab (kept until the page listens, after a cold start).
    func opened() {
        notifyListeners("opened", data: ["tab": "lab"], retainUntilConsumed: true)
    }

    /// The link copied on the Mac (its Phone page, Copy the link), here by Universal Clipboard. iOS asks first.
    @objc func paste(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            call.resolve(["text": UIPasteboard.general.string ?? ""])
        }
    }

    @objc func notifications(_ call: CAPPluginCall) {
        Task { call.resolve(await LabRefresh.state()) }
    }

    @objc func allowNotifications(_ call: CAPPluginCall) {
        Task { call.resolve(await LabRefresh.allow()) }
    }

    @objc func notNow(_ call: CAPPluginCall) {
        LabRefresh.notNow()
        call.resolve(["state": "off"])
    }

    private func strings(_ call: CAPPluginCall, _ key: String) -> [String] {
        (call.getArray(key) ?? []).compactMap { $0 as? String }
    }

    /// Pair with the Mac from the QR code's fields: the secret goes to every address at once (the same Mac,
    /// checked against the pinned authority); the token that comes back is kept in the Keychain.
    @objc func pair(_ call: CAPPluginCall) {
        let hosts = strings(call, "hosts"), port = call.getInt("port") ?? 8766
        guard let ca = call.getString("ca"), let secret = call.getString("secret"), !hosts.isEmpty else {
            call.reject("missing")
            return
        }
        let name = call.getString("name") ?? "iPhone"
        Task {
            let r = await PinnedSession(ca: ca).first(hosts, port, "/api/pair", json: ["secret": secret, "name": name, "app": true],
                                                      timeout: 8)
            guard let host = r.host, let body = r.body,
                  let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let token = json["token"] as? String else {
                call.reject(r.seen.contains(403) ? "code used or expired" : r.seen.isEmpty ? "unreachable" : "refused \(r.status)")
                return
            }
            Keychain.save(token)
            call.resolve(["host": host, "mac": json["mac"] as? String ?? ""])
        }
    }

    /// Which address answers now (home first is just the order given; the first to answer wins), and what the
    /// Mac says about itself. status 401: this phone was unpaired.
    @objc func hello(_ call: CAPPluginCall) {
        let hosts = strings(call, "hosts"), port = call.getInt("port") ?? 8766
        guard let ca = call.getString("ca") else {
            call.reject("missing")
            return
        }
        let token = Keychain.load()
        let timeout = (call.getDouble("timeoutMs") ?? 8000) / 1000  // as pairing: a phone's first TLS to the Mac can be slow
        Task {
            let r = await PinnedSession(ca: ca).first(hosts, port, "/api/octo/phone/hello", token: token, timeout: timeout)
            var out: [String: Any] = ["host": r.host ?? NSNull(), "status": r.status, "paired": token != nil]
            if let body = r.body, let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                out["hello"] = json
                if r.host != nil { LabRefresh.saw(hosts: hosts, port: port, ca: ca, waiting: json["waiting"] as? [String]) }
            }
            if r.seen.contains(401) && r.host == nil { out["status"] = 401 }
            call.resolve(out)
        }
    }

    /// Show the lab at `url` over the given rectangle of the app's page (CSS pixels = points).
    @objc func show(_ call: CAPPluginCall) {
        guard let s = call.getString("url"), let url = URL(string: s), let ca = call.getString("ca") else {
            call.reject("missing")
            return
        }
        let hosts = strings(call, "hosts")
        let rect = rectOf(call)
        DispatchQueue.main.async {
            guard let vc = self.bridge?.viewController, let page = self.bridge?.webView else {
                call.reject("no view")
                return
            }
            let lab = self.lab ?? LabWeb()
            lab.plugin = self
            lab.ca = ca
            lab.hosts = hosts
            if lab.web.superview == nil { vc.view.addSubview(lab.web) }
            lab.web.frame = page.convert(rect, to: vc.view)
            lab.web.isHidden = false
            self.lab = lab
            let token = Keychain.load() ?? ""
            let same = lab.web.url.map { $0.host == url.host && $0.port == url.port } ?? false
            lab.setCookies(token: token) {
                if !same { lab.web.load(URLRequest(url: url)) }
                call.resolve()
            }
        }
    }

    @objc func place(_ call: CAPPluginCall) {
        let rect = rectOf(call)
        DispatchQueue.main.async {
            if let lab = self.lab, let vc = self.bridge?.viewController, let page = self.bridge?.webView {
                lab.web.frame = page.convert(rect, to: vc.view)
            }
            call.resolve()
        }
    }

    @objc func hide(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            self.lab?.web.isHidden = true
            call.resolve()
        }
    }

    @objc func reload(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            self.lab?.web.reload()
            call.resolve()
        }
    }

    @objc func token(_ call: CAPPluginCall) {
        call.resolve(["paired": Keychain.load() != nil])
    }

    /// Forget the lab: the token, the cookies and the view.
    @objc func forget(_ call: CAPPluginCall) {
        Keychain.clear()
        LabRefresh.forget()
        DispatchQueue.main.async {
            let done = { call.resolve() }
            if let lab = self.lab {
                lab.web.removeFromSuperview()
                lab.clearCookies(then: done)
                self.lab = nil
            } else {
                LabWeb().clearCookies(then: done)
            }
        }
    }

    @objc func scan(_ call: CAPPluginCall) {
        let go = {
            DispatchQueue.main.async {
                guard let vc = self.bridge?.viewController else {
                    call.reject("no view")
                    return
                }
                let scanner = ScannerController()
                scanner.done = { result in
                    switch result {
                    case .success(let text): call.resolve(["text": text])
                    case .failure(let e): call.reject(e.localizedDescription)
                    }
                }
                let nav = UINavigationController(rootViewController: scanner)
                nav.modalPresentationStyle = .fullScreen
                nav.overrideUserInterfaceStyle = .dark
                vc.present(nav, animated: true)
            }
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: go()
        case .notDetermined: AVCaptureDevice.requestAccess(for: .video) { ok in ok ? go() : call.reject("not allowed") }
        default: call.reject("not allowed")
        }
    }

    private func rectOf(_ call: CAPPluginCall) -> CGRect {
        let r = call.getObject("rect") ?? [:]
        func n(_ k: String) -> CGFloat {
            if let d = r[k] as? Double { return CGFloat(d) }
            if let i = r[k] as? Int { return CGFloat(i) }
            return 0
        }
        return CGRect(x: n("x"), y: n("y"), width: n("width"), height: n("height"))
    }
}
