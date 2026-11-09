// This confirmation belongs to the current login, never to saved account paths.
namespace ChatRescueWindows {
public sealed class SourceImportFlow {
    public bool Confirmed { get; private set; }
    public void Confirm(bool value) { Confirmed = value; }
    public void Reset() { Confirmed = false; }
    public bool CanRead(string slot, bool firstVerified) {
        return slot == "A" || (slot == "B" && Confirmed);
    }
    public string Reminder(string slot, bool recoveryComplete) {
        return slot == "B" && !recoveryComplete
            ? "请从来源账号的手机将与目标账号的聊天导入来源账号的电脑微信。使用微信自己的聊天迁移入口，等待导入完成；电脑已有完整记录时可直接核对。确认目标单聊的内容、时间范围和所需媒体完整后，再读取来源账号。"
            : "";
    }
}
}
