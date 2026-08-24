import asyncio
import logging
import requests
from enum import Enum
from typing import Dict, List, Any, Optional
from openai import OpenAI
from src.core.config import config

logger = logging.getLogger("content_engine.llm_router")

class TaskType(Enum):
    PLANNING = "planning"           # Content strategy, weekly evolution
    CREATIVE_WRITING = "writing"    # Captions, scripts, tweets
    SPEED_BATCH = "speed"           # Fast hashtags, categorization
    QA_SCORING = "qa"              # Quality gate scoring
    SIMPLE = "simple"              # Basic extraction, structure conversion

class LLMRouter:
    def __init__(self):
        # Initialize Google GenAI configuration
        self.gemini_api_key = config.GEMINI_API_KEY
            
        # Initialize Groq Client (using OpenAI SDK for unified interface)
        self.groq_client = None
        if config.GROQ_API_KEY:
            self.groq_client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=config.GROQ_API_KEY,
                timeout=120.0
            )
            
        # Initialize OpenRouter Client
        self.openrouter_client = None
        if config.OPENROUTER_API_KEY:
            self.openrouter_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=config.OPENROUTER_API_KEY,
                default_headers={
                    "HTTP-Referer": "https://github.com/google-deepmind/content-engine",
                    "X-Title": "AI Content Engine"
                },
                timeout=120.0
            )

        # Initialize Nous Research Inference API client (OpenAI-compatible, NO rate limit on select/free models)
        self.nous_client = None
        # Cycle through Nous' always-free models so no single one gets rate-limited.
        self.nous_models = [
            "tencent/hy3:free",
            "poolside/laguna-s-2.1:free",
            "poolside/laguna-xs-2.1:free",
            "stepfun/step-3.7-flash:free",
        ]
        self.nous_idx = 0
        if config.NOUS_API_KEY:
            self.nous_client = OpenAI(
                base_url="https://inference-api.nousresearch.com/v1",
                api_key=config.NOUS_API_KEY,
                timeout=300.0  # long script generations (16-turn debates) can take 1-3 min on free models
            )

        # Basic local fallback check
        self.providers = {
            "nous": self.nous_client is not None,
            "gemini": self.gemini_api_key is not None,
            "groq": self.groq_client is not None,
            "openrouter": self.openrouter_client is not None
        }
        
        # Simple tracking for basic rate limiting/cool-down
        self.cool_downs = {p: 0 for p in self.providers}

    def _get_provider_for_task(self, task: TaskType) -> List[str]:
        """Returns ordered list of preferred providers for each task type.

        Nous is PRIMARY (no daily rate limit; cycles hy3/laguna/step free models).
        Gemini is last-resort fallback (it throttles + has daily limits, so we avoid
        hammering it). Groq/OpenRouter kept last (their model slugs are currently
        broken/deprecated but harmless to attempt).
        """
        order = ["nous", "gemini", "groq", "openrouter"]
        return order

    async def _call_gemini_rest(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.7) -> str:
        """Call Google Gemini REST API directly using requests to avoid Pydantic dependency conflicts.
        Uses gemini-flash-latest as default due to wider free-tier quota activation on standard API keys.
        """
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={self.gemini_api_key}"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": temperature
            }
        }
        
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=120)
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"Gemini REST call failed ({response.status_code}): {response.text}")
            
        data = response.json()
        try:
            if "candidates" in data and data["candidates"]:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    return candidate["content"]["parts"][0]["text"]
                elif "finishReason" in candidate:
                    reason = candidate["finishReason"]
                    raise RuntimeError(f"Gemini generation failed due to finish reason: {reason}. Full response: {data}")
            elif "promptFeedback" in data:
                reason = data["promptFeedback"].get("blockReason")
                raise RuntimeError(f"Gemini prompt blocked due to: {reason}. Full response: {data}")
                
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Invalid response structure from Gemini API: {data}")

    async def _call_nous(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.7) -> str:
        """Call Nous Research Inference API (OpenAI-compatible). Primary text provider,
        cycles through always-free models (hy3 / laguna-s / laguna-xs / step-3.7-flash)
        so no single model gets rate-limited. Uses tencent/hy3:free as the starting point."""
        if not self.nous_client:
            raise RuntimeError("Nous client not configured")
        loop = asyncio.get_event_loop()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        # Round-robin to the next free model each call.
        model = self.nous_models[self.nous_idx % len(self.nous_models)]
        self.nous_idx += 1
        response = await loop.run_in_executor(
            None,
            lambda: self.nous_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature
            )
        )
        return response.choices[0].message.content

    async def generate_vision(self, prompt: str, image_path: str, mime_type: str = "image/png") -> str:
        """Call Google Gemini REST API directly with an image file using requests.
        Uses gemini-flash-latest with automatic model fallback for maximum reliability.
        """
        import base64
        import os
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is not configured. Cannot perform Visual QA.")
            
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found at path: {image_path}")

        # Read and encode image to base64
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")
            
        # Sequence of vision-capable models to try in case of 503/429 overloads
        models_to_try = [
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite"
        ]
        
        last_error = None
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_api_key}"
            
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_image
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2
                }
            }
            
            logger.info(f"Attempting Vision QA API call using model: {model}...")
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=120)
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if "candidates" in data and data["candidates"]:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            return candidate["content"]["parts"][0]["text"]
                    raise RuntimeError(f"Invalid vision response structure from {model}: {data}")
                else:
                    logger.warning(f"Vision call to {model} returned status {response.status_code}: {response.text[:200]}")
                    last_error = RuntimeError(f"REST call returned {response.status_code}: {response.text[:200]}")
            except Exception as e:
                logger.warning(f"Vision call to {model} failed: {str(e)}")
                last_error = e
                
        raise RuntimeError(f"All Gemini Vision models failed. Last error: {str(last_error)}")

    async def generate(self, prompt: str, system_prompt: Optional[str] = None, task: TaskType = TaskType.SIMPLE, temperature: float = 0.7) -> str:
        """Asynchronously call the best available LLM with automatic fallback."""
        preferred_providers = self._get_provider_for_task(task)
        
        # Filter active providers
        active_providers = [p for p in preferred_providers if self.providers.get(p) is True]
        
        if not active_providers:
            raise RuntimeError("No LLM providers are configured in the env file.")

        last_error = None
        for provider in active_providers:
            if self.cool_downs[provider] > 0:
                self.cool_downs[provider] -= 1
                continue
                
            try:
                logger.info(f"Routing task {task.value} to provider: {provider}")
                if provider == "gemini":
                    return await self._call_gemini_rest(prompt, system_prompt, temperature)

                elif provider == "nous":
                    return await self._call_nous(prompt, system_prompt, temperature)

                elif provider == "groq":
                    # Groq OpenAI-compatible call
                    loop = asyncio.get_event_loop()
                    
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})
                    
                    # Using the standard versatile model (70B) which replaced decommissioned specdec
                    response = await loop.run_in_executor(
                        None,
                        lambda: self.groq_client.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=messages,
                            temperature=temperature
                        )
                    )
                    return response.choices[0].message.content
                    
                elif provider == "openrouter":
                    # OpenRouter OpenAI-compatible call
                    loop = asyncio.get_event_loop()
                    
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})
                    
                    # Using Llama 3.1 8B Instruct free model which is highly active and stable
                    response = await loop.run_in_executor(
                        None,
                        lambda: self.openrouter_client.chat.completions.create(
                            model="meta-llama/llama-3.1-8b-instruct:free",
                            messages=messages,
                            temperature=temperature
                        )
                    )
                    return response.choices[0].message.content

            except Exception as e:
                logger.warning(f"Provider {provider} failed with error: {str(e)}. Attempting fallback...")
                last_error = e
                # Set cool-down for this provider
                self.cool_downs[provider] = 3
                continue
                
        raise RuntimeError(f"All LLM providers failed. Last error: {str(last_error)}")

# Singleton instance
router = LLMRouter()
