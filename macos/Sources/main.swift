// ClipForge for macOS: a native shell around the local ClipForge engine.
//
// - First launch installs the engine into ~/Library/Application Support/ClipForge/runtime
//   (a Python virtualenv built from the bundled sources) and shows progress.
// - Starts `clipforge up` on localhost:3000 and shows the dashboard in a native window.
// - Lives in the menu bar: closing the window keeps automation (scheduled posts) running.
// - Mirrors important events (viral alerts, failed posts, clips ready) as macOS notifications.

import AppKit
import UserNotifications
import WebKit

let PORT = 3000
let BASE = URL(string: "http://127.0.0.1:\(PORT)")!

final class Engine {
    var process: Process?
    let support = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/ClipForge")
    var runtime: URL { support.appendingPathComponent("runtime") }
    var cli: URL { runtime.appendingPathComponent("venv/bin/clipforge") }
    var resources: URL { Bundle.main.resourceURL! }
    var logFile: URL { support.appendingPathComponent("logs/app.log") }

    var bundledVersion: String {
        (try? String(contentsOf: resources.appendingPathComponent("backend/VERSION"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? "0"
    }
    var installedVersion: String {
        (try? String(contentsOf: runtime.appendingPathComponent("VERSION"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
    var needsInstall: Bool {
        !FileManager.default.fileExists(atPath: cli.path) || installedVersion != bundledVersion
    }

    func isUp(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: BASE.appendingPathComponent("api/summary"))
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            done((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    /// Runs the bundled installer, streaming its output line by line.
    func install(onLine: @escaping (String) -> Void, onDone: @escaping (Bool) -> Void) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [resources.appendingPathComponent("setup.sh").path, resources.appendingPathComponent("backend").path,
                       runtime.path]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { h in
            if let s = String(data: h.availableData, encoding: .utf8), !s.isEmpty {
                s.split(separator: "\n").forEach { line in DispatchQueue.main.async { onLine(String(line)) } }
            }
        }
        p.terminationHandler = { proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async { onDone(proc.terminationStatus == 0) }
        }
        do { try p.run() } catch { onDone(false) }
    }

    func start() {
        try? FileManager.default.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logFile.path, contents: nil)
        let p = Process()
        p.executableURL = cli
        p.arguments = ["up", "--port", "\(PORT)"]
        var env = ProcessInfo.processInfo.environment
        env["CLIPFORGE_FRONTEND"] = resources.appendingPathComponent("web").path
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        if let h = try? FileHandle(forWritingTo: logFile) { p.standardOutput = h; p.standardError = h }
        try? p.run()
        process = p
    }

    func stop() {
        guard let p = process, p.isRunning else { return }
        p.interrupt()
        let deadline = Date().addingTimeInterval(8)
        while p.isRunning && Date() < deadline { usleep(100_000) }
        if p.isRunning { p.terminate() }
    }
}

final class SetupView: NSView {
    let title = NSTextField(labelWithString: "Setting up ClipForge")
    let subtitle = NSTextField(wrappingLabelWithString: "Installing the local AI engine. This happens once and takes a few minutes. Models download the first time you make clips.")
    let spinner = NSProgressIndicator()
    let log = NSTextView()

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor(calibratedRed: 0.05, green: 0.05, blue: 0.05, alpha: 1).cgColor
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        title.textColor = .white
        subtitle.textColor = NSColor(white: 0.75, alpha: 1)
        spinner.style = .spinning
        spinner.startAnimation(nil)
        log.isEditable = false
        log.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        log.textColor = NSColor(white: 0.65, alpha: 1)
        log.backgroundColor = NSColor(white: 0.1, alpha: 1)
        let scroll = NSScrollView()
        scroll.documentView = log
        scroll.hasVerticalScroller = true
        log.autoresizingMask = [.width]
        for v in [title, subtitle, spinner, scroll] as [NSView] { v.translatesAutoresizingMaskIntoConstraints = false; addSubview(v) }
        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: topAnchor, constant: 48),
            title.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 48),
            spinner.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            spinner.leadingAnchor.constraint(equalTo: title.trailingAnchor, constant: 12),
            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 10),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -48),
            scroll.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 24),
            scroll.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -48),
            scroll.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -48),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }

    func append(_ line: String) {
        log.textStorage?.append(NSAttributedString(string: line + "\n", attributes: [.foregroundColor: NSColor(white: 0.7, alpha: 1), .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)]))
        log.scrollToEndOfDocument(nil)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    let engine = Engine()
    var window: NSWindow!
    var web: WKWebView!
    var status: NSStatusItem!
    var lastEventId = 0
    var timer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "ClipForge"
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("ClipForgeMain")
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)

        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.image = NSImage(systemSymbolName: "film.stack", accessibilityDescription: "ClipForge")
        let m = NSMenu()
        m.addItem(withTitle: "Open ClipForge", action: #selector(showWindow), keyEquivalent: "o")
        m.addItem(withTitle: "Open in Browser", action: #selector(openBrowser), keyEquivalent: "b")
        m.addItem(.separator())
        m.addItem(withTitle: "Pause Automation", action: #selector(pause), keyEquivalent: "")
        m.addItem(withTitle: "Resume Automation", action: #selector(resume), keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Show Logs", action: #selector(showLogs), keyEquivalent: "l")
        m.addItem(withTitle: "Quit ClipForge", action: #selector(quit), keyEquivalent: "q")
        status.menu = m

        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        engine.isUp { up in
            DispatchQueue.main.async {
                if up { self.showDashboard() } else if self.engine.needsInstall { self.runSetup() } else { self.boot() }
            }
        }
    }

    func runSetup() {
        let v = SetupView(frame: window.contentView!.bounds)
        window.contentView = v
        engine.install(onLine: { v.append($0) }, onDone: { ok in
            if ok { self.boot() } else {
                v.append("\nSetup failed. Fix the issue above and reopen ClipForge.")
                v.spinner.stopAnimation(nil)
            }
        })
    }

    func boot() {
        if !(window.contentView is SetupView) { window.contentView = SetupView(frame: window.contentView!.bounds) }
        (window.contentView as? SetupView)?.title.stringValue = "Starting ClipForge…"
        (window.contentView as? SetupView)?.subtitle.stringValue = "Loading the dashboard and the automation worker."
        engine.start()
        waitUntilUp(tries: 120)
    }

    func waitUntilUp(tries: Int) {
        engine.isUp { up in
            DispatchQueue.main.async {
                if up { self.showDashboard() }
                else if tries > 0 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitUntilUp(tries: tries - 1) } }
                else { (self.window.contentView as? SetupView)?.append("The engine didn't start. See Show Logs in the menu bar.") }
            }
        }
    }

    func showDashboard() {
        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        web = WKWebView(frame: window.contentView!.bounds, configuration: cfg)
        web.autoresizingMask = [.width, .height]
        web.uiDelegate = self
        web.navigationDelegate = self
        web.allowsBackForwardNavigationGestures = true
        window.contentView = web
        web.load(URLRequest(url: BASE))
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in self.pollEvents() }
        pollEvents(initial: true)
    }

    // Native notifications for the important events.
    func pollEvents(initial: Bool = false) {
        let url = BASE.appendingPathComponent("api/events").appending(queryItems: [URLQueryItem(name: "since_id", value: "\(lastEventId)"), URLQueryItem(name: "limit", value: "20")])
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data, let items = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return }
            let important: Set<String> = ["clips_ready", "viral", "post_failed", "token_expiring", "replies_waiting", "published", "weekly_report"]
            for e in items.reversed() {
                let id = e["id"] as? Int ?? 0
                if id > self.lastEventId { self.lastEventId = id }
                if initial { continue }
                guard let kind = e["kind"] as? String, important.contains(kind) else { continue }
                let c = UNMutableNotificationContent()
                c.title = e["title"] as? String ?? "ClipForge"
                c.body = e["body"] as? String ?? ""
                UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "ev\(id)", content: c, trigger: nil))
            }
        }.resume()
    }

    // MARK: web view behaviour

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = navigationAction.request.url { NSWorkspace.shared.open(u) }
        return nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let u = navigationAction.request.url else { return decisionHandler(.allow) }
        if navigationAction.shouldPerformDownload { return decisionHandler(.download) }
        // OAuth consent pages (Google, Instagram) open in the real browser; everything local stays here.
        if u.host != "127.0.0.1" && u.host != "localhost" && navigationAction.navigationType == .linkActivated {
            NSWorkspace.shared.open(u)
            return decisionHandler(.cancel)
        }
        if u.path.hasPrefix("/api/oauth/") && u.path.hasSuffix("/start") {
            NSWorkspace.shared.open(URL(string: "http://localhost:\(PORT)\(u.path)?\(u.query ?? "")")!)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first!
        completionHandler(dir.appendingPathComponent(suggestedFilename))
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.begin { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message; a.runModal(); completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        completionHandler(a.runModal() == .alertFirstButtonReturn)
    }

    // MARK: menu actions

    @objc func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func openBrowser() { NSWorkspace.shared.open(URL(string: "http://localhost:\(PORT)")!) }
    @objc func showLogs() { NSWorkspace.shared.open(engine.support.appendingPathComponent("logs")) }
    @objc func pause() { post("api/automation", body: ["paused": true]) }
    @objc func resume() { post("api/automation", body: ["paused": false]) }
    @objc func quit() { NSApp.terminate(nil) }

    func post(_ path: String, body: [String: Any]) {
        var r = URLRequest(url: BASE.appendingPathComponent(path))
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: body)
        URLSession.shared.dataTask(with: r).resume()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func applicationWillTerminate(_ notification: Notification) { engine.stop() }

    func buildMenu() {
        let main = NSMenu()
        let app = NSMenuItem(); main.addItem(app)
        let am = NSMenu()
        am.addItem(withTitle: "About ClipForge", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        am.addItem(.separator())
        am.addItem(withTitle: "Hide ClipForge", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        am.addItem(withTitle: "Quit ClipForge", action: #selector(quit), keyEquivalent: "q")
        app.submenu = am
        let edit = NSMenuItem(); main.addItem(edit)
        let em = NSMenu(title: "Edit")
        em.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        em.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        em.addItem(.separator())
        em.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        em.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        em.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        em.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.submenu = em
        let view = NSMenuItem(); main.addItem(view)
        let vm = NSMenu(title: "View")
        vm.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        view.submenu = vm
        NSApp.mainMenu = main
    }

    @objc func reload() { web?.reload() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
