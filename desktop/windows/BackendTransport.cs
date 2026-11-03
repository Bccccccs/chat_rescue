using System.Globalization;
using System.IO;
using System.Text;

namespace ChatRescueWindows {
public static class BackendTransport {
    public static string ResolveConfiguredPath(string directory, string value) {
        if (string.IsNullOrEmpty(value)) return "";
        return Path.GetFullPath(Path.IsPathRooted(value) ? value : Path.Combine(directory, value));
    }
    // .NET Framework's redirected StandardInput inherits Console.InputEncoding
    // (often GBK on Chinese Windows). JSON unicode escapes keep the wire payload
    // ASCII without changing any decoded string, including surrogate pairs.
    public static string EscapeJsonForPipe(string json) {
        var output = new StringBuilder(json.Length);
        foreach(char value in json) {
            if(value > 127) output.Append("\\u").Append(((int)value).ToString("x4", CultureInfo.InvariantCulture));
            else output.Append(value);
        }
        return output.ToString();
    }
}}
