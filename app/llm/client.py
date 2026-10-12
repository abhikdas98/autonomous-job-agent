
import hashlib
import json
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.config import settings
from app.db import connection, log_event


class LLMDeferred(RuntimeError):
    pass


class GroqClient:
    def __init__(self):
        self.base = settings.groq_api_base.rstrip("/")
        self.model = settings.groq_model

    def chat(self, messages, temperature=0, max_tokens=400):
        if not settings.llm_enabled:
            raise LLMDeferred("LLM disabled by configuration.")

        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

        cached_response = None

        with connection() as c:
            cached = c.execute(
                "SELECT response, expires_at FROM llm_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()

            if cached and cached["expires_at"] > time.time():
                cached_response = cached["response"]
            else:
                c.execute(
                    "DELETE FROM llm_cache WHERE cache_key=?",
                    (cache_key,),
                )

        # Close the cache connection before logging events.
        if cached_response is not None:
            log_event("llm_cache_hit", {"model": self.model})
            return cached_response

        def reserve_attempt():
            now = time.time()
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            blocked_reason = None

            with connection() as c:
                c.execute(
                    "DELETE FROM llm_request_times WHERE created_at < ?",
                    (now - 60,),
                )

                rpm = c.execute(
                    "SELECT COUNT(*) FROM llm_request_times"
                ).fetchone()[0]

                daily = c.execute(
                    "SELECT count FROM llm_budget WHERE bucket=?",
                    (day,),
                ).fetchone()

                if rpm >= settings.llm_max_requests_per_minute:
                    blocked_reason = "rpm"
                elif (
                    daily
                    and daily["count"] >= settings.llm_max_requests_per_day
                ):
                    blocked_reason = "rpd"
                else:
                    c.execute(
                        "INSERT INTO llm_request_times(created_at) VALUES(?)",
                        (now,),
                    )

                    c.execute(
                        """
                        INSERT INTO llm_budget(bucket,count)
                        VALUES(?,1)
                        ON CONFLICT(bucket)
                        DO UPDATE SET count=count+1
                        """,
                        (day,),
                    )

            # The transaction is closed before logging.
            if blocked_reason:
                log_event(
                    "llm_budget_blocked",
                    {"reason": blocked_reason},
                )
                raise LLMDeferred(
                    f"Local {blocked_reason.upper()} budget exhausted."
                )

        headers = {"Content-Type": "application/json"}

        if settings.groq_api_key:
            headers["Authorization"] = (
                f"Bearer {settings.groq_api_key}"
            )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # GPT-OSS on Groq supports reasoning controls and JSON mode.
        if (
            "api.groq.com" in self.base
            and self.model.startswith("openai/gpt-oss")
        ):
            payload.pop("max_tokens", None)
            payload["max_completion_tokens"] = max_tokens
            payload["reasoning_effort"] = "low"
            payload["include_reasoning"] = False
            payload["response_format"] = {"type": "json_object"}

        last_error = None

        with httpx.Client(
            timeout=settings.llm_timeout_seconds
        ) as client:
            for attempt in range(settings.llm_max_retries + 1):
                reserve_attempt()

                try:
                    response = client.post(
                        f"{self.base}/chat/completions",
                        headers=headers,
                        json=payload,
                    )


                    # Permanent client errors should not be retried.
                    # HTTP 429 and 5xx responses use the retry logic below.
                    if (
                        400 <= response.status_code < 500
                        and response.status_code != 429
                    ):
                        
                        provider_error = "No provider error details returned"

                        try:
                            error_body = response.json()
                            error_detail = error_body.get("error", {})

                            if isinstance(error_detail, dict):
                                provider_error = str(
                                    error_detail.get("message")
                                    or error_detail.get("code")
                                    or "Provider returned an error without a message"
                                )
                            elif isinstance(error_detail, str):
                                provider_error = error_detail

                        except (ValueError, AttributeError):
                            pass

                        # Keep logs bounded and on a single line.
                        provider_error = " ".join(provider_error.split())[:500]

                        log_event(
                            "llm_request_error",
                            {
                                "status_code": response.status_code,
                                "error": provider_error,
                            },
                            "WARNING",
                        )

                        raise LLMDeferred(
                            "Non-retryable upstream HTTP error "
                            f"(status={response.status_code}). "
                            "Check the API key, model, and request."
                        )

                    if (
                        response.status_code == 429
                        or response.status_code >= 500
                    ):
                        retry_after = response.headers.get("Retry-After")
                        delay = None

                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                try:
                                    delay = max(
                                        0,
                                        (
                                            parsedate_to_datetime(retry_after)
                                            - datetime.now(timezone.utc)
                                        ).total_seconds(),
                                    )
                                except Exception:
                                    delay = None

                        if attempt >= settings.llm_max_retries:
                            response.raise_for_status()

                        if delay is None:
                            delay = min(
                                2**attempt + random.random(),
                                8,
                            )

                        if delay > 8:
                            log_event(
                                "llm_retry_deferred",
                                {"delay": delay},
                                "WARNING",
                            )
                            raise LLMDeferred(
                                "Upstream asked for a long wait; retry deferred."
                            )

                        time.sleep(max(0, delay))
                        continue


                    response.raise_for_status()
                    response_json = response.json()

                    choice = response_json["choices"][0]
                    content = choice["message"].get("content")

                    if not isinstance(content, str) or not content.strip():
                        finish = choice.get("finish_reason", "unknown")
                        raise ValueError(
                            f"Empty model content (finish_reason={finish})"
                        )

                    # Cache in its own short transaction.
                    with connection() as c:
                        c.execute(
                            """
                            INSERT OR REPLACE INTO llm_cache
                            (cache_key,response,expires_at)
                            VALUES(?,?,?)
                            """,
                            (
                                cache_key,
                                content,
                                time.time()
                                + settings.llm_cache_ttl_seconds,
                            ),
                        )

                    log_event(
                        "llm_request_success",
                        {
                            "model": self.model,
                            "attempt": attempt + 1,
                        },
                    )

                    return content

                except LLMDeferred:
                    raise

                except (
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    TypeError,
                ) as exc:
                    last_error = exc

                    log_event(
                        "llm_request_error",
                        {
                            "attempt": attempt + 1,
                            "error": str(exc)[:200],
                        },
                        "WARNING",
                    )

                    if attempt >= settings.llm_max_retries:
                        break

                    time.sleep(
                        min(2**attempt + random.random(), 8)
                    )

        raise LLMDeferred(
            f"LLM request failed after bounded retries: {last_error}"
        )
