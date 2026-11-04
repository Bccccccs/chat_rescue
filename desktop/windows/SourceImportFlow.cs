// This confirmation belongs to the current login, never to saved account paths.
namespace ChatRescueWindows {
public sealed class SourceImportFlow {
    public bool Confirmed { get; private set; }
    public void Confirm(bool value) { Confirmed = value; }
    public void Reset() { Confirmed = false; }
    public bool CanRead(string slot, bool firstVerified) {
        return slot == "A" || (slot == "B" && firstVerified && Confirmed);
    }
    public string Reminder(string slot, bool recoveryComplete) {
        return slot == "B" && !recoveryComplete
            ? "现在从手机 B 将与 A 的聊天导入电脑微信 B。请使用微信自己的聊天迁移入口，等待导入完成；如果电脑 B 已有完整记录，可直接核对。确认目标单聊的内容、时间范围和所需图片/语音等条目在电脑 B 可见后，再读取账号 B。"
            : "";
    }
}
}
