"""登录失败节流器。

系统采用单主机、单 Uvicorn 进程部署，因此使用进程内有界状态即可在不新增
外部依赖的前提下阻断口令爆破。状态只保存用户名和客户端地址的摘要，不记录
明文凭据；服务重启会清空临时锁定，这符合本地办公系统的可恢复性要求。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

from .config import Settings, get_settings

logger = logging.getLogger("partyops.auth")


@dataclass
class _FailureState:
    failures: int
    window_started: float
    locked_until: float
    last_seen: float


class LoginThrottle:
    """按账号和客户端地址双维度限制连续登录失败。"""

    def __init__(self) -> None:
        self._states: dict[str, _FailureState] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(kind: str, value: str) -> str:
        normalized = value.strip().casefold()
        return hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()

    def _keys(self, username: str, client_address: str) -> tuple[str, str]:
        return (
            self._digest("account", username),
            self._digest("client", client_address or "unknown"),
        )

    def _prune(self, now: float, settings: Settings) -> None:
        expiry = max(settings.login_window_seconds, settings.login_lock_seconds) * 2
        stale = [
            key
            for key, state in self._states.items()
            if state.locked_until <= now and now - state.last_seen > expiry
        ]
        for key in stale:
            self._states.pop(key, None)
        overflow = len(self._states) - settings.login_throttle_max_entries
        if overflow > 0:
            # 攻击者可能在一个时间窗口内轮换大量伪造账号。按最后访问时间
            # 灌入记录；淘汰时必须优先丢弃未锁定、失败次数少的噪声，不能
            # 让随机用户名把真实账号仍生效的锁定状态挤出。
            eviction_order = sorted(
                self._states,
                key=lambda key: (
                    self._states[key].locked_until > now,
                    self._states[key].failures,
                    self._states[key].last_seen,
                ),
            )
            for key in eviction_order[:overflow]:
                self._states.pop(key, None)

    def retry_after(
        self,
        username: str,
        client_address: str,
        *,
        now: float | None = None,
        settings: Settings | None = None,
    ) -> int:
        current = time.monotonic() if now is None else now
        configured = settings or get_settings()
        with self._lock:
            self._prune(current, configured)
            remaining = [
                max(0.0, self._states[key].locked_until - current)
                for key in self._keys(username, client_address)
                if key in self._states
            ]
        return int(max(remaining, default=0.0) + 0.999)

    def record_failure(
        self,
        username: str,
        client_address: str,
        *,
        now: float | None = None,
        settings: Settings | None = None,
    ) -> int:
        current = time.monotonic() if now is None else now
        configured = settings or get_settings()
        account_key, client_key = self._keys(username, client_address)
        limits = {
            account_key: configured.login_account_failure_limit,
            client_key: configured.login_ip_failure_limit,
        }
        with self._lock:
            self._prune(current, configured)
            for key, limit in limits.items():
                state = self._states.get(key)
                if (
                    state is None
                    or current - state.window_started
                    >= configured.login_window_seconds
                ):
                    state = _FailureState(0, current, 0.0, current)
                state.failures += 1
                state.last_seen = current
                if state.failures >= limit:
                    state.locked_until = max(
                        state.locked_until,
                        current + configured.login_lock_seconds,
                    )
                self._states[key] = state
            self._prune(current, configured)
            retry = max(
                0.0,
                self._states[account_key].locked_until - current,
                self._states[client_key].locked_until - current,
            )
            failures = self._states[account_key].failures
        logger.warning(
            "login_failed account=%s client=%s failures=%s locked=%s",
            account_key[:12],
            client_key[:12],
            failures,
            retry > 0,
        )
        return int(retry + 0.999)

    def record_success(self, username: str) -> None:
        """成功登录只清除该账号计数，保留客户端喷洒攻击计数。"""

        with self._lock:
            self._states.pop(self._digest("account", username), None)

    def reset(self) -> None:
        """仅供隔离测试和受控诊断使用。"""

        with self._lock:
            self._states.clear()


login_throttle = LoginThrottle()
