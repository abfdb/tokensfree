"""Tkinter control panel for the tokensfree local gateway (stdlib only)."""
import argparse
import dataclasses
import json
import os
import queue
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from tokensfree import profiles as provider_profiles
from tokensfree.desktop import config as desktop_config
from tokensfree.desktop import startup
from tokensfree.desktop.process import GatewayController, probe_endpoint

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
        self.root.geometry("980x780")
        self.root.minsize(860, 660)
        self._channels = []
        self._build()
        self._load_values()
        self._reload_channels()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    def _build(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        panel = ttk.Frame(notebook)
        notebook.add(panel, text="控制面板")
        self._build_panel(panel)
        channels = ttk.Frame(notebook)
        notebook.add(channels, text="接入通道")
        self._build_channels(channels)
    def _build_panel(self, panel):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(panel, padding=(12, 10))
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

        settings = ttk.LabelFrame(panel, text="设置", padding=(12, 8))
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
        self.allow_paid_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="允许自费通道", variable=self.allow_paid_var).pack(side="left")

        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="数据库").pack(side="left")
        self.db_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.db_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="选择…", command=self.browse_db).pack(side="left")

        status_frame = ttk.LabelFrame(panel, text="运行时状态", padding=(8, 6))
        status_frame.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.tree = ttk.Treeview(status_frame, columns=("kind", "name", "detail"), show="headings", height=7)
        for column, title, width in (("kind", "类型", 90), ("name", "名称", 160), ("detail", "详情", 520)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(panel, text="网关日志", padding=(8, 6))
        log_frame.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap="none", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(panel, padding=(12, 8))
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
        self.allow_paid_var.set(self.config.allow_user_paid)

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
            allow_user_paid=bool(self.allow_paid_var.get()),
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

    def _build_channels(self, parent):
        toolbar = ttk.Frame(parent, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="新增通道", command=self._new_channel).pack(side="left")
        ttk.Button(toolbar, text="编辑", command=self._edit_channel).pack(side="left", padx=4)
        ttk.Button(toolbar, text="删除", command=self._delete_channel).pack(side="left")
        ttk.Button(toolbar, text="启用/停用", command=self._toggle_channel).pack(side="left", padx=4)
        ttk.Button(toolbar, text="测试连接", command=self._test_channel).pack(side="left")
        ttk.Button(toolbar, text="刷新", command=self._reload_channels).pack(side="right")
        self.channels_tree = ttk.Treeview(
            parent,
            columns=("name", "url", "model", "policy", "enabled"),
            show="headings",
            height=12,
        )
        for column, title, width in (
            ("name", "名称", 140),
            ("url", "Base URL", 300),
            ("model", "模型", 150),
            ("policy", "成本策略", 120),
            ("enabled", "启用", 60),
        ):
            self.channels_tree.heading(column, text=title)
            self.channels_tree.column(column, width=width, anchor="w")
        self.channels_tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        ttk.Label(
            parent,
            padding=(10, 0, 10, 10),
            text="密钥保存在 Windows 凭据管理器（tokensfree/<通道 id>/env-key）；providers.json 只存引用。",
        ).pack(fill="x")

    def _profiles_path(self):
        return self.config.profiles_path or str(provider_profiles.default_profiles_path())

    def _reload_channels(self):
        try:
            self._channels = provider_profiles.load_profiles(self._profiles_path())
        except provider_profiles.ProfileError as error:
            self._channels = []
            self.hint_var.set("接入通道读取失败：%s" % error)
        self.channels_tree.delete(*self.channels_tree.get_children())
        for profile in self._channels:
            self.channels_tree.insert(
                "",
                "end",
                iid=profile.id,
                values=(
                    profile.name,
                    profile.base_url,
                    profile.model,
                    profile.cost_policy,
                    "是" if profile.enabled else "否",
                ),
            )

    def _selected_profile(self):
        selection = self.channels_tree.selection()
        if not selection:
            return None
        wanted = selection[0]
        for profile in self._channels:
            if profile.id == wanted:
                return profile
        return None

    def _new_channel(self):
        self._channel_dialog(None)

    def _edit_channel(self):
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("接入通道", "请先选择一个通道")
            return
        self._channel_dialog(profile)

    def _delete_channel(self):
        profile = self._selected_profile()
        if profile is None:
            return
        if not messagebox.askyesno("删除通道", "删除 %s？同时会删除其保存的凭据。" % profile.name):
            return
        remaining = provider_profiles.remove_profile(self._channels, profile.id)
        try:
            provider_profiles.save_profiles(remaining, self._profiles_path())
            provider_profiles.delete_key(profile.id)
        except Exception as error:
            messagebox.showerror("删除失败", str(error))
            return
        self._reload_channels()

    def _toggle_channel(self):
        profile = self._selected_profile()
        if profile is None:
            return
        updated = dataclasses.replace(profile, enabled=not profile.enabled)
        try:
            profiles_list = provider_profiles.upsert_profile(self._channels, updated)
            provider_profiles.save_profiles(profiles_list, self._profiles_path())
        except provider_profiles.ProfileError as error:
            messagebox.showerror("保存失败", str(error))
            return
        self._reload_channels()

    def _test_channel(self):
        profile = self._selected_profile()
        if profile is None:
            return
        try:
            key = "" if profile.cost_policy == "zero_cost_local" else provider_profiles.load_key(profile.id)
        except Exception as error:
            if profile.cost_policy == "zero_cost_local":
                key = ""
            else:
                messagebox.showerror("读取凭据失败", str(error))
                return
        self.hint_var.set("正在测试 %s ..." % profile.name)
        self._run_async(
            lambda: probe_endpoint(profile.base_url, key),
            lambda result: messagebox.showinfo(
                "连接成功",
                "%s\n模型：%s" % (profile.base_url, ", ".join(result[0][:10]) or "(空列表)"),
            ),
            lambda error: messagebox.showerror("连接失败", str(error)),
        )

    def _pick_snapshot(self, variable):
        chosen = filedialog.askopenfilename(
            title="选择证据快照", filetypes=[("JSON", "*.json"), ("All files", "*.*")]
        )
        if chosen:
            variable.set(chosen)

    def _channel_dialog(self, profile):
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑通道" if profile else "新增通道")
        dialog.transient(self.root)
        dialog.grab_set()
        evidence = profile.evidence or {} if profile else {}
        form = {
            "name": tk.StringVar(value=profile.name if profile else ""),
            "base_url": tk.StringVar(value=profile.base_url if profile else "http://127.0.0.1:11434/v1"),
            "model": tk.StringVar(value=profile.model if profile else ""),
            "policy": tk.StringVar(value=profile.cost_policy if profile else "zero_cost_local"),
            "snapshot": tk.StringVar(value=str(evidence.get("snapshot", ""))),
            "expires": tk.StringVar(value=str(evidence.get("expires_at", ""))),
            "key": tk.StringVar(value=""),
            "enabled": tk.BooleanVar(value=profile.enabled if profile else True),
        }
        for index, (label, field) in enumerate(("名称", "name"), ("Base URL", "base_url"), ("模型", "model")):
            ttk.Label(dialog, text=label).grid(row=index, column=0, sticky="w", padx=(12, 6), pady=4)
            ttk.Entry(dialog, textvariable=form[field], width=54).grid(
                row=index, column=1, columnspan=2, sticky="we", pady=4
            )
        ttk.Label(dialog, text="成本策略").grid(row=3, column=0, sticky="w", padx=(12, 6), pady=4)
        ttk.Combobox(
            dialog,
            textvariable=form["policy"],
            values=list(provider_profiles.COST_POLICIES),
            width=22,
            state="readonly",
        ).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(dialog, text="能力").grid(row=4, column=0, sticky="w", padx=(12, 6), pady=4)
        caps = ttk.Frame(dialog)
        caps.grid(row=4, column=1, columnspan=2, sticky="w", pady=4)
        selected = profile.capabilities if profile else frozenset({"text", "stream"})
        for name in provider_profiles.CAPABILITY_NAMES:
            variable = tk.BooleanVar(value=name in selected)
            setattr(dialog, "cap_" + name, variable)
            ttk.Checkbutton(caps, text=name, variable=variable).pack(side="left", padx=(0, 10))
        ttk.Label(dialog, text="证据快照").grid(row=5, column=0, sticky="w", padx=(12, 6), pady=4)
        ttk.Entry(dialog, textvariable=form["snapshot"], width=42).grid(row=5, column=1, sticky="we", pady=4)
        ttk.Button(dialog, text="选择…", command=lambda: self._pick_snapshot(form["snapshot"])).grid(
            row=5, column=2, sticky="w", padx=6
        )
        ttk.Label(dialog, text="或到期时间").grid(row=6, column=0, sticky="w", padx=(12, 6), pady=4)
        ttk.Entry(dialog, textvariable=form["expires"], width=22).grid(row=6, column=1, sticky="w", pady=4)
        ttk.Label(dialog, text="API Key").grid(row=7, column=0, sticky="w", padx=(12, 6), pady=4)
        ttk.Entry(dialog, textvariable=form["key"], width=54, show="*").grid(
            row=7, column=1, columnspan=2, sticky="we", pady=4
        )
        ttk.Label(
            dialog,
            text="留空表示不修改；本地通道可空。写入 Windows 凭据管理器。",
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 4))
        ttk.Checkbutton(dialog, text="启用", variable=form["enabled"]).grid(
            row=9, column=0, sticky="w", padx=12, pady=4
        )
        buttons = ttk.Frame(dialog)
        buttons.grid(row=10, column=0, columnspan=3, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(buttons, text="保存", command=lambda: self._save_channel_dialog(dialog, profile, form)).pack(side="right")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=6)
        dialog.columnconfigure(1, weight=1)

    def _save_channel_dialog(self, dialog, profile, form):
        capabilities = [
            name for name in provider_profiles.CAPABILITY_NAMES
            if getattr(dialog, "cap_" + name).get()
        ]
        values = {
            "name": form["name"].get(),
            "base_url": form["base_url"].get(),
            "model": form["model"].get(),
            "cost_policy": form["policy"].get(),
            "capabilities": capabilities,
            "evidence_snapshot": form["snapshot"].get(),
            "evidence_expires_at": form["expires"].get(),
            "enabled": bool(form["enabled"].get()),
        }
        try:
            updated = provider_profiles.profile_from_form(
                profile_id=profile.id if profile else None, form=values
            )
            profiles_list = provider_profiles.upsert_profile(self._channels, updated)
            provider_profiles.save_profiles(profiles_list, self._profiles_path())
            if form["key"].get().strip():
                provider_profiles.save_key(updated.id, form["key"].get().strip())
        except Exception as error:
            messagebox.showerror("保存失败", str(error))
            return
        dialog.destroy()
        self._reload_channels()
        self.hint_var.set("通道已保存：%s" % updated.name)

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
    parser.add_argument("--start-gateway", action="store_true", help="start the gateway when the panel opens")
    args = parser.parse_args(argv)
    if args.self_check:
        return run_self_check()
    config = desktop_config.load_config(args.config)
    desktop_config.save_config(config, args.config)
    root = tk.Tk()
    try:
        app = DesktopApp(root, config=config)
        if args.start_gateway:
            root.after(200, app.start_gateway)
    except tk.TclError as error:
        print("cannot create the desktop window: %s" % error, file=sys.stderr)
        return 2
    root.mainloop()
    return 0
