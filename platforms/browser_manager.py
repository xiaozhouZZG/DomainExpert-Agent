"""Shared persistent Playwright browser manager for Goofish."""

from __future__ import annotations

import json
import logging
import os
import re
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from .base import PlatformNotReadyError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_PROFILE_LOCK_NAMES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
_MAX_LAUNCH_ATTEMPTS = 3
_PROFILE_REPAIR_AFTER_ATTEMPT = 2
_GOOFISH_AUTH_DOMAINS = (
    "goofish.com",
    "2.taobao.com",
    "taobao.com",
    "tmall.com",
    "alibaba.com",
    "alipay.com",
)
_singleton_lock = threading.Lock()
_goofish_browser_manager: Optional["BrowserManager"] = None


class BoundConnectProxy:
    """Small local CONNECT proxy that binds upstream sockets to one local IP."""

    def __init__(self, source_ip: str) -> None:
        self.source_ip = source_ip
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(64)
        self.port = int(self._server.getsockname()[1])
        self.server_url = f"http://127.0.0.1:{self.port}"
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="goofish-bound-proxy", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        try:
            self._server.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        with client:
            try:
                client.settimeout(15)
                data = b""
                while b"\r\n\r\n" not in data and len(data) < 65536:
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    data += chunk

                header, pending = data.split(b"\r\n\r\n", 1)
                first_line = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
                parts = first_line.split()
                if len(parts) < 3 or parts[0].upper() != "CONNECT" or ":" not in parts[1]:
                    client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                    return

                host, port_text = parts[1].rsplit(":", 1)
                upstream = socket.create_connection(
                    (host, int(port_text)),
                    timeout=20,
                    source_address=(self.source_ip, 0),
                )
                with upstream:
                    upstream.settimeout(None)
                    client.settimeout(None)
                    client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    if pending:
                        upstream.sendall(pending)
                    self._tunnel(client, upstream)
            except Exception as exc:
                logger.debug("Goofish bound proxy connection failed: %s", exc)

    @staticmethod
    def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 90)
            if errored or not readable:
                return
            for source in readable:
                target = upstream if source is client else client
                chunk = source.recv(16384)
                if not chunk:
                    return
                target.sendall(chunk)


class BrowserManager:
    """Process-wide persistent Chromium manager."""

    def __init__(
        self,
        *,
        user_data_dir: str,
        storage_state_path: str,
        headless: bool = False,
        slow_mo_ms: int = 500,
    ) -> None:
        self.user_data_dir = Path(user_data_dir).resolve()
        self.storage_state_path = Path(storage_state_path).resolve()
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._playwright = None
        self._context = None
        self._page = None
        self._bound_proxy: Optional[BoundConnectProxy] = None

    def with_page(self, action_name: str, callback: Callable[[Any], T]) -> T:
        """Execute callback with a page, ensuring all operations on worker thread."""
        # ✅ 确保所有playwright操作在worker线程执行，避免跨线程错误
        from platforms.browser_worker import get_browser_worker
        worker = get_browser_worker()

        def execute_on_worker():
            import threading
            logger.info(
                "✅ with_page executing on thread_id=%s thread_name=%s",
                threading.current_thread().ident,
                threading.current_thread().name
            )

            with self._lock:
                context = self._ensure_started_locked(action_name)
                page = self._ensure_page_locked(context)
                try:
                    return callback(page)
                except Exception as exc:
                    if _is_browser_closed_error(exc):
                        logger.warning(
                            "Goofish browser became unavailable during %s: %s",
                            action_name,
                            exc,
                        )
                        self._discard_runtime_locked()
                    raise
                finally:
                    self._save_storage_state_locked()

        # ✅ 通过worker执行，确保context在worker线程创建和使用
        return worker.execute(execute_on_worker)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def is_started(self) -> bool:
        with self._lock:
            return self._context_is_live_locked()

    def is_profile_locked(self) -> bool:
        """检测 profile 是否被其他活进程占用"""
        with self._lock:
            if self._context_is_live_locked():
                return False  # 自己在用，不算 locked
            owner_pids = self._profile_owner_pids_locked()
            return len(owner_pids) > 0

    def get_profile_owner_pids(self) -> list[int]:
        """获取占用 profile 的其他进程 PID"""
        with self._lock:
            if self._context_is_live_locked():
                return []
            return self._profile_owner_pids_locked()

    def force_restart(self) -> dict[str, Any]:
        """
        强制重启浏览器会话：
        1. 关闭当前 context/browser
        2. 杀掉占用 goofish_profile 的旧进程
        3. 清理 stale lock 文件
        4. 等待 2 秒
        5. 不自动启动新浏览器（等下次 with_page 时启动）
        """
        import time as _time
        with self._lock:
            # 1. 优雅关闭自己的 context
            self._stop_locked()
            logger.info("force_restart: 已关闭当前浏览器会话")

        # 2. 杀掉占用 profile 的旧进程
        killed_pids = self._kill_profile_owners()

        # 3. 清理 stale lock 文件
        removed = self._clear_stale_profile_locks_locked()

        # 4. 等 2 秒让进程完全退出
        _time.sleep(2)

        # 5. 再检查一次是否还有活进程
        remaining_pids = self._profile_owner_pids_locked()

        if remaining_pids:
            return {
                "status": "still_locked",
                "message": f"仍有进程占用 profile: PIDs {remaining_pids}，请手动关闭",
                "killed_pids": killed_pids,
                "removed_locks": removed,
                "remaining_pids": remaining_pids,
            }

        return {
            "status": "released",
            "message": "浏览器会话已重启，下次操作时自动启动新浏览器",
            "killed_pids": killed_pids,
            "removed_locks": removed,
        }

    def _kill_profile_owners(self) -> list[int]:
        """杀掉占用 goofish_profile 的其他进程（用 psutil）"""
        killed = []
        try:
            import psutil
        except ImportError:
            logger.warning("_kill_profile_owners: psutil 未安装，无法杀进程")
            return killed

        profile_str = str(self.user_data_dir).lower()
        current_pid = os.getpid()

        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']
                if pid == current_pid:
                    continue
                name = (proc.info['name'] or '').lower()
                # 只杀 Chrome/Chromium 进程
                if name not in ('chrome.exe', 'chromium.exe', 'chrome', 'chromium'):
                    continue
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                if profile_str in cmdline:
                    logger.info(f"_kill_profile_owners: 终止 PID={pid} name={proc.info['name']}")
                    proc.terminate()
                    killed.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.warning(f"_kill_profile_owners: 检查进程失败: {e}")

        # 等待 terminate 生效
        if killed:
            time.sleep(1)
            # 对还没退出的进程 kill
            for pid in killed:
                try:
                    p = psutil.Process(pid)
                    if p.is_running():
                        logger.info(f"_kill_profile_owners: 强制 kill PID={pid}")
                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        return killed

    def save_storage_state(self) -> None:
        with self._lock:
            self._save_storage_state_locked()

    def _ensure_started_locked(self, action_name: str):
        if self._context_is_live_locked():
            import threading
            logger.info(
                "✅ reusing Goofish browser context: action=%s context_id=%s page_id=%s thread_id=%s thread_name=%s",
                action_name,
                id(self._context),
                id(self._page) if self._page is not None else None,
                threading.current_thread().ident,
                threading.current_thread().name,
            )
            return self._context

        from playwright.sync_api import sync_playwright

        profile_repaired = False
        for attempt in range(1, _MAX_LAUNCH_ATTEMPTS + 1):
            owner_pids = self._prepare_profile_dir_locked()
            import threading
            logger.info(
                "✅ opening real browser for Goofish: action=%s attempt=%s/%s thread_id=%s thread_name=%s user_data_dir=%s storage_state=%s headed=%s slow_mo_ms=%s",
                action_name,
                attempt,
                _MAX_LAUNCH_ATTEMPTS,
                threading.current_thread().ident,
                threading.current_thread().name,
                self.user_data_dir,
                self.storage_state_path,
                not self.headless,
                self.slow_mo_ms,
            )

            try:
                self._playwright = sync_playwright().start()
                launch_kwargs: dict[str, Any] = {}
                proxy_url = self._ensure_bound_proxy_locked()
                if proxy_url:
                    launch_kwargs["proxy"] = {"server": proxy_url}
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.user_data_dir),
                    headless=self.headless,
                    slow_mo=self.slow_mo_ms,
                    **launch_kwargs,
                )
                self._page = None
                # ✅ 常驻context不需要每次重载storage_state（会导致localStorage 0/25）
                # 首次启动时，launch_persistent_context已自动加载user_data_dir的登录态
                # self._restore_storage_state_locked()
                self._save_storage_state_locked()
                logger.info(
                    "✅ Goofish browser context CREATED: action=%s context_id=%s pages=%s thread_id=%s thread_name=%s",
                    action_name,
                    id(self._context),
                    len(self._context.pages),
                    threading.current_thread().ident,
                    threading.current_thread().name,
                )
                return self._context
            except Exception as exc:
                message = str(exc)
                exit_code = _extract_exit_code(message)
                chromium_stderr = _extract_chromium_stderr(message)
                detail = _classify_launch_failure(exc, self.user_data_dir, owner_pids)
                logger.exception(
                    "failed to launch Goofish persistent browser: action=%s attempt=%s/%s detail=%s user_data_dir=%s exit_code=%s chromium_stderr=%s raw_error=%s",
                    action_name,
                    attempt,
                    _MAX_LAUNCH_ATTEMPTS,
                    detail,
                    self.user_data_dir,
                    exit_code,
                    chromium_stderr or "<unavailable>",
                    exc,
                )
                self._stop_locked()

                if attempt < _MAX_LAUNCH_ATTEMPTS and _is_retriable_launch_failure(message):
                    removed = self._clear_stale_profile_locks_locked()
                    logger.warning(
                        "retrying Goofish browser launch after cleaning stale profile locks: removed=%s detail=%s",
                        removed,
                        detail,
                    )
                    if (
                        not profile_repaired
                        and attempt >= _PROFILE_REPAIR_AFTER_ATTEMPT
                        and self._should_rebuild_profile_locked(message)
                    ):
                        backup_dir = self._backup_corrupt_profile_locked()
                        profile_repaired = True
                        logger.error(
                            "Goofish profile failed after lock cleanup and clean-profile probe passed; profile backed up before final retry: backup_dir=%s storage_state_preserved=%s",
                            backup_dir,
                            self.storage_state_path.exists(),
                        )
                    continue

                raise PlatformNotReadyError(detail) from exc

        raise PlatformNotReadyError(f"Failed to start Goofish browser: {self.user_data_dir}")

    def _ensure_page_locked(self, context):
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                logger.info("cached Goofish page handle is stale; reopening page")
                self._page = None

        for page in context.pages:
            try:
                if not page.is_closed():
                    self._page = page
                    return page
            except Exception:
                continue

        self._page = context.new_page()
        return self._page

    def _prepare_profile_dir_locked(self) -> list[int]:
        owner_pids = self._profile_owner_pids_locked()
        if owner_pids:
            # 返回详细错误，而不是直接抛异常，让调用方决定如何处理
            pids_str = ",".join(map(str, owner_pids))
            raise PlatformNotReadyError(
                f"profile_locked:{pids_str}"
            )

        self._clear_stale_profile_locks_locked()
        return owner_pids

    def _clear_stale_profile_locks_locked(self) -> list[str]:
        removed: list[str] = []
        lock_paths = [self.user_data_dir / name for name in _PROFILE_LOCK_NAMES if (self.user_data_dir / name).exists()]
        for lock_path in lock_paths:
            try:
                if lock_path.is_dir():
                    shutil.rmtree(lock_path)
                else:
                    lock_path.unlink()
                removed.append(str(lock_path))
                logger.info("removed stale Goofish profile lock: %s", lock_path)
            except FileNotFoundError:
                continue
            except Exception:
                logger.exception("failed to remove stale Goofish profile lock: %s", lock_path)
                raise PlatformNotReadyError(
                    f"Failed to clean stale browser profile lock: {lock_path}"
                )
        return removed

    def _profile_owner_pids_locked(self) -> list[int]:
        profile = str(self.user_data_dir).lower()
        profile_norm = _normalize_windows_path(profile)
        owners: list[int] = []
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                    "Get-CimInstance Win32_Process | "
                    "Select-Object ProcessId,CommandLine | "
                    "ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except Exception as exc:
            logger.warning("failed to inspect running processes for Goofish profile occupancy: %s", exc)
            return owners

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if result.returncode != 0 or not stdout.strip():
            logger.warning(
                "process inspection unavailable for Goofish profile occupancy: returncode=%s stderr=%s",
                result.returncode,
                stderr.strip(),
            )
            return owners

        try:
            payload = json.loads(stdout)
        except Exception:
            logger.warning("unable to parse PowerShell process inventory for Goofish profile occupancy")
            return owners

        entries = payload if isinstance(payload, list) else [payload]
        current_pid = os.getpid()
        for entry in entries:
            try:
                pid = int(entry.get("ProcessId"))
                cmdline = str(entry.get("CommandLine") or "").lower()
            except Exception:
                continue
            cmdline_norm = _normalize_windows_path(cmdline)
            if pid != current_pid and (profile in cmdline or profile_norm in cmdline_norm):
                owners.append(pid)
        return owners

    def _should_rebuild_profile_locked(self, launch_error_message: str) -> bool:
        if _is_confirmed_profile_corruption(launch_error_message):
            return True

        if not _is_retriable_launch_failure(launch_error_message):
            return False

        clean_probe = self._clean_profile_probe_locked()
        logger.warning(
            "Goofish clean-profile launch probe result: ok=%s detail=%s",
            clean_probe["ok"],
            clean_probe["detail"],
        )
        return bool(clean_probe["ok"])

    def _clean_profile_probe_locked(self) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory(
            prefix="goofish_profile_probe-",
            dir=str(self.user_data_dir.parent),
        ) as probe_dir:
            probe_path = Path(probe_dir).resolve()
            try:
                with sync_playwright() as probe_playwright:
                    context = probe_playwright.chromium.launch_persistent_context(
                        user_data_dir=str(probe_path),
                        headless=self.headless,
                        slow_mo=0,
                        **({"proxy": {"server": proxy_url}} if (proxy_url := self._ensure_bound_proxy_locked()) else {}),
                    )
                    context.close()
                return {"ok": True, "detail": f"clean profile launched: {probe_path}"}
            except Exception as exc:
                return {
                    "ok": False,
                    "detail": _classify_launch_failure(exc, probe_path, []),
                }

    def _ensure_bound_proxy_locked(self) -> Optional[str]:
        # ✅ 禁用代理，直连闲鱼（避免触发反爬）
        return None

        # 原代码已注释（如需恢复取消下面注释）
        # if os.getenv("GOOFISH_BROWSER_PROXY", "").strip().lower() in {"0", "false", "off", "no"}:
        #     return None
        #
        # if self._bound_proxy is not None:
        #     return self._bound_proxy.server_url
        #
        # source_ip = os.getenv("GOOFISH_BROWSER_BIND_IP", "").strip() or _detect_preferred_browser_source_ip()
        # if not source_ip:
        #     return None
        #
        # self._bound_proxy = BoundConnectProxy(source_ip)
        # logger.warning(
        #     "Goofish browser traffic is routed through a local bound proxy: proxy=%s source_ip=%s",
        #     self._bound_proxy.server_url,
        #     source_ip,
        # )
        # return self._bound_proxy.server_url

    def _context_is_live_locked(self) -> bool:
        if self._context is None:
            return False

        try:
            _ = len(self._context.pages)
            return True
        except Exception as exc:
            logger.warning("Goofish browser context is no longer live: %s", exc)
            self._discard_runtime_locked()
            return False

    def _save_storage_state_locked(self) -> None:
        if self._context is None:
            return

        tmp_path = self.storage_state_path.with_suffix(f"{self.storage_state_path.suffix}.tmp")
        try:
            self._context.storage_state(path=str(tmp_path))
            should_replace, reason = _should_replace_storage_state(self.storage_state_path, tmp_path)
            if not should_replace:
                logger.warning(
                    "skipped Goofish storage_state overwrite to avoid losing login state: reason=%s current=%s candidate=%s",
                    reason,
                    self.storage_state_path,
                    tmp_path,
                )
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                return

            if self.storage_state_path.exists():
                backup_path = self.storage_state_path.with_suffix(f"{self.storage_state_path.suffix}.bak")
                shutil.copy2(self.storage_state_path, backup_path)
            tmp_path.replace(self.storage_state_path)
        except Exception as exc:
            logger.warning("failed to save Goofish storage_state: %s", exc)
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            if _is_browser_closed_error(exc):
                self._discard_runtime_locked()

    def _restore_storage_state_locked(self) -> None:
        if self._context is None or not self.storage_state_path.exists():
            return

        try:
            state = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("failed to read Goofish storage_state for profile recovery: %s", exc)
            return

        cookies = state.get("cookies") or []
        if cookies:
            try:
                self._context.add_cookies(cookies)
                logger.info("restored %s Goofish cookies into persistent context", len(cookies))
            except Exception as exc:
                logger.warning("failed to restore Goofish cookies into persistent context: %s", exc)

        for origin_state in state.get("origins") or []:
            origin = origin_state.get("origin")
            local_storage = origin_state.get("localStorage") or []
            if not origin or not local_storage:
                continue
            try:
                state_json = json.dumps(
                    {"origin": origin, "localStorage": local_storage},
                    ensure_ascii=False,
                )
                self._context.add_init_script(
                    f"""
                    (() => {{
                      const state = {state_json};
                      if (location.origin !== state.origin) return;
                      for (const item of state.localStorage || []) {{
                        localStorage.setItem(item.name, item.value);
                      }}
                    }})();
                    """
                )
            except Exception as exc:
                logger.warning("failed to install localStorage restore script for %s: %s", origin, exc)

    def _backup_corrupt_profile_locked(self) -> Optional[str]:
        if not self.user_data_dir.exists():
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            return None

        backup_dir = self.user_data_dir.with_name(
            f"{self.user_data_dir.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.move(str(self.user_data_dir), str(backup_dir))
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        return str(backup_dir)

    def _discard_runtime_locked(self) -> None:
        context = self._context
        playwright = self._playwright
        bound_proxy = self._bound_proxy

        self._context = None
        self._page = None
        self._playwright = None
        self._bound_proxy = None

        if context is not None:
            try:
                context.close()
            except Exception:
                logger.info("Goofish browser context was already closed")

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                logger.info("Playwright runtime was already stopped")

        if bound_proxy is not None:
            bound_proxy.close()

    def _stop_locked(self) -> None:
        self._discard_runtime_locked()


def get_goofish_browser_manager(
    *,
    user_data_dir: str = "data/browser_state/goofish_profile",
    storage_state_path: str = "data/browser_state/goofish.json",
    headless: bool = False,
    slow_mo_ms: int = 500,
) -> BrowserManager:
    global _goofish_browser_manager

    with _singleton_lock:
        if _goofish_browser_manager is None:
            _goofish_browser_manager = BrowserManager(
                user_data_dir=user_data_dir,
                storage_state_path=storage_state_path,
                headless=headless,
                slow_mo_ms=slow_mo_ms,
            )
        return _goofish_browser_manager


def shutdown_goofish_browser_manager() -> None:
    global _goofish_browser_manager

    with _singleton_lock:
        manager = _goofish_browser_manager
        _goofish_browser_manager = None

    if manager is not None:
        manager.stop()


def summarize_storage_state(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    if not target.exists():
        return {
            "exists": False,
            "valid": False,
            "error": "storage_state file does not exist",
            "size": 0,
            "cookie_count": 0,
            "important_cookie_count": 0,
            "origin_count": 0,
            "local_storage_item_count": 0,
        }

    summary = _summarize_storage_state(target)
    summary["exists"] = True
    return summary


def _detect_preferred_browser_source_ip() -> Optional[str]:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$routes = Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
                "Select-Object ifIndex,InterfaceAlias,NextHop,RouteMetric,InterfaceMetric; "
                "$ips = Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } | "
                "Select-Object InterfaceIndex,IPAddress; "
                "[pscustomobject]@{ routes = $routes; ips = $ips } | ConvertTo-Json -Depth 5 -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except Exception as exc:
        logger.info("failed to inspect Windows network routes for Goofish browser proxy: %s", exc)
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        logger.info("failed to parse Windows route inventory for Goofish browser proxy: %s", exc)
        return None

    routes = payload.get("routes") or []
    ips = payload.get("ips") or []
    if isinstance(routes, dict):
        routes = [routes]
    if isinstance(ips, dict):
        ips = [ips]

    default_aliases = [str(route.get("InterfaceAlias") or "") for route in routes]
    has_virtual_default = any(
        token in alias.lower()
        for alias in default_aliases
        for token in ("vmware", "vethernet", "radmin", "tap")
    )
    if not has_virtual_default:
        return None

    ip_by_index = {
        int(item.get("InterfaceIndex")): str(item.get("IPAddress"))
        for item in ips
        if item.get("InterfaceIndex") is not None and item.get("IPAddress")
    }

    preferred_tokens = ("wlan", "wi-fi", "wifi", "\u4ee5\u592a\u7f51", "ethernet")
    for route in sorted(
        routes,
        key=lambda r: (int(r.get("RouteMetric") or 0), int(r.get("InterfaceMetric") or 0)),
    ):
        alias = str(route.get("InterfaceAlias") or "").lower()
        if any(token in alias for token in preferred_tokens):
            source_ip = ip_by_index.get(int(route.get("ifIndex")))
            if source_ip:
                return source_ip

    return None


def _classify_launch_failure(exc: Exception, user_data_dir: Path, owner_pids: list[int]) -> str:
    message = str(exc)
    exit_code = _extract_exit_code(message)
    chromium_stderr = _extract_chromium_stderr(message)

    if owner_pids:
        return (
            "Browser profile is already in use by live process(es) "
            f"{owner_pids}; please close them and retry: {user_data_dir}"
        )

    if "Executable doesn't exist" in message or "playwright install" in message.lower():
        return "Playwright browser is not installed; please run `playwright install`."

    if _looks_like_profile_occupancy(message):
        detail = (
            "Browser process exited or disconnected unexpectedly; please check whether the profile is occupied: "
            f"{user_data_dir}"
        )
        if exit_code is not None:
            detail = f"{detail} (exitCode={exit_code})"
        if chromium_stderr:
            detail = f"{detail}; chromium_stderr={chromium_stderr}"
        return detail

    if exit_code is not None:
        detail = f"Failed to start Goofish browser (exitCode={exit_code})."
        if chromium_stderr:
            detail = f"{detail} chromium_stderr={chromium_stderr}"
        return detail

    return f"Failed to start Goofish browser: {message}"


def _looks_like_profile_occupancy(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "targetclosederror",
            "target page, context or browser has been closed",
            "connection closed while reading from the driver",
            "browser has been closed",
            "browser process exited",
            "user data directory is already in use",
            "opening in existing browser session",
            "singletonlock",
            "singletonsocket",
            "singletoncookie",
            "exitcode=21",
        )
    )


def _extract_exit_code(message: str) -> Optional[int]:
    match = re.search(r"exitcode=(\d+)", message, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _is_browser_closed_error(exc: Exception) -> bool:
    return _looks_like_profile_occupancy(str(exc))


def _normalize_windows_path(value: str) -> str:
    return value.replace("/", "\\").replace('"', "").lower()


def _is_retriable_launch_failure(message: str) -> bool:
    return _looks_like_profile_occupancy(message)


def _is_confirmed_profile_corruption(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "profile error occurred",
            "profile cannot be used",
            "profile appears to be from a newer version",
            "failed to read preferences",
            "local state is corrupt",
            "preferences file is corrupt",
        )
    )


def _should_replace_storage_state(existing_path: Path, candidate_path: Path) -> tuple[bool, str]:
    candidate = _summarize_storage_state(candidate_path)
    if not candidate["valid"]:
        return False, f"candidate storage_state is invalid: {candidate['error']}"

    if not existing_path.exists():
        return True, "no existing storage_state"

    existing = _summarize_storage_state(existing_path)
    if not existing["valid"]:
        return True, f"existing storage_state is invalid: {existing['error']}"

    if existing["cookie_count"] > 0 and candidate["cookie_count"] == 0:
        return False, "candidate removed all cookies"

    if existing["important_cookie_count"] > 0 and candidate["important_cookie_count"] == 0:
        return False, "candidate removed all Goofish/Taobao auth-domain cookies"

    if (
        existing["important_cookie_count"] >= 4
        and candidate["important_cookie_count"] < max(1, existing["important_cookie_count"] // 2)
    ):
        return (
            False,
            "candidate dropped most Goofish/Taobao auth-domain cookies "
            f"({candidate['important_cookie_count']}/{existing['important_cookie_count']})",
        )

    if existing["cookie_count"] >= 12 and candidate["cookie_count"] < max(1, existing["cookie_count"] // 2):
        return (
            False,
            "candidate dropped most cookies "
            f"({candidate['cookie_count']}/{existing['cookie_count']})",
        )

    if (
        existing["local_storage_item_count"] >= 4
        and candidate["local_storage_item_count"] < max(1, existing["local_storage_item_count"] // 2)
    ):
        return (
            False,
            "candidate dropped most localStorage items "
            f"({candidate['local_storage_item_count']}/{existing['local_storage_item_count']})",
        )

    if existing["size"] >= 20_000 and candidate["size"] < max(1, existing["size"] // 3):
        return (
            False,
            "candidate storage_state is much smaller than existing state "
            f"({candidate['size']}/{existing['size']} bytes)",
        )

    return (
        True,
        "candidate accepted "
        f"cookies={candidate['cookie_count']} important_cookies={candidate['important_cookie_count']} "
        f"local_storage_items={candidate['local_storage_item_count']}",
    )


def _summarize_storage_state(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "valid": False,
        "error": None,
        "size": 0,
        "cookie_count": 0,
        "important_cookie_count": 0,
        "origin_count": 0,
        "local_storage_item_count": 0,
    }

    try:
        raw = path.read_text(encoding="utf-8")
        summary["size"] = len(raw.encode("utf-8"))
        state = json.loads(raw)
    except Exception as exc:
        summary["error"] = str(exc)
        return summary

    cookies = state.get("cookies") or []
    origins = state.get("origins") or []
    if not isinstance(cookies, list) or not isinstance(origins, list):
        summary["error"] = "storage_state has invalid cookies/origins shape"
        return summary

    summary["cookie_count"] = len(cookies)
    summary["important_cookie_count"] = sum(
        1
        for cookie in cookies
        if _is_goofish_auth_domain(str(cookie.get("domain") or ""))
    )
    summary["origin_count"] = len(origins)
    summary["local_storage_item_count"] = sum(
        len(origin.get("localStorage") or [])
        for origin in origins
        if isinstance(origin, dict)
    )
    summary["valid"] = True
    return summary


def _is_goofish_auth_domain(domain: str) -> bool:
    normalized = domain.lower().lstrip(".")
    return any(normalized == auth_domain or normalized.endswith(f".{auth_domain}") for auth_domain in _GOOFISH_AUTH_DOMAINS)


def _extract_chromium_stderr(message: str) -> Optional[str]:
    patterns = (
        r"<launching>.*?(?=\n[A-Za-z]+Type\.|$)",
        r"browser logs:\s*(.*)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
        if match:
            extracted = match.group(0 if pattern.startswith("<launching>") else 1)
            return " ".join(extracted.split())[:1000]
    return None
