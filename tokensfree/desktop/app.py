"""Tkinter control panel for the tokensfree local gateway (stdlib only)."""
import argparse
import json
import os
import queue
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from tokensfree.desktop import config as desktop_config
from tokensfree.desktop import startup
from tokensfree.desktop.process import GatewayController

POLL_MS = 1000
STATUS_MS = 5000


class DesktopApp:
    def __init__(self, root, *, config, controller=None, tray=None):
        self.root = root
        self.config = config
        self.controller = controller or GatewayController(config)
        self._busy = False
        self._events = queue.Queue()
        self._tray_events = queue.Queue()
        self.tray = None if tray is False else (tray if tray is not None else self._create_tray())
        self.root.title("tokensfree 控制台")
        self.root.geometry("880x640")
        self.root.minsize(760, 560)
        self._build()
        self._load_values()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill="x")
        ttk.Label(top, text="tokensfree", font=("Segoe UI", 15, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="未运行")
        self.status_label = ttk.Label(top, textvariable=self.status_var, foreground="#b45309")
        self.status_label.pack(side="left", padx=12)
        self.start_button = ttk.Button(top, text="启动网关", command=self.start_gateway)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(top, text="停止网关", command=self.stop_gateway, state="disabled")
        self.stop_button.pack(side="right", padx=6)
        self.console_button = ttk.Button(top, text="打开网页控制台", command=self.open_console, state="disabled")
        self.console_button.pack(side="right", padx=6)

        settings = ttk.LabelFrame(self.root, text="设置", padding=(12, 8))
        settings.pack(fill="x", padx=12)
        row = ttk.Frame(settings)
        row.pack(fill="x")
        ttk.Label(row, text="模式").pack(side="left")
        self.mode_var = tk.StringVar(value="mock")
        self.mode_combo = ttk.Combobox(row, textvariable=self.mode_var, values=list(desktop_config.MODES), width=10, state="readonly")
        self.mode_combo.pack(side="left", padx=(6, 16))
        ttk.Label(row, text="端口").pack(side="left")
        self.port_var = tk.StringVar(value=str(desktop_config.DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=(6, 16))
        ttk.Label(row, text="loopback 上游端口").pack(side="left")
        self.loopback_var = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.loopback_var, width=8).pack(side="left", padx=6)
        self.autostart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="开机启动", variable=self.autostart_var, command=self.toggle_autostart).pack(side="left", padx=12)

        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="数据库").pack(side="left")
        self.db_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.db_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="选择…", command=self.browse_db).pack(side="left")

        status_frame = ttk.LabelFrame(self.root, text="运行时状态", padding=(8, 6))
        status_frame.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.tree = ttk.Treeview(status_frame, columns=("kind", "name", "detail"), show="headings", height=7)
        for column, title, width in (("kind", "类型", 90), ("name", "名称", 160), ("detail", "详情", 520)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(self.root, text="网关日志", padding=(8, 6))
        log_frame.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap="none", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(self.root, padding=(12, 8))
        bottom.pack(fill="x")
        self.hint_var = tk.StringVar(value="提示：令牌保存在本机配置文件中，仅用于本地网关。")
        ttk.Label(bottom, textvariable=self.hint_var).pack(side="left")
        ttk.Button(bottom, text="保存设置", command=self.save_settings).pack(side="right")

    def _load_values(self):
        self.mode_var.set(self.config.mode)
        self.port_var.set(str(self.config.port))
        self.loopback_var.set(str(self.config.loopback_port))
        self.db_var.set(self.config.db_path)
        self.autostart_var.set(startup.is_autostart_enabled())

    def _create_tray(self):
        if not self.config.minimize_to_tray:
            return None
        try:
            from tokensfree.desktop.tray import IS_WINDOWS, TrayIcon
        except ImportError:
            return None
        if not IS_WINDOWS:
            return None
        icon = TrayIcon(on_command=lambda command: self._tray_events.put(command))
        if icon.start():
            return icon
        self.hint_var.set("系统托盘不可用：%s（关闭窗口将直接退出）" % (icon.error or "unknown"))
        return None

    def _current_config(self):
        try:
            port = int(self.port_var.get().strip() or str(desktop_config.DEFAULT_PORT))
            loopback_port = int(self.loopback_var.get().strip() or "0")
        except ValueError:
            raise ValueError("端口必须是数字") from None
        return desktop_config.with_updates(
            self.config,
            port=port,
            loopback_port=loopback_port,
            mode=self.mode_var.get(),
            db_path=self.db_var.get().strip() or str(desktop_config.default_db_path()),
            autostart=self.autostart_var.get(),
        )

    def save_settings(self):
        try:
            config = self._current_config()
        except ValueError as error:
            messagebox.showerror("设置无效", str(error))
            return None
        desktop_config.save_config(config)
        self.config = config
        self.controller = GatewayController(config)
        self.hint_var.set("设置已保存：" + str(desktop_config.config_path()))
        return config

    def browse_db(self):
        chosen = filedialog.asksaveasfilename(
            title="选择 SQLite 数据库",
            defaultextension=".db",
            initialfile="state.db",
        )
        if chosen:
            self.db_var.set(chosen)

    def toggle_autostart(self):
        try:
            if self.autostart_var.get():
                startup.set_autostart(True, repo_root=desktop_config.repo_root())
                self.hint_var.set("已启用开机启动（Startup 文件夹快捷脚本）")
            else:
                startup.remove_autostart()
                self.hint_var.set("已关闭开机启动")
        except OSError as error:
            self.autostart_var.set(not self.autostart_var.get())
            messagebox.showerror("开机启动", "无法写入 Startup 文件夹：%s" % error)

    def _run_async(self, action, on_success, on_error):
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                result = action()
                self._events.put((on_success, result, None))
            except Exception as error:  # surfaced to the user, never swallowed silently
                self._events.put((on_error, None, error))

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _apply_events(self):
        while True:
            try:
                callback, result, error = self._events.get_nowait()
            except queue.Empty:
                break
            self._busy = False
            if error is not None:
                self.status_var.set("错误")
                self.start_button.configure(state="normal")
                messagebox.showerror("操作失败", str(error))
            else:
                callback(result)

    def start_gateway(self):
        config = self.save_settings()
        if config is None:
            return
        self.status_var.set("启动中…")
        self.start_button.configure(state="disabled")
        self._run_async(
            lambda: self.controller.start(timeout=40),
            self._on_started,
            self._on_start_failed,
        )

    def _on_started(self, port):
        self.status_var.set("运行中 · 127.0.0.1:%d" % port)
        self.status_label.configure(foreground="#15803d")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.console_button.configure(state="normal")
        self.hint_var.set("控制台：" + self.controller.console_url())

    def _on_start_failed(self, error):
        self.status_var.set("启动失败")
        self.status_label.configure(foreground="#b91c1c")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.console_button.configure(state="disabled")

    def stop_gateway(self):
        if not self.controller.running:
            self._after_stop()
            return
        self.status_var.set("停止中…")
        self.stop_button.configure(state="disabled")
        self._run_async(self.controller.stop, lambda _result: self._after_stop(), lambda error: self._after_stop())

    def _after_stop(self):
        self.status_var.set("未运行")
        self.status_label.configure(foreground="#b45309")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.console_button.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())

    def open_console(self):
        url = self.controller.console_url()
        if url:
            webbrowser.open(url)

    def _poll(self):
        try:
            self._apply_tray_events()
            self._apply_events()
            self._refresh_logs()
            if self.controller.running:
                self._refresh_status()
        finally:
            self.root.after(POLL_MS, self._poll)

    def _refresh_logs(self):
        lines = self.controller.logs(tail=200)
        text = "\n".join(lines)
        if text == getattr(self, "_last_logs", None):
            return
        self._last_logs = text
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_status(self):
        try:
            status = self.controller.status()
        except RuntimeError:
            return
        self.tree.delete(*self.tree.get_children())
        for provider in status.get("providers", []):
            detail = provider.get("model", "")
            if provider.get("state") != "eligible":
                detail = "%s / %s" % (provider.get("state"), provider.get("policy_reason"))
            self.tree.insert("", "end", values=("provider", provider.get("name"), detail))
        for account in status.get("accounts", []):
            self.tree.insert(
                "", "end",
                values=("account", account.get("provider"), "%s · 已计费 %s" % (account.get("status"), account.get("requests_charged"))),
            )
        for name, breaker in (status.get("breakers") or {}).items():
            self.tree.insert("", "end", values=("breaker", name, breaker.get("state", "unknown")))

    def _apply_tray_events(self):
        while True:
            try:
                command = self._tray_events.get_nowait()
            except queue.Empty:
                return
            if command == "show":
                self.root.deiconify()
                self.root.lift()
            elif command == "console":
                self.open_console()
            elif command == "toggle-gateway":
                if self.controller.running:
                    self.stop_gateway()
                else:
                    self.start_gateway()
            elif command == "exit":
                self.exit_application()

    def exit_application(self):
        try:
            if self.tray is not None:
                self.tray.stop()
                self.tray = None
            self.controller.stop()
        finally:
            self.root.destroy()

    def on_close(self):
        if self.tray is not None:
            self.root.withdraw()
            self.hint_var.set("已最小化到系统托盘；托盘菜单可退出")
            return
        self.exit_application()


def run_self_check():
    import tempfile

    report = {"ok": False, "ui": False}
    with tempfile.TemporaryDirectory(prefix="tokensfree-desktop-check-") as temp:
        config = desktop_config.DesktopConfig(
            port=0,
            token=desktop_config.default_token(),
            db_path=os.path.join(temp, "state.db"),
            mode="mock",
        )
        controller = GatewayController(config)
        try:
            port = controller.start(timeout=40)
            status = controller.status()
            report.update(
                {
                    "ok": status.get("simulation") is True,
                    "port": port,
                    "console_url": controller.console_url(),
                    "providers": [provider.get("name") for provider in status.get("providers", [])],
                    "models": [model.get("id") for model in controller.models()],
                }
            )
        finally:
            controller.stop()
        try:
            root = tk.Tk()
            root.withdraw()
            app = DesktopApp(root, config=config, controller=controller)
            root.update()
            if app.tray is not None:
                app.tray.stop()
            root.destroy()
            report["ui"] = True
        except tk.TclError as error:
            report["ui_error"] = str(error)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 3


def main(argv=None):
    parser = argparse.ArgumentParser(description="tokensfree desktop shell")
    parser.add_argument("--self-check", action="store_true", help="start a mock gateway, verify it, then exit")
    parser.add_argument("--config", default=None, help="path to the desktop config JSON")
    args = parser.parse_args(argv)
    if args.self_check:
        return run_self_check()
    config = desktop_config.load_config(args.config)
    desktop_config.save_config(config, args.config)
    root = tk.Tk()
    try:
        DesktopApp(root, config=config)
    except tk.TclError as error:
        print("cannot create the desktop window: %s" % error, file=sys.stderr)
        return 2
    root.mainloop()
    return 0
