import SwiftUI
import AppKit

struct Account: Codable, Equatable {
    var owner = ""
    var root = ""
    var keys = ""
    var request: [String: String] { ["owner": owner, "root": root, "keys": keys] }
}
struct Conversation: Identifiable {
    let id: String
    let name: String
    let count: Int
    let unsupported: Int
}
struct Settings: Codable, Equatable {
    var original = "/Applications/微信.app"
    var a = Account()
    var b = Account()
    var launchOnOpen = true
}

@MainActor final class Model: ObservableObject {
    @Published var settings = Settings()
    @Published var step = 0
    @Published var busy = false
    @Published var reading = false
    @Published var closing = false
    @Published var message = "软件会自动检查准备状态。首次使用只需完成系统授权和微信登录。"
    @Published var technicalError = ""
    @Published var roots: [String] = []
    @Published var discovery: [String: [String: Any]] = [:]
    @Published var copies: [String: [String: Any]] = [:]
    @Published var chats: [String: [Conversation]] = [:]
    @Published var verified: Set<String> = []
    @Published var names: [String: String] = [:]
    @Published var plan = ""
    @Published var preview: [String: [String: Any]] = [:]
    @Published var applied = false
    @Published var attempted = false
    @Published var planNames: [String: String] = [:]
    @Published var environmentReady = false
    @Published var environment = "正在检查运行环境"
    @Published var captureRequest = ""
    @Published var captureChoices: [[String: String]] = []
    @Published var captureKeys = ""
    @Published var readingSlot = ""
    private var readQueue: [String] = []
    private var initial = true
    private var ticks = 0
    private var polling = false
    private let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Chat Rescue")
    var config: URL { base.appendingPathComponent("settings.json") }
    var labels: URL { base.appendingPathComponent("account-labels.json") }
    var prepared: Bool { ["A", "B"].allSatisfy { copies[$0]?["prepared"] as? Bool == true } }
    var locked: Bool { busy || reading || closing }
    var pair: [Conversation] {
        (chats["A"] ?? []).filter { $0.id == settings.b.owner } + (chats["B"] ?? []).filter { $0.id == settings.a.owner }
    }
    init() {
        if let data = try? Data(contentsOf: config), let value = try? JSONDecoder().decode(Settings.self, from: data) { settings = value }
        if let data = try? Data(contentsOf: labels), let cache = try? JSONDecoder().decode([String: [String: String]].self, from: data) {
            for slot in ["A", "B"] {
                let account = slot == "A" ? settings.a : settings.b
                if cache[slot]?["owner"] == account.owner, cache[slot]?["root"] == account.root { names[slot] = cache[slot]?["nickname"] }
            }
        }
    }
    func name(_ slot: String) -> String { names[slot].flatMap { $0.isEmpty ? nil : $0 } ?? "微信账号 \(slot)" }
    func save() {
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        if let data = try? JSONEncoder().encode(settings) { try? data.write(to: config, options: .atomic); try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: config.path) }
    }
    func saveLabel(_ slot: String) {
        var cache = (try? Data(contentsOf: labels)).flatMap { try? JSONDecoder().decode([String: [String: String]].self, from: $0) } ?? [:]
        let account = slot == "A" ? settings.a : settings.b
        cache[slot] = ["owner": account.owner, "root": account.root, "nickname": name(slot)]
        if let data = try? JSONEncoder().encode(cache) { try? data.write(to: labels, options: .atomic); try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: labels.path) }
    }
    func accountChanged(_ slot: String) {
        verified.remove(slot); chats[slot] = nil
        if !reading { names[slot] = nil }
        if !attempted { plan = ""; preview = [:]; planNames = [:] }
        save()
    }
    func fail(_ error: String) {
        technicalError = error; reading = false; readQueue = []; captureRequest = ""; closing = false
        if error.contains("启动后退出") || error.contains("无法启动") { message = "微信启动失败。请展开详情查看具体原因；这不代表其他微信没有退出。" }
        else if error.contains("退出") || error.contains("⌘Q") { message = "微信尚未准备好。请正常退出其他微信；若微信出现确认框，先完成确认，再点击重试。" }
        else if error.contains("权限") || error.contains("取钥") { message = "读取还没有完成。请查看终端提示，完成系统授权或手机确认后重试。" }
        else { message = error }
    }
    func call(_ request: [String: Any], quiet: Bool = false, done: @escaping ([String: Any]) -> Void = { _ in }) {
        guard !busy else { return }
        if quiet { guard !polling else { return }; polling = true } else { busy = true }
        let resource = Bundle.main.resourceURL!.appendingPathComponent("backend/chat-rescue-backend").path
        DispatchQueue.global(qos: .userInitiated).async {
            var response: [String: Any]
            do {
                let process = Process(); process.executableURL = URL(fileURLWithPath: resource)
                let input = Pipe(); let output = Pipe()
                process.standardInput = input; process.standardOutput = output; process.standardError = output
                try process.run()
                let protectedActions: Set<String> = ["capture", "inspect", "plan", "apply", "undo"]
                let transport: [String: Any] = protectedActions.contains(request["action"] as? String ?? "") ? ["action": "authorized-request", "request": request] : request
                try input.fileHandleForWriting.write(contentsOf: JSONSerialization.data(withJSONObject: transport)); try input.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
                response = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            } catch { response = ["ok": false, "error": "无法运行软件核心：\(error.localizedDescription)"] }
            let result = response
            DispatchQueue.main.async {
                if quiet { self.polling = false } else { self.busy = false }
                if result["ok"] as? Bool == true, let payload = result["result"] as? [String: Any] {
                    if request["action"] as? String != "status", let instruction = payload["instruction"] as? String { self.message = instruction }
                    done(payload)
                } else if !quiet { self.fail(result["error"] as? String ?? "这一步尚未完成，请重试。") }
            }
        }
    }
    func refresh(light: Bool = false) {
        call(["action": "status", "light": light], quiet: light) { result in
            self.roots = result["database_roots"] as? [String] ?? []
            self.copies = result["copies"] as? [String: [String: Any]] ?? [:]
            self.discovery = result["discovery"] as? [String: [String: Any]] ?? [:]
            if !light { self.environmentReady = result["ready"] as? Bool == true; self.environment = self.environmentReady ? "运行环境就绪" : "运行环境需要检查" }
            if !self.reading && !self.busy {
                for slot in ["A", "B"] {
                    let account = slot == "A" ? self.settings.a : self.settings.b
                    if let found = self.discovery[slot]?["suggestion"] as? [String: String],
                       account.root.isEmpty || ((self.discovery[slot]?["reason"] as? String ?? "").contains("正在运行") && account.root != found["root"]) {
                        if slot == "A" { self.settings.a.keys = ""; self.settings.a.root = found["root"] ?? ""; self.settings.a.owner = found["owner"] ?? "" }
                        else { self.settings.b.keys = ""; self.settings.b.root = found["root"] ?? ""; self.settings.b.owner = found["owner"] ?? "" }
                    }
                }
                self.save()
            }
            if self.initial {
                self.initial = false
                if self.prepared { self.step = 1; if self.settings.launchOnOpen { self.launch(["A", "B"]) } }
            }
        }
    }
    func tick() {
        guard !busy && !closing && !polling else { return }
        if !captureRequest.isEmpty {
            call(["action": "capture-status", "request": captureRequest]) { result in
                let state = result["state"] as? String ?? "waiting"
                if state == "failed" { self.fail(result["error"] as? String ?? "读取失败，请重试") }
                if state == "complete" {
                    self.captureRequest = ""; self.captureKeys = result["keys"] as? String ?? ""
                    let choices = result["accounts"] as? [[String: String]] ?? []
                    if choices.count == 1 { self.acceptCapture(choices[0]) }
                    else if choices.count > 1 { self.captureChoices = choices; self.message = "软件已识别到多个真实昵称，请选择你刚刚登录的账号。" }
                    else { self.fail("读取回执缺少已验证的账号身份，请重新读取。") }
                }
            }
        } else if !reading {
            ticks += 1; if ticks % 2 == 0 { refresh(light: true) }
        }
    }
    func prepare() {
        technicalError = ""
        if prepared { step = 1; launch(["A", "B"]); return }
        message = "正在自动准备两个微信副本，请稍候。"
        withClosed { self.call(["action": "prepare-both", "original": self.settings.original]) { _ in self.step = 1; self.refresh() } }
    }
    func launch(_ slots: [String]) {
        technicalError = ""
        message = "正在通过已授权的终端打开微信，请稍候。"
        call(["action": "launch-via-terminal", "slots": slots]) { result in
            if let launched = result["launched"] as? [String: [String: Any]], let last = slots.last,
               let pid = launched[last]?["pid"] as? Int { NSRunningApplication(processIdentifier: pid_t(pid))?.activate() }
            self.refresh(light: true)
        }
    }
    func withClosed(_ continuation: @escaping () -> Void) {
        closing = true; technicalError = ""; message = "正在请求两个微信副本正常退出。遇到微信确认框，请按提示确认退出。"
        let paths = Set(["A", "B"].compactMap { copies[$0]?["path"] as? String }.map { $0 + "/Contents/MacOS/WeChat" })
        for app in NSWorkspace.shared.runningApplications { if let path = app.executableURL?.path, paths.contains(path) { app.terminate() } }
        Task {
            for _ in 0..<16 {
                let active = NSWorkspace.shared.runningApplications.contains { app in app.executableURL.map { paths.contains($0.path) } ?? false }
                if !active { break }; try? await Task.sleep(nanoseconds: 500_000_000)
            }
            self.closing = false
            self.call(["action": "ensure-closed"]) { _ in continuation() }
        }
    }
    func readBoth() {
        guard !locked else { return }
        step = 2; verified = []; captureChoices = []; reading = true
        withClosed { self.readQueue = ["A", "B"]; self.nextRead() }
    }
    func nextRead() {
        guard let slot = readQueue.first else {
            reading = false
            if settings.a.owner == settings.b.owner { fail("两个窗口对应同一个账号。请在第二个微信窗口切换为另一位参与者的账号，再重新读取。"); return }
            if pair.isEmpty { message = "双方本地没有找到彼此的聊天。请先在微信中把需要的记录迁移到电脑，再重新读取。"; return }
            if pair.contains(where: { $0.unsupported > 0 }) { fail("所选聊天含当前版本不支持的消息类型，已停止补齐并保留原记录。"); return }
            createPlan(); return
        }
        readingSlot = slot; let account = slot == "A" ? settings.a : settings.b
        message = "正在处理微信 \(slot)。软件会识别真实昵称、读取会话并自动继续下一步。"
        if !account.keys.isEmpty && FileManager.default.fileExists(atPath: account.keys) { inspect(slot); return }
        call(["action": "capture", "slot": slot, "original": settings.original, "account": account.request]) { result in self.captureRequest = result["request"] as? String ?? "" }
    }
    func acceptCapture(_ choice: [String: String]) {
        captureChoices = []
        let account = Account(owner: choice["owner"] ?? "", root: choice["root"] ?? "", keys: captureKeys)
        if readingSlot == "A" { settings.a = account } else { settings.b = account }
        names[readingSlot] = choice["nickname"]; save(); inspect(readingSlot)
    }
    func inspect(_ slot: String) {
        let account = slot == "A" ? settings.a : settings.b
        call(["action": "inspect", "account": account.request]) { result in
            guard let owner = result["owner"] as? [String: Any] else { self.fail("未能核验账号本人身份"); return }
            let nick = owner["nick_name"] as? String ?? ""
            self.names[slot] = nick.isEmpty ? "微信账号 \(slot)（昵称未设置）" : nick
            self.chats[slot] = (result["conversations"] as? [[String: Any]] ?? []).compactMap { row in
                guard let id = row["username"] as? String else { return nil }
                return Conversation(id: id, name: row["nick_name"] as? String ?? "", count: row["count"] as? Int ?? 0, unsupported: row["unsupported"] as? Int ?? 0)
            }
            self.verified.insert(slot); self.saveLabel(slot)
            if self.readQueue.first == slot { self.readQueue.removeFirst() }; self.nextRead()
        }
    }
    func createPlan() {
        message = "昵称与会话已读取，正在自动备份并生成双向预览。"
        call(["action": "plan", "a": settings.a.request, "b": settings.b.request]) { result in
            self.plan = result["plan"] as? String ?? ""; self.preview = result["directions"] as? [String: [String: Any]] ?? [:]
            self.planNames = ["A": self.name("A"), "B": self.name("B")]; self.applied = false; self.attempted = false; self.step = 3
            self.message = "预览已生成。请确认下面是双方真实昵称，并核对新增数量；确认前不会写入聊天库。"
        }
    }
    func apply() {
        withClosed {
            self.attempted = true
            self.call(["action": "apply", "plan": self.plan]) { _ in self.applied = true; self.step = 4; self.message = "数据库补齐完成。下一步打开两个微信，核对聊天内容和收发方向。" }
        }
    }
    func undo() {
        withClosed { self.call(["action": "undo", "plan": self.plan]) { _ in self.applied = false; self.attempted = false; self.plan = ""; self.preview = [:]; self.step = 2; self.message = "本次补齐已撤回到原始快照。" } }
    }
    func resume() {
        choose("选择之前的任务目录", directory: true) { path in
            call(["action": "resume", "plan": path]) { result in
                self.plan = result["plan"] as? String ?? ""; self.preview = result["directions"] as? [String: [String: Any]] ?? [:]
                let accounts = result["accounts"] as? [String: [String: String]] ?? [:]
                self.planNames = accounts.mapValues { $0["nickname"] ?? "历史任务账号" }
                let state = result["state"] as? String ?? "interrupted"; self.applied = state == "applied"; self.attempted = state != "ready"; self.step = self.applied ? 4 : 3
                self.message = "历史任务已读取。部分完成或中断时，请查看备份与各侧回执。"
            }
        }
    }
    func choose(_ title: String, directory: Bool, completion: (String) -> Void) {
        let panel = NSOpenPanel(); panel.title = title; panel.canChooseDirectories = directory; panel.canChooseFiles = !directory; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { completion(url.path) }
    }
    func permissions() { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!) }
    func showCopies() { NSWorkspace.shared.open(base) }
}

enum Palette {
    static let blue = Color(red: 0.23, green: 0.44, blue: 0.96)
    static let teal = Color(red: 0.08, green: 0.70, blue: 0.58)
    static let background = Color(nsColor: .windowBackgroundColor)
    static let surface = Color(nsColor: .controlBackgroundColor)
    static let stroke = Color(nsColor: .separatorColor).opacity(0.24)
}
struct BrandIcon: View {
    var size: CGFloat = 64
    var body: some View {
        Group {
            if let url = Bundle.main.resourceURL?.appendingPathComponent("AppIcon.icns"), let image = NSImage(contentsOf: url) { Image(nsImage: image).resizable().interpolation(.high) }
            else { Image(systemName: "bubble.left.and.bubble.right.fill").resizable().scaledToFit().foregroundStyle(Palette.blue) }
        }.frame(width: size, height: size)
    }
}
struct PrimaryStyle: ButtonStyle {
    @Environment(\.isEnabled) var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 15, weight: .semibold)).padding(.horizontal, 24).padding(.vertical, 14)
            .foregroundStyle(.white).background(LinearGradient(colors: [Palette.blue, Palette.blue.opacity(0.85)], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 12))
            .opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.4)
    }
}
struct Card<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View { content.padding(24).frame(maxWidth: .infinity, alignment: .leading).background(Palette.surface, in: RoundedRectangle(cornerRadius: 20)).overlay(RoundedRectangle(cornerRadius: 20).stroke(Palette.stroke, lineWidth: 1)) }
}
struct Instruction: View {
    let number: String
    let title: String
    let detail: String
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Text(number).font(.system(size: 13, weight: .bold)).foregroundStyle(Palette.blue).frame(width: 28, height: 28).background(Palette.blue.opacity(0.10), in: Circle())
            VStack(alignment: .leading, spacing: 6) { Text(title).font(.system(size: 15, weight: .semibold)); Text(detail).font(.system(size: 13)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
        }
    }
}
struct PermissionPathRow: View {
    let title: String
    let path: String
    @State private var copied = false
    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(path).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary).textSelection(.enabled).lineLimit(2)
            }
            Spacer(minLength: 8)
            Button(copied ? "已复制" : "复制路径") {
                NSPasteboard.general.clearContents(); NSPasteboard.general.setString(path, forType: .string); copied = true
            }.buttonStyle(.bordered)
            Button { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) } label: { Image(systemName: "folder") }.buttonStyle(.bordered).help("在访达中显示此应用").disabled(!FileManager.default.fileExists(atPath: path))
        }.padding(12).background(Palette.blue.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
    }
}
struct AccountTile: View {
    @ObservedObject var model: Model
    let slot: String
    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Image(systemName: "person.crop.circle.fill").font(.system(size: 34)).foregroundStyle(slot == "A" ? Palette.blue : Palette.teal)
                    Spacer()
                    let running = model.copies[slot]?["running"] as? Bool == true
                    Label(running ? "运行中" : "未打开", systemImage: running ? "circle.fill" : "circle").font(.caption).foregroundStyle(running ? Palette.teal : .secondary)
                }
                VStack(alignment: .leading, spacing: 7) {
                    Text(model.name(slot)).font(.system(size: 21, weight: .semibold))
                    Text(model.verified.contains(slot) ? "昵称与本人身份已核验" : (model.names[slot] != nil ? "上次昵称 · 读取后重新核验" : "登录并读取后，软件会识别微信昵称")).font(.caption).foregroundStyle(.secondary)
                }
                Button("查看微信 \(slot)") { model.launch([slot]) }.buttonStyle(.bordered).disabled(model.locked || model.copies[slot]?["prepared"] as? Bool != true)
            }
        }
    }
}

struct ContentView: View {
    @StateObject var model = Model()
    @State var guide = false
    @State var advanced = false
    @State var confirmApply = false
    @State var confirmUndo = false
    let timer = Timer.publish(every: 3, on: .main, in: .common).autoconnect()
    let titles = ["准备软件", "登录两个账号", "读取双方聊天", "确认补齐预览", "检查恢复结果"]
    let subtitles = ["检查环境并完成首次授权", "在两个微信窗口登录不同账号", "昵称识别、读取、备份由软件完成", "只需核对昵称和新增数量", "打开微信确认内容与方向"]
    var body: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 220)
            Rectangle().fill(Palette.stroke).frame(width: 1)
            VStack(spacing: 0) {
                topbar
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                        header
                        page
                        statusBanner
                    }.padding(.horizontal, 32).padding(.top, 24).padding(.bottom, 28)
                }
            }
        }.background(Palette.background).frame(minWidth: 1080, minHeight: 760)
        .onAppear { model.refresh() }
        .onReceive(timer) { _ in model.tick() }
        .onChange(of: model.settings.a) { model.accountChanged("A") }
        .onChange(of: model.settings.b) { model.accountChanged("B") }
        .onChange(of: model.settings.original) { model.save() }
        .sheet(isPresented: $guide) { guideSheet }
        .sheet(isPresented: $advanced) { advancedSheet }
        .alert("确认补齐双方聊天？", isPresented: $confirmApply) {
            Button("返回核对", role: .cancel) {}
            Button("已核对昵称与数量，开始补齐") { model.apply() }
        } message: { Text("\(model.planNames["A"] ?? model.name("A")) 与 \(model.planNames["B"] ?? model.name("B"))。软件会正常退出微信、核验备份后写入。请确认双方同意此次补齐。") }
        .alert("撤回本次补齐？", isPresented: $confirmUndo) {
            Button("取消", role: .cancel) {}
            Button("恢复原始快照", role: .destructive) { model.undo() }
        } message: { Text("只有双方数据库未产生新变化时才允许撤回，避免覆盖新消息。") }
    }
    var sidebar: some View {
        VStack(alignment: .leading, spacing: 28) {
            HStack(spacing: 10) { BrandIcon(size: 48); VStack(alignment: .leading, spacing: 4) { Text("Chat Rescue").font(.system(size: 17, weight: .bold)); Text("双方聊天互相补齐").font(.caption).foregroundStyle(.secondary) } }.padding(.top, 6)
            VStack(spacing: 10) {
                ForEach(0..<5, id: \.self) { index in
                    Button { model.step = index } label: {
                        HStack(spacing: 12) {
                            Text("\(index + 1)").font(.system(size: 12, weight: .bold)).frame(width: 26, height: 26).background(model.step == index ? Palette.blue : Palette.stroke, in: Circle()).foregroundStyle(model.step == index ? .white : .secondary)
                            Text(titles[index]).font(.system(size: 14, weight: model.step == index ? .semibold : .regular))
                            Spacer()
                        }.padding(12).background(model.step == index ? Palette.blue.opacity(0.10) : .clear, in: RoundedRectangle(cornerRadius: 12))
                    }.buttonStyle(.plain)
                }
            }
            Spacer()
            VStack(alignment: .leading, spacing: 14) {
                Button { guide = true } label: { Label("一步一步使用指南", systemImage: "questionmark.circle") }.buttonStyle(.plain)
                Button { advanced = true } label: { Label("高级设置", systemImage: "slider.horizontal.3") }.buttonStyle(.plain)
                Text("0.3.5 · 本机实验版").font(.caption).foregroundStyle(.tertiary)
            }.font(.system(size: 13))
        }.padding(18).background(Palette.surface.opacity(0.48))
    }
    var topbar: some View {
        HStack {
            Label(model.environment, systemImage: model.environmentReady ? "checkmark.shield.fill" : "circle.dotted").font(.caption).foregroundStyle(model.environmentReady ? Palette.teal : .secondary)
            Spacer()
            Button("历史任务") { model.resume() }.buttonStyle(.borderless).disabled(model.locked)
            Button { model.refresh() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.borderless).help("重新检测状态").disabled(model.locked)
        }.padding(.horizontal, 32).padding(.vertical, 18).overlay(alignment: .bottom) { Rectangle().fill(Palette.stroke).frame(height: 1) }
    }
    var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 10) {
                Text("第 \(model.step + 1) 步 / 共 5 步").font(.system(size: 12, weight: .semibold)).foregroundStyle(Palette.blue)
                Text(titles[model.step]).font(.system(size: 29, weight: .bold))
                Text(subtitles[model.step]).font(.system(size: 14)).foregroundStyle(.secondary)
            }
            Spacer(); BrandIcon(size: 68)
        }
    }
    @ViewBuilder var page: some View {
        switch model.step {
        case 0: preparationPage
        case 1: loginPage
        case 2: readingPage
        case 3: previewPage
        default: resultPage
        }
    }
    var preparationPage: some View {
        VStack(spacing: 18) {
            Card {
                VStack(alignment: .leading, spacing: 22) {
                    Label(model.prepared ? "两个微信副本已准备" : "先让软件完成准备", systemImage: model.prepared ? "checkmark.circle.fill" : "sparkles").font(.headline).foregroundStyle(Palette.blue)
                    Text("软件会检查运行环境、准备两个固定微信副本。原版微信和已有聊天保留。首次准备前请退出所有微信。").foregroundStyle(.secondary)
                    Button(model.prepared ? "下一步：打开两个微信" : "自动检查并准备") { model.prepare() }.buttonStyle(PrimaryStyle()).disabled(model.locked || !model.environmentReady)
                }
            }
            Card {
                VStack(alignment: .leading, spacing: 20) {
                    Text("首次需要你授权一次").font(.headline)
                    Instruction(number: "1", title: "打开系统权限设置", detail: "进入“隐私与安全 → 完全磁盘访问权限”。点击下面的按钮可直接前往。")
                    Instruction(number: "2", title: "添加软件和两个微信副本", detail: "在权限列表点“＋”，按 ⌘⇧G 打开“前往文件夹”，粘贴下面复制的路径，点击打开/添加，最后开启开关。四项分别添加。")
                    VStack(spacing: 8) {
                        PermissionPathRow(title: "Chat Rescue", path: Bundle.main.bundleURL.path)
                        PermissionPathRow(title: "Terminal（终端）", path: "/System/Applications/Utilities/Terminal.app")
                        PermissionPathRow(title: "微信 A 副本", path: FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Chat Rescue/WeChat-A.app").path)
                        PermissionPathRow(title: "微信 B 副本", path: FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Chat Rescue/WeChat-B.app").path)
                    }
                    Text("路径按这台电脑自动生成。若副本文件夹按钮不可点，先完成上方的软件准备。").font(.caption).foregroundStyle(.secondary)
                    HStack { Button("打开权限设置") { model.permissions() }; Button("打开副本所在文件夹") { model.showCopies() } }.buttonStyle(.bordered)
                    Text("系统权限只能由你确认。取钥时若出现开发者工具授权，也请按系统提示允许。以后一般无需重复授权。").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
    var loginPage: some View {
        VStack(spacing: 18) {
            HStack(alignment: .top, spacing: 18) { AccountTile(model: model, slot: "A"); AccountTile(model: model, slot: "B") }
            Card {
                VStack(alignment: .leading, spacing: 22) {
                    Instruction(number: "1", title: "在两个微信窗口分别进入账号", detail: "点击“打开两个微信”。已有登录信息时点击“进入微信”；否则扫码并在手机上确认。两边必须是不同账号。")
                    Instruction(number: "2", title: "确认需要的聊天在电脑微信里可见", detail: "如果记录只在手机上，先用微信自己的迁移功能导入对应电脑账号。B 是独立副本，首次登录不代表旧记录已经迁入。")
                    Text("此时显示“运行中”只代表微信打开了。真实昵称会在下一步读取后显示，无需你识别目录或填写微信内部编号。").font(.caption).foregroundStyle(.secondary)
                    HStack(spacing: 12) {
                        Button("打开两个微信") { model.launch(["A", "B"]) }.buttonStyle(.bordered).disabled(model.locked || !model.prepared)
                        Button("我已登录，下一步") { model.step = 2 }.buttonStyle(PrimaryStyle()).disabled(model.locked || !model.prepared)
                    }
                }
            }
        }
    }
    var readingPage: some View {
        VStack(spacing: 18) {
            Card {
                VStack(alignment: .leading, spacing: 22) {
                    Text(model.reading ? "软件正在继续处理" : "点击一次，软件继续完成后续步骤").font(.headline)
                    Instruction(number: "1", title: "正常退出两个微信副本", detail: "点击下方按钮后，软件会请求两个副本正常退出。若微信弹出退出确认框，请你确认；软件不会强制结束微信。原版若仍在运行，需要你正常退出。")
                    Instruction(number: "2", title: "首次读取时，在终端启动的微信里登录", detail: "软件会依次处理 A、B。终端弹出后，登录当前微信并完成手机确认；需要授权时按系统提示操作。无需复制命令或寻找密钥文件。")
                    Instruction(number: "3", title: "等待昵称和预览自动出现", detail: "四个必要数据库验证完成后，软件识别本人昵称、读取聊天、备份并生成预览。两侧依次进行；这一步不会写入聊天库。")
                    if model.reading || model.closing { HStack { ProgressView().controlSize(.small); Text(model.closing ? "等待微信正常退出…" : "正在处理微信 \(model.readingSlot)，请关注终端和手机提示").foregroundStyle(Palette.blue) } }
                    Button(model.reading || model.closing ? "正在处理，请等待" : "关闭副本，读取双方聊天并生成预览") { model.readBoth() }.buttonStyle(PrimaryStyle()).disabled(model.locked || !model.prepared)
                    if !model.captureChoices.isEmpty {
                        Divider(); Text("已读取真实昵称，请选择你刚刚登录的账号：").font(.headline)
                        ForEach(Array(model.captureChoices.enumerated()), id: \.offset) { _, choice in Button(choice["nickname"] ?? "未设置昵称的微信账号") { model.acceptCapture(choice) }.buttonStyle(.bordered) }
                    }
                }
            }
            HStack(spacing: 18) { readingState("A"); readingState("B") }
        }
    }
    func readingState(_ slot: String) -> some View {
        Card { HStack(spacing: 12) { Image(systemName: model.verified.contains(slot) ? "checkmark.circle.fill" : "person.crop.circle").font(.title2).foregroundStyle(slot == "A" ? Palette.blue : Palette.teal); VStack(alignment: .leading, spacing: 5) { Text(model.name(slot)).font(.headline); Text(model.verified.contains(slot) ? "昵称与聊天读取完成" : "等待读取，尚未核验昵称").font(.caption).foregroundStyle(.secondary) }; Spacer() } }
    }
    var previewPage: some View {
        VStack(spacing: 18) {
            if model.plan.isEmpty {
                Card { VStack(alignment: .leading, spacing: 18) { Text("先读取双方聊天").font(.headline); Text("软件读取完成后会自动生成预览。这里不会要求你选择数据库或辨认微信内部编号。").foregroundStyle(.secondary); Button("前往读取聊天") { model.step = 2 }.buttonStyle(PrimaryStyle()) } }
            } else {
                Card {
                    VStack(alignment: .leading, spacing: 22) {
                        Text("请确认是这两个人的聊天").font(.headline)
                        HStack { nicknameBlock("A"); Image(systemName: "arrow.left.arrow.right").font(.title2).foregroundStyle(Palette.blue); nicknameBlock("B") }
                        Divider()
                        ForEach(["A", "B"], id: \.self) { slot in
                            let direction = model.preview[slot] ?? [:]
                            HStack { Text("补齐到 \(model.planNames[slot] ?? model.name(slot))").font(.system(size: 15, weight: .medium)); Spacer(); Text("新增 \(direction["added"] as? Int ?? 0) 条").foregroundStyle(Palette.blue); Text("已有重复 \(direction["skipped"] as? Int ?? 0) 条").foregroundStyle(.secondary) }
                        }
                        Text("已有消息保留，重复消息跳过。冲突或不支持的内容会停止处理。确认后才写入；原件和回执保存在本机。").font(.caption).foregroundStyle(.secondary)
                        HStack { Button("昵称与数量正确，确认补齐") { confirmApply = true }.buttonStyle(PrimaryStyle()).disabled(model.locked || model.attempted); Button("查看备份与回执") { NSWorkspace.shared.open(URL(fileURLWithPath: model.plan)) }.buttonStyle(.bordered) }
                    }
                }
            }
            Text("当前支持文本、名片和文件消息条目；附件本体不恢复。手机结果需通过微信自己的迁移入口另行验收。").font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    func nicknameBlock(_ slot: String) -> some View {
        VStack(spacing: 10) { Image(systemName: "person.crop.circle.fill").font(.system(size: 42)).foregroundStyle(slot == "A" ? Palette.blue : Palette.teal); Text(model.planNames[slot] ?? model.name(slot)).font(.system(size: 21, weight: .semibold)); Text("微信账号 \(slot)").font(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity).padding(.vertical, 12)
    }
    var resultPage: some View {
        Card {
            VStack(alignment: .leading, spacing: 24) {
                Label(model.applied ? "数据库补齐已完成" : "这里显示补齐后的验收步骤", systemImage: model.applied ? "checkmark.circle.fill" : "checklist").font(.system(size: 22, weight: .semibold)).foregroundStyle(Palette.teal)
                Instruction(number: "1", title: "打开两个微信，找到双方聊天", detail: "确认原有消息仍在，新增记录的时间、内容、头像和发送/接收方向正确。数据库验证不能代替你检查实际显示。")
                Instruction(number: "2", title: "需要手机记录时，使用微信自己的迁移功能", detail: "由本人在电脑微信和手机上完成迁移，并重新核对内容和方向。软件不会自动同步到微信服务器。")
                HStack { Button("打开两个微信检查") { model.launch(["A", "B"]) }.buttonStyle(PrimaryStyle()).disabled(model.locked || !model.prepared || !model.applied); if !model.plan.isEmpty { Button("查看备份") { NSWorkspace.shared.open(URL(fileURLWithPath: model.plan)) }.buttonStyle(.bordered) } }
                if model.applied { Divider(); Text("需要撤回时，软件会先检查是否产生新消息。发现变化会拒绝撤回，保护新记录。").font(.caption).foregroundStyle(.secondary); Button("撤回本次补齐", role: .destructive) { confirmUndo = true }.disabled(model.locked) }
            }
        }
    }
    var statusBanner: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) { if model.busy || model.closing { ProgressView().controlSize(.small) } else { Image(systemName: model.technicalError.isEmpty ? "info.circle.fill" : "exclamationmark.circle.fill").foregroundStyle(model.technicalError.isEmpty ? Palette.blue : .orange) }; Text(model.message).font(.system(size: 13)).textSelection(.enabled) }
            if !model.technicalError.isEmpty { DisclosureGroup("查看错误详情") { Text(model.technicalError).font(.caption).textSelection(.enabled).foregroundStyle(.secondary).padding(.top, 6) } }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).background(Palette.blue.opacity(0.07), in: RoundedRectangle(cornerRadius: 14))
    }
    var guideSheet: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack { BrandIcon(size: 44); Text("一步一步使用指南").font(.title2.bold()); Spacer(); Button("完成") { guide = false } }
            ScrollView { VStack(alignment: .leading, spacing: 24) {
                Instruction(number: "1", title: "准备软件与首次权限", detail: "在“准备软件”页点自动准备。进入完全磁盘访问权限，点“＋”，按 ⌘⇧G，粘贴准备页中对应的应用路径并添加，开启开关。四项分别添加。需要安装 Xcode Command Line Tools 时，按系统提示完成。")
                Instruction(number: "2", title: "分别登录两个不同账号", detail: "在“登录两个账号”页打开微信 A、B，扫码或进入已有账号。手机确认由本人完成。聊天只在手机上时，先迁移到对应电脑账号，直到所需聊天在电脑里可见。")
                Instruction(number: "3", title: "点击一次读取并等待", detail: "在“读取双方聊天”页点击主按钮。软件请求副本正常退出，然后依次启动终端取钥。按当前终端提示登录并确认，等待软件自动识别昵称、读取聊天、备份和生成预览。无需复制命令、选择数据库目录或寻找密钥。")
                Instruction(number: "4", title: "核对昵称和新增数量", detail: "查看预览中两个真实昵称与消息数量。只有确认双方身份与授权后，点击确认补齐。已有记录保留，重复跳过，冲突停止。")
                Instruction(number: "5", title: "验收实际聊天显示", detail: "打开两个微信逐项检查。需要手机记录时，另用微信迁移并在手机验收。遇到问题保留备份与回执；数据库变化后不会自动覆盖新消息来撤回。")
            }.padding(.vertical, 8) }
        }.padding(28).frame(width: 740, height: 650)
    }
    var advancedSheet: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { Text("高级设置").font(.title2.bold()); Spacer(); Button("完成") { advanced = false } }
            Text("通常不需要修改。主流程会自动识别目录、密钥与微信昵称。").foregroundStyle(.secondary)
            HStack { Text("原版微信"); TextField("微信 .app", text: $model.settings.original); Button("选择") { model.choose("选择原版微信", directory: false) { model.settings.original = $0 } } }.disabled(model.locked)
            Toggle("打开软件时自动打开已准备的微信副本", isOn: $model.settings.launchOnOpen).disabled(model.locked).onChange(of: model.settings.launchOnOpen) { model.save() }
            Divider()
            advancedAccount("A", account: $model.settings.a).disabled(model.locked)
            advancedAccount("B", account: $model.settings.b).disabled(model.locked)
            HStack { Button("权限设置") { model.permissions() }; Button("副本文件夹") { model.showCopies() }; Button("重新检测") { model.refresh() } }
        }.padding(28).frame(width: 800)
    }
    func advancedAccount(_ slot: String, account: Binding<Account>) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(model.name(slot)).font(.headline)
            HStack { TextField("数据库目录（自动识别）", text: account.root); Button("选择目录") { model.choose("选择账号 db_storage", directory: true) { path in account.wrappedValue.root = path; account.wrappedValue.owner = URL(fileURLWithPath: path).deletingLastPathComponent().lastPathComponent.replacingOccurrences(of: "_[0-9a-fA-F]{4}$", with: "", options: .regularExpression) } } }
            TextField("内部身份（自动填写）", text: account.owner)
            HStack { TextField("密钥文件（自动管理）", text: account.keys); Button("导入已有密钥") { model.choose("选择已验证的密钥文件", directory: false) { account.wrappedValue.keys = $0 } } }
        }
    }
}
@main struct ChatRescueApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }.windowStyle(.titleBar).defaultSize(width: 1180, height: 860).commands { CommandGroup(replacing: .newItem) {} }
    }
}
