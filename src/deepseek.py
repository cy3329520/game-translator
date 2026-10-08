"""DeepSeek Responses API 客户端。

一次请求同时完成「截图 OCR」和「日译中」——不再需要本地 OCR 引擎。
接口：POST https://api.deepseek.com/responses
"""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Callable

import requests
from PIL import Image

from .config import Config


class ApiError(Exception):
    """带分类信息的接口异常，方便界面给出可读的提示。"""

    def __init__(self, message: str, kind: str = "unknown", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


# HTTP 状态码 -> 用户能看懂的中文提示
_STATUS_HINTS = {
    400: "请求被拒绝（图片或参数不合法）",
    401: "API Key 无效或未授权",
    402: "账户余额不足",
    403: "无权访问该模型",
    404: "接口地址不存在，请检查 Base URL",
    422: "请求参数校验失败",
    429: "请求过于频繁，已被限速",
    500: "DeepSeek 服务端内部错误",
    502: "DeepSeek 网关错误",
    503: "DeepSeek 服务暂时不可用",
    504: "DeepSeek 服务响应超时",
}


def short_network_error(exc: Exception) -> str:
    """requests 的异常信息又长又啰嗦，提炼成一句人能看懂的。"""
    text = str(exc)
    lowered = text.lower()
    if "connection refused" in lowered:
        return "连接被拒绝 —— 接口地址不对，或本机没有对应服务"
    if "nodename nor servname" in lowered or "name or service not known" in lowered:
        return "域名解析失败，请检查网络或代理设置"
    if "timed out" in lowered or "timeout" in lowered:
        return "连接超时，请检查网络或代理设置"
    if "certificate" in lowered or "ssl" in lowered:
        return "TLS / 证书校验失败"
    return text[:140]


def encode_image(image: Image.Image, max_width: int = 1280, quality: int = 82) -> str:
    """把 PIL 图像压成 base64 JPEG data URL，控制体积与 token 消耗。"""
    img = image
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max_width and img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, max(1, int(round(img.height * ratio)))), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(quality), optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


class Translator:
    """DeepSeek 视觉翻译客户端（无状态，每次请求独立）。"""

    def __init__(self, cfg: Config, timeout: int = 60):
        self.cfg = cfg
        self.timeout = timeout
        self._session = requests.Session()

    # ------------------------------------------------------------------
    def build_payload(self, image: Image.Image) -> dict:
        cfg = self.cfg
        data_url = encode_image(image, cfg.image_max_width, cfg.image_quality)
        return {
            "model": cfg.model,
            "reasoning": {"effort": cfg.reasoning_effort},
            "max_output_tokens": int(cfg.max_output_tokens),
            "instructions": cfg.system_prompt,
            "stream": True,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "请识别并翻译这张游戏截图中的日文。只输出译文。"},
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": cfg.image_detail,
                        },
                    ],
                }
            ],
        }

    # ------------------------------------------------------------------
    def translate(
        self,
        image: Image.Image,
        on_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict:
        """翻译一张截图。

        返回 dict: {"text": 译文, "usage": {...}, "elapsed": 秒, "first_token": 秒或 None}
        """
        cfg = self.cfg
        if not (cfg.api_key or "").strip():
            raise ApiError("还没有填写 DeepSeek API Key", kind="no_key")

        payload = self.build_payload(image)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {cfg.api_key.strip()}",
        }

        started = time.time()
        first_token: float | None = None
        chunks: list[str] = []
        usage: dict = {}
        failure: dict | None = None
        last_event_type = ""

        try:
            resp = self._session.post(
                cfg.endpoint,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(10, self.timeout),
            )
        except requests.exceptions.ConnectTimeout:
            raise ApiError(f"连接 {cfg.base_url} 超时，请检查网络或代理", kind="network")
        except requests.exceptions.RequestException as exc:
            raise ApiError(f"无法连接 {cfg.base_url}：{short_network_error(exc)}",
                           kind="network")

        with resp:
            if resp.status_code != 200:
                raise ApiError(self._read_error(resp), kind="http", status=resp.status_code)

            for raw in resp.iter_lines(decode_unicode=True):
                if should_stop is not None and should_stop():
                    break
                if not raw:
                    continue
                if raw.startswith("event:"):
                    last_event_type = raw[len("event:"):].strip()
                    continue
                if not raw.startswith("data:"):
                    continue

                data = raw[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type") or last_event_type

                if etype == "response.output_text.delta":
                    delta = event.get("delta") or ""
                    if delta:
                        if first_token is None:
                            first_token = time.time() - started
                        chunks.append(delta)
                        if on_delta is not None:
                            on_delta("".join(chunks))

                elif etype == "response.completed":
                    response = event.get("response") or {}
                    usage = response.get("usage") or {}
                    text = self._extract_text(response)
                    if text:
                        chunks = [text]

                elif etype == "response.incomplete":
                    response = event.get("response") or {}
                    usage = response.get("usage") or {}
                    details = response.get("incomplete_details") or {}
                    reason = details.get("reason") or "unknown"
                    text = self._extract_text(response)
                    if text:
                        chunks = [text]
                    if reason == "max_output_tokens":
                        chunks.append("\n[输出被截断：可调大 max_output_tokens]")

                elif etype == "response.failed":
                    response = event.get("response") or {}
                    failure = response.get("error") or {"message": "响应失败"}

        if failure is not None:
            message = failure.get("message") or str(failure)
            raise ApiError(f"模型返回失败：{message}", kind="model")

        return {
            "text": "".join(chunks).strip(),
            "usage": usage,
            "elapsed": time.time() - started,
            "first_token": first_token,
        }

    # ------------------------------------------------------------------
    def ping(self) -> str:
        """轻量连通性测试：不传图片，只验证 Key / 网络 / 接口可用。"""
        cfg = self.cfg
        if not (cfg.api_key or "").strip():
            raise ApiError("还没有填写 DeepSeek API Key", kind="no_key")

        payload = {
            "model": cfg.model,
            "reasoning": {"effort": "none"},
            "max_output_tokens": 32,
            "instructions": "你是一个连通性测试助手。",
            "input": "请只回复两个字：正常",
        }
        try:
            resp = self._session.post(
                cfg.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {cfg.api_key.strip()}",
                },
                json=payload,
                timeout=(10, 30),
            )
        except requests.exceptions.RequestException as exc:
            raise ApiError(f"无法连接 {cfg.base_url}：{short_network_error(exc)}",
                           kind="network")

        if resp.status_code != 200:
            raise ApiError(self._read_error(resp), kind="http", status=resp.status_code)

        try:
            return self._extract_text(resp.json()) or "（返回内容为空）"
        except (ValueError, json.JSONDecodeError):
            raise ApiError("返回内容无法解析", kind="model")

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text(response: dict) -> str:
        parts: list[str] = []
        for item in response.get("output") or []:
            if item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text"):
                    parts.append(block.get("text") or "")
        return "".join(parts).strip()

    @staticmethod
    def _read_error(resp: requests.Response) -> str:
        hint = _STATUS_HINTS.get(resp.status_code, f"HTTP {resp.status_code}")
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    detail = err.get("message") or ""
                detail = detail or body.get("message") or ""
            if not detail:
                detail = json.dumps(body, ensure_ascii=False)[:300]
        except (ValueError, json.JSONDecodeError):
            detail = (resp.text or "")[:300]
        detail = detail.strip()
        return f"{hint}：{detail}" if detail else hint
