"""
LLM 客户端封装 🤖✨

将 LLM 调用逻辑从业务代码中分离出来，提供统一的接口。
支持异步调用、并发控制、重试和超时。
"""

import asyncio
import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from .llm_config import get_llm_config, get_default_model


def _coerce_json(content: str) -> Dict[str, Any]:
    """
    尝试从 LLM 输出中提取 JSON 🎯
    
    处理 LLM 可能返回的带格式的 JSON（如 markdown 代码块）
    """
    # 移除首尾空白
    content = content.strip()
    
    # 尝试直接解析
    try:
        return json.loads(content)
    except Exception:
        pass
    
    # 尝试移除 markdown 代码块标记
    # 处理 ```json ... ``` 或 ``` ... ```
    # 匹配 ```json 或 ``` 开头的代码块
    json_block_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    match = re.search(json_block_pattern, content, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        try:
            return json.loads(json_str)
        except Exception:
            pass
    
    # 尝试截取最外层 JSON（从第一个 { 到最后一个 }）
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = content[start : end + 1]
        try:
            return json.loads(json_str)
        except Exception as e:
            # 如果 JSON 不完整，尝试修复常见的截断问题
            # 例如：缺少闭合括号、缺失字段、字符串值被截断等
            try:
                # 🔧 改进的修复策略：逐字符检查，找到最后一个完整的键值对
                # 策略：从后往前找，找到最后一个完整的字段（以引号、null、true、false、数字、}、] 结尾）
                
                # 先尝试找到最后一个完整的字符串值
                # 字符串值应该以 " 开头，以 " 结尾（考虑转义）
                fixed_json = json_str
                
                # 检查是否有未闭合的字符串（从后往前找最后一个引号）
                # 如果最后一个引号前面没有匹配的开头引号，说明字符串被截断了
                last_quote_idx = fixed_json.rfind('"')
                if last_quote_idx != -1:
                    # 检查这个引号是否是转义的
                    escape_count = 0
                    i = last_quote_idx - 1
                    while i >= 0 and fixed_json[i] == '\\':
                        escape_count += 1
                        i -= 1
                    
                    # 如果引号不是转义的（偶数个反斜杠），说明可能是字符串的结尾
                    # 但如果前面没有匹配的开头引号，说明字符串被截断了
                    if escape_count % 2 == 0:
                        # 从后往前找，找到这个字符串的开头
                        # 字符串应该以 :" 或 ," 开头
                        search_start = max(0, last_quote_idx - 200)  # 只搜索最近200个字符
                        value_start = fixed_json.rfind(':"', search_start, last_quote_idx)
                        if value_start == -1:
                            value_start = fixed_json.rfind(',"', search_start, last_quote_idx)
                        
                        if value_start == -1:
                            # 如果找不到字符串的开头，说明字符串被截断了
                            # 找到最后一个完整的键值对（以逗号结尾的完整字段）
                            # 策略：找到最后一个完整的字段，移除之后的不完整内容
                            lines = fixed_json.split('\n')
                            fixed_lines = []
                            
                            for i, line in enumerate(lines):
                                stripped = line.strip()
                                # 检查这一行是否完整
                                if stripped.startswith('"'):
                                    if ':' in stripped:
                                        # 检查值是否完整
                                        colon_idx = stripped.find(':')
                                        value_part = stripped[colon_idx+1:].strip()
                                        
                                        # 如果值不完整（没有以引号、null、true、false、数字、}、] 结尾），说明被截断了
                                        value_clean = value_part.rstrip(',').strip()
                                        
                                        # 检查字符串值是否完整（以引号结尾，且不是转义的）
                                        is_complete = False
                                        if value_clean.startswith('"'):
                                            # 字符串值：检查是否以引号结尾（考虑转义）
                                            if value_clean.endswith('"') and value_clean.count('"') >= 2:
                                                # 检查最后一个引号是否是转义的
                                                last_quote_in_value = value_clean.rfind('"')
                                                if last_quote_in_value > 0:
                                                    escape_count = 0
                                                    j = last_quote_in_value - 1
                                                    while j >= 0 and value_clean[j] == '\\':
                                                        escape_count += 1
                                                        j -= 1
                                                    if escape_count % 2 == 0:
                                                        is_complete = True
                                        elif value_clean in ['null', 'true', 'false']:
                                            is_complete = True
                                        elif value_clean.replace('.', '').replace('-', '').isdigit():
                                            is_complete = True
                                        elif value_clean.endswith('}') or value_clean.endswith(']'):
                                            is_complete = True
                                        
                                        if not is_complete:
                                            # 这一行不完整，停止添加
                                            break
                                    
                                    # 如果键不完整（没有 :），也停止
                                    elif ':' not in stripped and not stripped.endswith('{') and not stripped.endswith('['):
                                        break
                                
                                fixed_lines.append(line)
                            
                            fixed_json = '\n'.join(fixed_lines)
                            # 移除末尾的逗号（如果有）
                            fixed_json = fixed_json.rstrip().rstrip(',')
                            
                            # 🔧 处理字符串值被截断的情况
                            # 如果最后一个字符不是引号、}、]，说明字符串值可能被截断了
                            # 找到最后一个不完整的字符串值，移除它
                            last_char = fixed_json.rstrip()[-1] if fixed_json.rstrip() else ''
                            if last_char not in ['"', '}', ']', 'e', 'l', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
                                # 可能是字符串值被截断了，找到最后一个完整的字段
                                # 策略：找到最后一个以逗号结尾的完整字段
                                last_comma = fixed_json.rfind(',')
                                if last_comma != -1:
                                    # 检查逗号之前的内容是否完整
                                    before_comma = fixed_json[:last_comma].rstrip()
                                    # 如果逗号之前以 } 或 ] 结尾，说明是完整的
                                    if before_comma.endswith('}') or before_comma.endswith(']'):
                                        fixed_json = before_comma
                                    # 如果逗号之前以引号结尾（且不是转义的），说明字符串值完整
                                    elif before_comma.endswith('"'):
                                        # 检查最后一个引号是否是转义的
                                        quote_idx = len(before_comma) - 1
                                        escape_count = 0
                                        i = quote_idx - 1
                                        while i >= 0 and before_comma[i] == '\\':
                                            escape_count += 1
                                            i -= 1
                                        if escape_count % 2 == 0:
                                            # 引号不是转义的，字符串值完整
                                            fixed_json = before_comma
                                        else:
                                            # 引号是转义的，字符串值不完整，移除这个字段
                                            # 找到这个字段的开头（上一个逗号或 {）
                                            prev_comma = before_comma.rfind(',')
                                            prev_brace = before_comma.rfind('{')
                                            cut_point = max(prev_comma, prev_brace)
                                            if cut_point != -1:
                                                fixed_json = fixed_json[:cut_point].rstrip().rstrip(',')
                                            else:
                                                # 如果找不到，说明整个对象都不完整，移除最后一个字段
                                                fixed_json = before_comma
                                else:
                                    # 没有逗号，说明可能只有一个字段，且不完整
                                    # 找到最后一个 { 或 [，移除之后的内容
                                    last_brace = fixed_json.rfind('{')
                                    last_bracket = fixed_json.rfind('[')
                                    cut_point = max(last_brace, last_bracket)
                                    if cut_point != -1:
                                        fixed_json = fixed_json[:cut_point+1]
                            
                            # 如果最后一个字符是引号，但字符串不完整，移除它
                            if fixed_json.rstrip().endswith('"'):
                                # 检查引号是否是转义的
                                last_quote_idx = len(fixed_json.rstrip()) - 1
                                escape_count = 0
                                i = last_quote_idx - 1
                                while i >= 0 and fixed_json[i] == '\\':
                                    escape_count += 1
                                    i -= 1
                                
                                # 如果引号不是转义的，检查前面是否有匹配的开头引号
                                if escape_count % 2 == 0:
                                    # 检查前面是否有 :" 或 ,"
                                    search_start = max(0, last_quote_idx - 200)
                                    value_start = fixed_json.rfind(':"', search_start, last_quote_idx)
                                    if value_start == -1:
                                        value_start = fixed_json.rfind(',"', search_start, last_quote_idx)
                                    
                                    if value_start == -1:
                                        # 找不到字符串的开头，说明字符串被截断了，移除最后一个引号
                                        fixed_json = fixed_json[:last_quote_idx].rstrip().rstrip(',')
                            
                            # 检查并补全缺失的字段
                            if '"is_obstacle"' not in fixed_json and '"state_changes"' in fixed_json:
                                # 在最后一个对象中添加缺失的字段
                                # 找到最后一个 } 的位置
                                last_brace = fixed_json.rfind('}')
                                if last_brace != -1:
                                    # 在最后一个 } 之前添加缺失的字段
                                    fixed_json = fixed_json[:last_brace] + ',\n            "is_obstacle": false\n        }' + fixed_json[last_brace+1:]
                            
                            # 补全对象的闭合括号
                            while fixed_json.count('{') > fixed_json.count('}'):
                                fixed_json += '\n        }'
                            # 补全数组的闭合括号
                            while fixed_json.count('[') > fixed_json.count(']'):
                                fixed_json += '\n    ]'
                            # 补全最外层对象的闭合括号
                            if fixed_json.count('{') > fixed_json.count('}'):
                                fixed_json += '\n}'
                            
                            return json.loads(fixed_json)
            except Exception:
                pass
    
    # 如果都失败了，返回错误信息
    preview = content[:300] if len(content) > 300 else content
    raise ValueError(f"无法从 LLM 输出中提取 JSON。预览: {preview}")


class AsyncLLMClient:
    """
    异步 LLM 客户端封装类 🚀
    
    统一管理 LLM 调用，支持异步、并发控制、重试、超时等。
    """
    
    def __init__(
        self,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_concurrent: int | None = None,
    ):
        """
        初始化异步 LLM 客户端
        
        Args:
            model_name: 模型配置名称（如 "deepseek", "gpt-4o"），会从配置中读取 base_url、api_key 等
            base_url: API 基础 URL（如果提供，会覆盖配置中的值）
            api_key: API Key（如果提供，会覆盖配置中的值）
            model: 模型名称（如果提供，会覆盖配置中的值）
            temperature: 温度参数（如果提供，会覆盖配置中的默认值）
            max_tokens: 最大 token 数（如果提供，会覆盖配置中的默认值）
            timeout: 请求超时时间（秒，如果提供，会覆盖配置中的默认值）
            max_retries: 最大重试次数（如果提供，会覆盖配置中的默认值）
            max_concurrent: 最大并发数（如果提供，会覆盖配置中的默认值）
        """
        # 如果没有指定 model_name，使用默认模型
        if model_name is None:
            model_name = get_default_model()
        
        # 从配置中读取参数
        config = get_llm_config(model_name)
        self._provider = config.get("provider", "openai")
        
        self.base_url = base_url or config["base_url"]
        self.api_key = api_key or config.get("api_key")
        self.model = model or config["model"]
        self.temperature = temperature if temperature is not None else config["default_temperature"]
        self.max_tokens = max_tokens if max_tokens is not None else config["default_max_tokens"]
        self.timeout = timeout if timeout is not None else config["default_timeout"]
        self.max_retries = max_retries if max_retries is not None else config["default_max_retries"]
        max_concurrent_val = max_concurrent if max_concurrent is not None else config["default_max_concurrent"]
        
        # 如果没有 API Key，使用 Stub 模式
        self.use_stub = not bool(self.api_key)
        
        if not self.use_stub and self._provider not in ("gemini", "claude"):
            self.client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,  # 我们自己实现重试逻辑
            )
        else:
            self.client = None
        
        # 创建 Semaphore 控制并发
        self.semaphore = asyncio.Semaphore(max_concurrent_val)
    
    async def invoke(
        self,
        prompt: str,
        return_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any] | str:
        """
        异步调用 LLM 生成内容
        
        在 Semaphore 保护下执行，支持重试和超时控制。
        
        Args:
            prompt: 提示词
            return_json: 是否尝试解析为 JSON
            tools: 函数定义列表（用于 function calling）
            tool_choice: 工具选择策略（"auto", "none", 或 {"type": "function", "function": {"name": "函数名"}}）
            
        Returns:
            JSON 字典（如果 return_json=True）或原始字符串
            如果使用了 function calling，返回包含 tool_calls 的字典
        """
        if self.use_stub:
            # Stub 模式：返回空结果
            return {} if return_json else ""
        
        # 在 Semaphore 保护下执行
        async with self.semaphore:
            last_error: Exception | None = None
            
            for attempt in range(1, self.max_retries + 1):
                try:
                    # 🔍 调试：记录重试信息
                    if attempt > 1:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"🔍 [DEBUG] LLM 调用重试第 {attempt} 次（共 {self.max_retries} 次）...")
                    
                    # 使用 asyncio.wait_for 实现超时控制
                    result = await asyncio.wait_for(
                        self._call_api(prompt, tools=tools, tool_choice=tool_choice),
                        timeout=self.timeout
                    )
                    
                    # 🔍 调试：记录成功响应
                    if attempt > 1:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"🔍 [DEBUG] LLM 调用在第 {attempt} 次重试后成功")
                    
                    if return_json:
                        return _coerce_json(result)
                    return result
                    
                except asyncio.TimeoutError:
                    last_error = TimeoutError(f"请求超时（{self.timeout}s）")
                    if attempt < self.max_retries:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"⚠️  LLM 调用超时，{1.0 * attempt} 秒后重试（第 {attempt}/{self.max_retries} 次）...")
                        await asyncio.sleep(1.0 * attempt)  # 指数退避
                    else:
                        raise RuntimeError(f"LLM 调用失败（超时）: {last_error}")
                        
                except Exception as e:
                    last_error = e
                    if attempt < self.max_retries:
                        # 指数退避重试；503/429/高负载或网络断连时用更长退避
                        wait_time = 1.0 * (2 ** (attempt - 1))
                        err_str = str(e).lower()
                        is_transient = (
                            "503" in err_str or "429" in err_str or "unavailable" in err_str or "high demand" in err_str
                            or "10054" in err_str or "urlopen error" in err_str
                            or "connection reset" in err_str or "econnreset" in err_str or "connection refused" in err_str
                            or "connection error" in err_str or "apiconnectionerror" in err_str
                            or "temporary failure" in err_str or "timed out" in err_str
                        )
                        if is_transient:
                            wait_time = max(wait_time, 8.0 * (2 ** (attempt - 1)))  # 至少 8s, 16s, 32s…
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"⚠️  LLM 调用出错: {e}，{wait_time:.0f} 秒后重试（第 {attempt}/{self.max_retries} 次）...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise RuntimeError(f"LLM 调用失败（重试 {self.max_retries} 次后）: {e}")
            
            # 理论上不会到这里，但为了类型检查
            raise RuntimeError(f"LLM 调用失败: {last_error}")
    
    async def _call_api(
        self,
        prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> str:
        """
        实际调用 API 的内部方法
        
        Args:
            prompt: 提示词
            tools: 函数定义列表（用于 function calling）
            tool_choice: 工具选择策略
            
        Returns:
            LLM 返回的文本内容或函数调用信息
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"🔍 [DEBUG] 调用 LLM API: model={self.model}, prompt_length={len(prompt)}, max_tokens={self.max_tokens}")

        if self._provider == "gemini":
            return await self._call_gemini_api(prompt, tools, tool_choice, logger)
        if self._provider == "claude":
            return await self._call_claude_api(prompt, tools, tool_choice, logger)

        # OpenAI 兼容路径
        request_params = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice or "auto"

        try:
            response = await self.client.chat.completions.create(**request_params)
        except Exception as e:
            logger.error(f"❌ LLM API 调用失败: {e}")
            logger.error(f"   错误类型: {type(e).__name__}")
            raise
        
        if not response.choices or not response.choices[0].message:
            logger.warning(f"⚠️  LLM 响应为空（没有 choices 或 message）")
            raise ValueError("LLM 返回空响应")
        
        message = response.choices[0].message
        finish_reason = getattr(response.choices[0], 'finish_reason', 'unknown')
        logger.debug(f"🔍 [DEBUG] LLM 响应: content_length={len(message.content) if message.content else 0}, finish_reason={finish_reason}")
        
        # 🔍 如果 finish_reason 是 "length"，说明响应被截断了
        if finish_reason == "length":
            logger.warning(f"⚠️  LLM 响应被截断（达到 max_tokens 限制: {self.max_tokens}），可能需要增加 max_tokens")
        
        # 如果 LLM 调用了函数，返回函数调用信息
        if message.tool_calls:
            # 返回函数调用的 JSON 格式
            tool_calls = []
            for tool_call in message.tool_calls:
                tool_calls.append({
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                })
            return json.dumps({"tool_calls": tool_calls}, ensure_ascii=False)
        
        # 否则返回文本内容
        return message.content or ""

    def _gemini_sync_request(self, url: str, body: bytes, timeout: float) -> dict:
        """同步发起 Gemini REST 请求（供 asyncio.to_thread 调用）"""
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    async def _call_gemini_api(
        self,
        prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
        logger: Any,
    ) -> str:
        """调用 Google Gemini generateContent API（密钥仅从环境变量 GEMINI_API_KEY 读取）"""
        if tools:
            logger.warning("⚠️ Gemini 路径暂不支持 function calling，将忽略 tools 仅生成文本")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, self._gemini_sync_request, url, body, self.timeout),
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else str(e)
            logger.error(f"❌ Gemini API 调用失败: {e.code} {e.reason} {err_body}")
            raise RuntimeError(f"Gemini API 错误: {e.code} {err_body}") from e
        except Exception as e:
            logger.error(f"❌ Gemini API 调用失败: {e}")
            raise

        candidates = raw.get("candidates") or []
        if not candidates:
            logger.warning("⚠️ Gemini 返回无 candidates")
            raise ValueError("LLM 返回空响应")
        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            return ""
        return parts[0].get("text", "") or ""

    def _claude_sync_request(self, url: str, body: bytes, timeout: float) -> dict:
        """同步发起 Anthropic Claude REST 请求（供 asyncio.to_thread 调用）"""
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    async def _call_claude_api(
        self,
        prompt: str,
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
        logger: Any,
    ) -> str:
        """调用 Anthropic Claude Messages API（密钥仅从环境变量 ANTHROPIC_API_KEY 读取）"""
        if tools:
            logger.warning("⚠️ Claude 路径暂不支持 function calling，将忽略 tools 仅生成文本")
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, self._claude_sync_request, url, body, self.timeout),
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else str(e)
            logger.error(f"❌ Claude API 调用失败: {e.code} {e.reason} {err_body}")
            raise RuntimeError(f"Claude API 错误: {e.code} {err_body}") from e
        except Exception as e:
            logger.error(f"❌ Claude API 调用失败: {e}")
            raise

        content_blocks = raw.get("content") or []
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text" and b.get("text")]
        return "".join(text_parts) if text_parts else ""
    
    @staticmethod
    def create_default(model_name: str | None = None) -> "AsyncLLMClient":
        """
        创建默认的异步 LLM 客户端
        
        Args:
            model_name: 模型配置名称（如 "deepseek", "gpt-4o"），如果不提供则从环境变量 LLM_MODEL 读取，默认为 "deepseek"
        
        Returns:
            AsyncLLMClient 实例
        """
        return AsyncLLMClient(model_name=model_name)