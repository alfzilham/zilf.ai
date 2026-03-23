"""
NVIDIA NIM Provider — direct API calls per model
Each model has its own API key
"""
from __future__ import annotations
import os
import httpx
from agent.llm.base import BaseLLM, LLMResponse

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"

# Model ID → (nvidia_model_id, env_key_name)
NVIDIA_MODELS: dict[str, tuple[str, str]] = {
    "nvidia/qwen-3.5":          ("qwen/qwen3.5-72b",                           "NVIDIA_KEY_QWEN35"),
    "nvidia/glm-5":             ("zhipuai/glm-4-9b-chat",                       "NVIDIA_KEY_GLM5"),
    "nvidia/minimax-m25":       ("minimax/minimax-01",                          "NVIDIA_KEY_MINIMAX"),
    "nvidia/kimi-k2.5":         ("moonshot/moonshot-v1-8k",                     "NVIDIA_KEY_KIMI25"),
    "nvidia/stepfun-step3.5":   ("stepfun-ai/step-1-8k",                        "NVIDIA_KEY_STEPFUN"),
    "nvidia/mistral-small-4":   ("mistralai/mistral-small-latest",              "NVIDIA_KEY_MISTRAL"),
    "nvidia/qwen-397b":         ("qwen/qwen3.5-397b-a17b",                      "NVIDIA_KEY_QWEN397B"),
    "nvidia/deepseek-v3.2":     ("deepseek-ai/deepseek-v3",                     "NVIDIA_KEY_DEEPSEEK"),
    "nvidia/kimi-k2-thinking":  ("moonshot/moonshot-v1-8k",                     "NVIDIA_KEY_KIMI_THINK"),
    "nvidia/autogen":           ("nvidia/llama-3.1-nemotron-70b-instruct",      "NVIDIA_KEY_AUTOGEN"),
    "nvidia/nemotron-super-3":  ("nvidia/llama-3.3-nemotron-super-49b-v1",      "NVIDIA_KEY_NEMOTRON"),
}


class NvidiaLLM(BaseLLM):
    def __init__(self, model: str, max_tokens: int = 4096, temperature: float = 0.7) -> None:
        super().__init__(model=model, max_tokens=max_tokens, temperature=temperature)

        if model not in NVIDIA_MODELS:
            raise ValueError(f"Model '{model}' tidak dikenal. Pilih dari: {list(NVIDIA_MODELS.keys())}")

        self._nvidia_model, env_key = NVIDIA_MODELS[model]
        self._api_key = os.environ.get(env_key, "")

        if not self._api_key:
            raise RuntimeError(f"API key untuk {model} tidak ditemukan. Set env variable: {env_key}")

    async def generate_text(self, messages: list, system: str | None = None,
                            max_tokens: int = 4096, **kwargs) -> str:
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{NVIDIA_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._nvidia_model,
                    "messages": payload_messages,
                    "max_tokens": max_tokens,
                    "temperature": self.temperature,
                }
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def generate(self, messages, tools=None, system=None, **kwargs) -> LLMResponse:
        text = await self.generate_text(messages, system=system)
        return LLMResponse(
            thought=text, action_type="final_answer",
            tool_calls=[], final_answer=text, raw=text
        )

    async def stream(self, messages, system=None, **kwargs):
        text = await self.generate_text(messages, system=system)
        yield text