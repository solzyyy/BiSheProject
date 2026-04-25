from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
from typing import Callable


_READY_FINGERPRINT: str | None = None
_READY_ENV_KEY = "NEO4J_READY_FINGERPRINT"


def _resolve_connection_env() -> tuple[str, str, str]:
    """补齐 Neo4j 连接环境变量，并返回当前连接三元组。"""
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
    os.environ.setdefault("NEO4J_USER", "neo4j")
    os.environ.setdefault("NEO4J_PASSWORD", "password")
    return (
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", "password"),
    )


def _build_env_fingerprint(uri: str, user: str, password: str) -> str:
    """基于当前连接参数构造稳定指纹，用于跨进程复用「已就绪」状态。"""
    raw = f"{uri}\0{user}\0{password}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def _startup_candidates() -> list[list[str]]:
    """获取可用的 Neo4j 启动命令列表。"""
    configured = (os.environ.get("NEO4J_START_COMMAND") or "").strip()
    candidates: list[list[str]] = []
    if configured:
        candidates.append(shlex.split(configured, posix=os.name != "nt"))
    # 勿写死本机绝对路径；请在本机配置 PATH 中的 `neo4j`，或设置 NEO4J_START_COMMAND。
    candidates.append(["neo4j", "console"])
    return candidates


def _mark_ready(fingerprint: str) -> None:
    global _READY_FINGERPRINT
    _READY_FINGERPRINT = fingerprint
    os.environ[_READY_ENV_KEY] = fingerprint


def ensure_neo4j_ready(
    *,
    auto_start: bool = False,
    reporter: Callable[[str], None] | None = print,
    retry_attempts: int = 15,
    retry_interval_seconds: float = 2.0,
) -> None:
    """确保 Neo4j 已可连接；必要时自动拉起，并缓存成功状态。
    
    Args:
        auto_start: 若为 True，连接失败时尝试自动启动 Neo4j；
                   若为 False (默认)，连接失败时直接抛异常（用于 Streamlit 启动进行快速检查）。
    """
    uri_from_env = os.environ.get("NEO4J_URI")
    user_from_env = os.environ.get("NEO4J_USER")
    pw_from_env = os.environ.get("NEO4J_PASSWORD")

    uri, user, password = _resolve_connection_env()
    fingerprint = _build_env_fingerprint(uri, user, password)
    if _READY_FINGERPRINT == fingerprint:
        return
    if os.environ.get(_READY_ENV_KEY) == fingerprint:
        _mark_ready(fingerprint)
        return

    def emit(message: str) -> None:
        if reporter is not None:
            reporter(message)

    from .neo4j_client import Neo4jClient

    emit(f"[cyan]Neo4j 登录预检查[/cyan] (URI={uri}, USER={user})")
    if pw_from_env is None:
        emit(
            "[yellow]提示：NEO4J_PASSWORD 未设置，当前将使用默认 password（很可能导致 unauthorized）。[/yellow]"
        )
    if user_from_env is None:
        emit("[yellow]提示：NEO4J_USER 未设置，当前将使用默认 neo4j。[/yellow]")

    # 静默探测：检查 Neo4j 是否已在运行
    client = None
    try:
        client = Neo4jClient(quiet=True)
        _mark_ready(fingerprint)
        emit("[green]Neo4j 已就绪。[/green]")
        return
    except Exception as exc:
        if not auto_start:
            emit(f"[red]❌ Neo4j 连接失败: {exc}[/red]")
            raise
        # auto_start=True：静默跳过，直接进入自动启动流程
    finally:
        if client is not None:
            client.close()

    # --- 自动启动 Neo4j ---
    emit("[cyan]Neo4j 未运行，正在尝试自动启动...[/cyan]")
    started = False
    for cmd in _startup_candidates():
        try:
            emit(f"[cyan]尝试自动启动：{' '.join(cmd)}[/cyan]")
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            started = True
            break
        except Exception:
            continue

    if not started:
        raise RuntimeError(
            "自动启动 Neo4j 失败：未找到可用的 neo4j 启动命令。"
        )

    last_exc: Exception | None = None
    for i in range(retry_attempts):
        time.sleep(retry_interval_seconds)
        retry_client = None
        try:
            retry_client = Neo4jClient(quiet=True)
            _mark_ready(fingerprint)
            emit("[green]Neo4j 启动成功，连接已就绪。[/green]")
            last_exc = None
            return
        except Exception as retry_exc:
            last_exc = retry_exc
        finally:
            if retry_client is not None:
                retry_client.close()

    raise RuntimeError(f"Neo4j 启动后仍连接失败：{last_exc}")


__all__ = ["ensure_neo4j_ready"]