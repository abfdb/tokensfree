"""Static egress guard for the new kernel.

Network primitives may only appear where the design allows them: provider
adapters, the loopback HTTP host, and the desktop supervisor (which polls the
loopback gateway only). Everything else in `tokensfree/` must stay
pure, so a future change cannot quietly open a socket from the router or store.

Run: python scripts/check_imports.py
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "tokensfree"
NETWORK_MODULES = {"socket", "ssl", "http.client", "http.server", "urllib.request", "ftplib", "smtplib", "xmlrpc.client"}
ALLOWED_PATHS = (
    "tokensfree/adapters/",
    "tokensfree/edge/host.py",
    "tokensfree/desktop/process.py",
)


def module_name(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return [node.module]
    return []


def is_network(name):
    return name in NETWORK_MODULES or any(name.startswith(prefix + ".") for prefix in NETWORK_MODULES)


def scan(root):
    """Return violations as (relative_path, line, module) for network imports outside allowed paths."""
    violations = []
    package_root = pathlib.Path(root) / PACKAGE
    if not package_root.exists():
        return violations
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            violations.append((relative, error.lineno or 0, "syntax-error"))
            continue
        if relative.startswith(ALLOWED_PATHS):
            continue
        for node in ast.walk(tree):
            for name in module_name(node):
                if is_network(name):
                    violations.append((relative, node.lineno, name))
    return violations


def main():
    violations = scan(ROOT)
    if violations:
        for path, line, module in violations:
            print("violation: %s:%d imports %s" % (path, line, module))
        print("import guard FAILED: move network code into tokensfree/adapters/, tokensfree/edge/host.py or tokensfree/desktop/process.py")
        return 1
    print("import guard ok: no network imports outside adapters/host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
