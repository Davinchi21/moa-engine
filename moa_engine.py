import asyncio
import hashlib
import json
import os
import random
import re
import sqlite3
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

import httpx
import yaml


# ═══════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════

@dataclass
class ModelConfig:
    name: str
    provider: str
    model: str
    key_env: str
    base_url: Optional[str] = None
    weight: float = 1.0
    temperature: float = 0.7
    max_tokens: int = 4096

@dataclass
class MoAResult:
    final_answer: str
    raw_answers: dict
    scores: dict
    layer1_outputs: list = field(default_factory=list)
    tokens_used: int = 0
    latency: float = 0.0
    models_used: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
#  CONFIG LOADER
# ═══════════════════════════════════════════════════════════

class Config:
    def __init__(self, path="moa_config.yaml"):
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.general = raw.get("general", {})
        self.proposers = [ModelConfig(**p) for p in raw.get("proposers", [])]
        self.aggregators_l1 = [ModelConfig(**a) for a in raw.get("aggregators_layer1", [])]
        final_raw = raw.get("final_aggregator", {}).copy()
        self.final_self_critique = final_raw.pop("self_critique", False)
        self.final = ModelConfig(**final_raw)
        self.quality_cfg = raw.get("quality", {})
        self.cache_cfg = raw.get("cache", {})
        self.judge = ModelConfig(**self.quality_cfg.get("judge_model", {})) if self.quality_cfg.get("judge_model") else None

    @property
    def top_k_layer1(self):
        return self.quality_cfg.get("top_k_layer1", 2)

    @property
    def metrics(self):
        return self.quality_cfg.get("metrics", {})


# ═══════════════════════════════════════════════════════════
#  PROVIDERS (адаптеры к разным API)
# ═══════════════════════════════════════════════════════════

class ProviderError(Exception):
    pass

class OpenAICompatible:
    """Groq, OpenRouter, DeepSeek, etc."""
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.api_key = os.getenv(cfg.key_env, "")

    async def chat(self, messages: list, temperature=None, max_tokens=None) -> str:
        if not self.api_key:
            raise ProviderError(f"Missing env var: {self.cfg.key_env}")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.cfg.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/moa-system"
            headers["X-Title"] = "MoA-Engine"

        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature or self.cfg.temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
        }

        async with httpx.AsyncClient(timeout=120) as c:
            last_err = None
            for attempt in range(3):
                try:
                    r = await c.post(
                        f"{self.cfg.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    if r.status_code == 429:
                        retry_sec = 10
                        try:
                            ra = r.headers.get("Retry-After") or r.headers.get("X-RateLimit-Reset", "")
                            retry_sec = max(int(float(ra)), 5) if ra else retry_sec
                        except (ValueError, TypeError):
                            retry_sec = (2 ** attempt) * 5
                        if retry_sec > 30:
                            raise ProviderError(
                                f"{self.cfg.name}: daily limit ({retry_sec}s). "
                                f"Add $10 credit on OpenRouter for 1000 req/day"
                            )
                        if attempt == 2:
                            raise ProviderError(f"{self.cfg.name}: rate limited, skip")
                        print(f"  [429] {self.cfg.name}: retry {attempt+1}/3, wait {retry_sec}s")
                        await asyncio.sleep(retry_sec)
                        continue
                    r.raise_for_status()
                    data = r.json()
                    return data["choices"][0]["message"]["content"]
                except ProviderError:
                    raise
                except Exception as e:
                    last_err = e
                    if attempt == 2:
                        raise ProviderError(f"{self.cfg.name}: {e}")
                    await asyncio.sleep(2 ** attempt)
            raise ProviderError(f"{self.cfg.name}: {last_err or 'all retries exhausted'}")

class GeminiProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.api_key = os.getenv(cfg.key_env, "")

    async def chat(self, messages: list, temperature=None, max_tokens=None) -> str:
        if not self.api_key:
            raise ProviderError(f"Missing env var: {self.cfg.key_env}")
        # конвертируем OpenAI-формат в Gemini
        system = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                contents.append({"role": m["role"], "parts": [{"text": m["content"]}]})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.cfg.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature or self.cfg.temperature,
                "maxOutputTokens": max_tokens or self.cfg.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient(timeout=120) as c:
            last_err = None
            for attempt in range(3):
                try:
                    r = await c.post(url, json=payload)
                    if r.status_code == 429:
                        retry_sec = (2 ** attempt) * 5
                        if attempt == 2:
                            raise ProviderError(f"{self.cfg.name}: rate limited, skip")
                        print(f"  [429] {self.cfg.name}: retry {attempt+1}/3, wait {retry_sec}s")
                        await asyncio.sleep(retry_sec)
                        continue
                    r.raise_for_status()
                    data = r.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except ProviderError:
                    raise
                except Exception as e:
                    last_err = e
                    if attempt == 4:
                        raise ProviderError(f"{self.cfg.name}: {e}")
                    await asyncio.sleep(2 ** attempt)
            raise ProviderError(f"{self.cfg.name}: {last_err or 'all retries exhausted'}")
            raise ProviderError(f"{self.cfg.name}: all retries exhausted")

class HuggingFaceProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.api_key = os.getenv(cfg.key_env, "")

    async def chat(self, messages: list, temperature=None, max_tokens=None) -> str:
        if not self.api_key:
            raise ProviderError(f"Missing env var: {self.cfg.key_env}")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        prompt = self._build_prompt(messages)
        payload = {
            "inputs": prompt,
            "parameters": {
                "temperature": temperature or self.cfg.temperature,
                "max_new_tokens": min(max_tokens or self.cfg.max_tokens, 2048),
                "return_full_text": False,
            },
        }
        url = f"https://api-inference.huggingface.co/models/{self.cfg.model}"

        async with httpx.AsyncClient(timeout=120) as c:
            last_err = None
            for attempt in range(5):
                try:
                    r = await c.post(url, json=payload, headers=headers)
                    if r.status_code == 503:
                        if attempt == 4:
                            raise ProviderError(f"{self.cfg.name}: model loading after 5 retries")
                        await asyncio.sleep(5 + attempt * 3)
                        continue
                    r.raise_for_status()
                    data = r.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0].get("generated_text", "")
                    return str(data)
                except ProviderError:
                    raise
                except Exception as e:
                    last_err = e
                    if attempt == 4:
                        raise ProviderError(f"{self.cfg.name}: {e}")
                    await asyncio.sleep(2)
            raise ProviderError(f"{self.cfg.name}: {last_err or 'all retries exhausted'}")

    def _build_prompt(self, messages):
        prompt = ""
        for m in messages:
            if m["role"] == "system":
                prompt += f"<|system|>\n{m['content']}\n"
            elif m["role"] == "user":
                prompt += f"<|user|>\n{m['content']}\n"
            elif m["role"] == "assistant":
                prompt += f"<|assistant|>\n{m['content']}\n"
        prompt += "<|assistant|>\n"
        return prompt


def build_provider(cfg: ModelConfig):
    if cfg.provider == "gemini":
        return GeminiProvider(cfg)
    elif cfg.provider == "huggingface":
        return HuggingFaceProvider(cfg)
    else:
        return OpenAICompatible(cfg)


# ═══════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════

class MoACache:
    def __init__(self, cfg: dict):
        self.enabled = cfg.get("enabled", True)
        self.ttl = cfg.get("ttl_seconds", 3600)
        backend = cfg.get("backend", "memory")
        if backend == "sqlite":
            self._db = sqlite3.connect("moa_cache.db", check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, ts REAL)")
        else:
            self._db = None
            self._store = {}

    def _make_key(self, prompt: str, system: str, models: list) -> str:
        raw = f"{prompt}|{system}|{sorted(models)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, prompt: str, system: str, models: list) -> Optional[str]:
        if not self.enabled:
            return None
        key = self._make_key(prompt, system, models)
        if self._db:
            row = self._db.execute("SELECT value, ts FROM cache WHERE key=?", (key,)).fetchone()
            if row and time.time() - row[1] < self.ttl:
                return row[0]
        else:
            val, ts = self._store.get(key, (None, 0))
            if val and time.time() - ts < self.ttl:
                return val
        return None

    def set(self, prompt: str, system: str, models: list, value: str):
        if not self.enabled:
            return
        key = self._make_key(prompt, system, models)
        ts = time.time()
        if self._db:
            self._db.execute("REPLACE INTO cache (key, value, ts) VALUES (?,?,?)", (key, value, ts))
            self._db.commit()
        else:
            self._store[key] = (value, ts)


# ═══════════════════════════════════════════════════════════
#  QUALITY SCORER
# ═══════════════════════════════════════════════════════════

class QualityScorer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.metrics = cfg.metrics
        self.judge_provider = build_provider(cfg.judge) if cfg.judge else None

    async def score(self, answer: str, all_answers: list[str], prompt: str, model_name: str, model_weight: float) -> dict:
        scores = {}

        # 1. Длина (штраф за слишком короткие/длинные)
        length = len(answer)
        ideal_len = min(max(length, 200), 3000)
        scores["length"] = 1.0 - abs(length - ideal_len) / ideal_len * self.metrics.get("length_weight", 0.15)

        # 2. Разнообразие (по сравнению с другими ответами)
        diversity = 0.5
        if len(all_answers) > 1:
            similarities = [self._text_similarity(answer, a) for a in all_answers if a != answer]
            avg_sim = sum(similarities) / len(similarities) if similarities else 0
            diversity = 1.0 - avg_sim
        scores["diversity"] = diversity * self.metrics.get("diversity_weight", 0.25)

        # 3. Вес модели
        scores["model"] = model_weight * self.metrics.get("model_weight", 0.20)

        # 4. Когерентность через модель-судью
        coherence = 0.7
        if self.judge_provider and random.random() < 0.5:  # не каждый раз, экономия токенов
            try:
                coherence = await self._judge_coherence(answer, prompt)
            except Exception:
                coherence = 0.7
        scores["coherence"] = coherence * self.metrics.get("coherence_weight", 0.25)

        # 5. Полнота (ключевые слова из промпта)
        completeness = self._check_completeness(answer, prompt)
        scores["completeness"] = completeness * self.metrics.get("completeness_weight", 0.15)

        scores["total"] = sum(scores.values())
        return scores

    async def _judge_coherence(self, answer: str, prompt: str) -> float:
        judge_prompt = (
            f"Rate the quality of this answer on a scale 0.0 to 1.0.\n"
            f"Consider: relevance, coherence, informativeness, clarity.\n"
            f"Respond ONLY with a number between 0 and 1.\n\n"
            f"Question: {prompt}\n\nAnswer: {answer}\n\nScore:"
        )
        result = await self.judge_provider.chat([
            {"role": "user", "content": judge_prompt}
        ], temperature=0.1)
        match = re.search(r"0\.\d+|1\.0|1|0", result.strip())
        if match:
            return min(max(float(match.group()), 0.0), 1.0)
        return 0.7

    def _text_similarity(self, a: str, b: str) -> float:
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0
        return len(set_a & set_b) / len(set_a | set_b)

    def _check_completeness(self, answer: str, prompt: str) -> float:
        keywords = set(re.findall(r"\b\w{4,}\b", prompt.lower()))
        if not keywords:
            return 0.8
        answer_words = set(answer.lower().split())
        covered = sum(1 for k in keywords if k in answer_words)
        return min(covered / max(len(keywords) * 0.3, 1), 1.0)


# ═══════════════════════════════════════════════════════════
#  CORE MoA ENGINE
# ═══════════════════════════════════════════════════════════

class MoAEngine:
    def __init__(self, config_path="moa_config.yaml"):
        self.cfg = Config(config_path)
        self.cache = MoACache(self.cfg.cache_cfg)
        self.scorer = QualityScorer(self.cfg)

        # Инициализация провайдеров
        self.proposers = [build_provider(p) for p in self.cfg.proposers]
        self.aggregators_l1 = [build_provider(a) for a in self.cfg.aggregators_l1]
        self.final_provider = build_provider(self.cfg.final)
        self.self_critique = self.cfg.final_self_critique

    async def run(self, prompt: str, system: str = "You are a helpful assistant.") -> MoAResult:
        start = time.time()
        models_used = []
        result = MoAResult(
            final_answer="",
            raw_answers={},
            scores={},
        )

        # ─── Проверка кэша ───
        model_names = [p.cfg.name for p in self.proposers]
        cached = self.cache.get(prompt, system, model_names)
        if cached:
            result.final_answer = cached
            result.latency = 0
            result.models_used = ["cache"]
            return result

        try:
            # ═══════════════════════════════════════════
            # LAYER 0: Последовательный запуск пропозеров
            # ═══════════════════════════════════════════
            l0_answers = {}
            for prov in self.proposers:
                name, ans = await self._call_safe(prov, system, prompt, prov.cfg.name)
                models_used.append(prov.cfg.name)
                if ans:
                    l0_answers[name] = ans
                await asyncio.sleep(3)  # пауза между моделями

            result.raw_answers = l0_answers

            if not l0_answers:
                raise RuntimeError("Все пропозеры упали")

            # ═══════════════════════════════════════════
            # LAYER 1: Аггрегация ответов пропозеров
            # ═══════════════════════════════════════════
            l1_prompt = self._build_aggregation_prompt(prompt, l0_answers)
            l1_outputs = []
            for prov in self.aggregators_l1:
                name, ans = await self._call_safe(prov, "You are a critical synthesizer.", l1_prompt, prov.cfg.name)
                models_used.append(prov.cfg.name)
                if ans:
                    l1_outputs.append({"model": name, "answer": ans})
                await asyncio.sleep(5)

            if not l1_outputs:
                # fallback — берём лучший из Layer 0
                best = max(l0_answers.values(), key=len)
                result.final_answer = best
                result.latency = time.time() - start
                result.models_used = models_used
                return result

            result.layer1_outputs = l1_outputs

            # ═══════════════════════════════════════════
            # QUALITY SCORING Layer 1 (опционально)
            # ═══════════════════════════════════════════
            all_l1_texts = [o["answer"] for o in l1_outputs]
            scored = []
            if self.scorer and self.cfg.quality_cfg.get("enabled", True):
                for entry in l1_outputs:
                    cfg = next((a for a in self.cfg.aggregators_l1 if a.name == entry["model"]), None)
                    w = cfg.weight if cfg else 1.0
                    scores = await self.scorer.score(
                        entry["answer"], all_l1_texts, prompt,
                        entry["model"], w,
                    )
                    scored.append((scores.get("total", 0), entry["model"], entry["answer"]))
                    result.scores[entry["model"]] = scores

                scored.sort(key=lambda x: x[0], reverse=True)
            else:
                # без скоринга — равные веса
                for i, entry in enumerate(l1_outputs):
                    scored.append((1.0 - i * 0.01, entry["model"], entry["answer"]))

            top_k = self.cfg.top_k_layer1

            # ═══════════════════════════════════════════
            # LAYER 2: Финальная аггрегация
            # ═══════════════════════════════════════════
            top_answers = scored[:top_k]
            final_prompt = self._build_final_prompt(prompt, top_answers)
            final_system = "You are a master synthesizer. Produce the best possible final answer."

            final_answer = await self.final_provider.chat([
                {"role": "system", "content": final_system},
                {"role": "user", "content": final_prompt},
            ])
            models_used.append(self.cfg.final.name)

            # ═══════════════════════════════════════════
            # SELF-CRITIQUE (опционально)
            # ═══════════════════════════════════════════
            if self.self_critique:
                final_answer = await self._self_critique(prompt, final_answer, models_used)

            result.final_answer = final_answer
            result.models_used = models_used

        except Exception as e:
            traceback.print_exc()
            # Ultimate fallback
            if not result.final_answer and result.raw_answers:
                result.final_answer = max(result.raw_answers.values(), key=len)
            else:
                result.final_answer = f"MoA Error: {e}"

        result.latency = time.time() - start
        # Кэшируем
        if result.final_answer and "Error" not in result.final_answer:
            self.cache.set(prompt, system, model_names, result.final_answer)
        return result

    async def _call_safe(self, provider, system: str, prompt: str, name: str) -> tuple:
        try:
            ans = await asyncio.wait_for(
                provider.chat([
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]),
                timeout=120.0,
            )
            return name, ans
        except asyncio.TimeoutError:
            print(f"[WARN] {name} timed out (75s)")
            return name, None
        except Exception as e:
            print(f"[WARN] {name} failed: {e}")
            return name, None

    def _build_aggregation_prompt(self, original_prompt: str, answers: dict) -> str:
        parts = [f"Original question: {original_prompt}\n"]
        parts.append("Here are candidate answers from different AI models:\n")
        for name, ans in answers.items():
            parts.append(f"--- {name} ---\n{ans}\n")
        parts.append(
            "Synthesize the BEST possible answer from these candidates. "
            "Combine strengths, remove weaknesses, be comprehensive and accurate."
        )
        return "\n".join(parts)

    def _build_final_prompt(self, original_prompt: str, top_answers: list) -> str:
        parts = [f"Original question: {original_prompt}\n"]
        parts.append("Here are the top refined answers:\n")
        for i, (score, name, ans) in enumerate(top_answers, 1):
            parts.append(f"--- Refined Answer {i} (score: {score:.3f}) ---\n{ans}\n")
        parts.append(
            "Produce the ultimate final answer — the most accurate, comprehensive, "
            "and well-structured response. Integrate all valuable information."
        )
        return "\n".join(parts)

    async def _self_critique(self, prompt: str, answer: str, models_used: list) -> str:
        critique_sys = "You are a critical reviewer. Find flaws and improve."
        critique_prompt = (
            f"Original question: {prompt}\n\n"
            f"Draft answer: {answer}\n\n"
            f"Critique this answer. Identify any errors, omissions, or weaknesses. "
            f"Then provide an IMPROVED final version.\n\n"
            f"Format:\n"
            f"CRITIQUE: <your critique>\n"
            f"IMPROVED: <improved answer>"
        )
        try:
            critique = await self.final_provider.chat([
                {"role": "system", "content": critique_sys},
                {"role": "user", "content": critique_prompt},
            ])
            improved_match = re.search(r"IMPROVED:\s*(.+)", critique, re.DOTALL)
            if improved_match:
                improved = improved_match.group(1).strip()
                if len(improved) > len(answer) * 0.5:
                    return improved
            return answer
        except Exception:
            return answer
