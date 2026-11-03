"""Exercise the desktop login state machine without starting WeChat."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class LoginFlowTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("swiftc"), "requires Swift compiler")
    def test_login_order_and_required_checks(self):
        source = Path(__file__).resolve().parents[1] / "desktop/LoginFlow.swift"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.swift").write_text('''var flow = LoginFlow()
assert(flow.slot == "A" && !flow.canOpen("B"))
assert(!flow.advance())
flow.identityConfirmed = true
assert(!flow.advance()) // a checkbox cannot substitute for a successful launch
flow.didOpen("A")
assert(flow.advance())
assert(flow.slot == "B" && flow.canOpen("B") && !flow.identityConfirmed)
assert(!flow.advance())
flow.didOpen("B")
flow.identityConfirmed = true
assert(!flow.advance()) // B must actually have the old conversation on the computer
flow.historyConfirmed = true
assert(flow.advance() && flow.complete)
assert(!flow.advance())
flow = LoginFlow() // a new session or changed account starts from A again
assert(!flow.complete && !flow.canOpen("B") && flow.opened.isEmpty)
''')
            executable = root / "login-flow-test"
            subprocess.run(["swiftc", str(source), str(root / "main.swift"), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)
