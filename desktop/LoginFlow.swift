// Login confirmations belong to this session, never to cached account labels.
struct LoginFlow {
    enum Stage { case a, b, complete }
    private(set) var stage: Stage = .a
    private(set) var opened: Set<String> = []
    var identityConfirmed = false
    var historyConfirmed = false
    var slot: String { stage == .a ? "A" : "B" }
    var complete: Bool { stage == .complete }
    var importReminder: String {
        stage == .b ? "现在请从手机 B，把 B 与 A 的聊天记录导入电脑上的微信 B。电脑 B 已有完整记录时，核对即可；完成导入并检查后再继续读取。" : ""
    }
    var canConfirm: Bool {
        !complete && opened.contains(slot) && identityConfirmed && (stage == .a || historyConfirmed)
    }
    func canOpen(_ slot: String) -> Bool {
        slot == "A" || (slot == "B" && stage != .a)
    }
    mutating func didOpen(_ slot: String) { opened.insert(slot) }
    @discardableResult mutating func advance() -> Bool {
        guard canConfirm else { return false }
        stage = stage == .a ? .b : .complete
        identityConfirmed = false; historyConfirmed = false
        return true
    }
}
