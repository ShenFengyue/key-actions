"""AI 助手：配置、请求与发送日志。

隐私红线（对应 docs/AI-功能草案.md）：
    - 只在用户主动点「发送」时联网；没配置 Key 或关掉开关时，应用完全离线可用；
    - 只发送：本次输入的文字、本会话的历史消息、设置里填写的人设提示词；
    - **绝不**发送数据库、活动备注汇总、日记、备份或导出文件；
    - Key 明文保存在项目内的 `data/ai.json`，不进 Git、不写日志；
    - `data/logs/ai.log` 只记时间、模型、字节数、成败与错误摘要，不记 Key、不记正文。

本模块是全项目**唯一**允许使用网络库的模块（tests/test_core.py 有白名单检查）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import clock, paths

AI_CONFIG_PATH = paths.DATA_DIR / "ai.json"
AI_LOG_PATH = paths.LOG_DIR / "ai.log"

DEFAULTS: dict[str, Any] = {
    # 默认关闭：没填 Key 的人不应该被任何联网行为打扰
    "enabled": False,
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "",
    "temperature": 0.7,
    "system_prompt": "你是一个简洁、直接的中文助手。回答尽量短，不说套话。",
}

DEFAULT_TIMEOUT = 60.0


class AIError(RuntimeError):
    """请求失败：消息可以直接展示给用户。"""


class AIConfig:
    """data/ai.json 的读写（明文保存，页面里会写明这一点）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else AI_CONFIG_PATH
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            self._data.update({k: v for k, v in raw.items() if k in DEFAULTS})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @property
    def ready(self) -> bool:
        """配置完整且开关打开时才算可用。"""
        return bool(
            self.get("enabled")
            and str(self.get("api_key") or "").strip()
            and str(self.get("base_url") or "").strip()
            and str(self.get("model") or "").strip()
        )


def endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("还没有填写接口地址。")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def build_payload(
    config: AIConfig,
    messages: list[dict[str, str]],
    *,
    system_hint: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """构造请求体：只有对话消息（+ 设置里的人设 + 程序自带的格式约定），不带任何本地数据。"""
    prepared: list[dict[str, str]] = []
    system_prompt = str(config.get("system_prompt") or "").strip()
    if system_prompt:
        prepared.append({"role": "system", "content": system_prompt})
    hint = str(system_hint or "").strip()
    if hint:
        prepared.append({"role": "system", "content": hint})
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            prepared.append({"role": role, "content": content})
    payload: dict[str, Any] = {
        "model": str(config.get("model") or "").strip(),
        "messages": prepared,
        "temperature": float(config.get("temperature") or 0.7),
        "stream": False,
    }
    payload.update(extra)
    return payload


def _log(ok: bool, model: str, size: int, detail: str = "") -> None:
    """发送日志：不记 Key、不记正文。"""
    try:
        AI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{clock.stamp()} | {'ok' if ok else 'fail'} | model={model or '-'}"
            f" | bytes={size} | {detail}\n"
        )
        with AI_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def chat(
    config: AIConfig,
    messages: list[dict[str, str]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    system_hint: str = "",
    **extra: Any,
) -> str:
    """调用 OpenAI 兼容的 /chat/completions，返回助手回复文本。"""
    if not str(config.get("api_key") or "").strip():
        raise AIError("还没有填写 API Key（在「设置」里填）。")

    url = endpoint(str(config.get("base_url") or ""))
    payload = build_payload(config, messages, system_hint=system_hint, **extra)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {str(config.get('api_key')).strip()}",
        },
    )
    model = str(config.get("model") or "")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - 读取错误体失败不影响主流程
            detail = ""
        _log(False, model, len(body), f"http {exc.code}")
        raise AIError(f"接口返回 {exc.code}：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        _log(False, model, len(body), f"network {exc.reason}")
        raise AIError(f"网络不通：{exc.reason}") from exc
    except TimeoutError as exc:
        _log(False, model, len(body), "timeout")
        raise AIError("请求超时，稍后再试。") from exc

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        _log(False, model, len(body), "bad response")
        raise AIError(f"返回格式看不懂：{raw[:300]}") from exc

    _log(True, model, len(body), f"reply={len(content or '')}c")
    return str(content or "").strip()


def test_connection(config: AIConfig, timeout: float = 30.0) -> str:
    """设置页的「测试连接」：发一句极短的问候，返回回复。"""
    reply = chat(
        config,
        [{"role": "user", "content": "只回复两个字：可用"}],
        timeout=timeout,
        max_tokens=8,
    )
    return reply
