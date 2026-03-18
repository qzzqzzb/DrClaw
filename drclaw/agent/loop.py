"""Agent loop engine — the core LLM ↔ tool iteration cycle."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from drclaw.agent.context import ContextBuilder
from drclaw.agent.debug import DebugLogger
from drclaw.agent.memory import MemoryStore
from drclaw.bus.queue import MessageBus
from drclaw.models.messages import InboundMessage, OutboundMessage
from drclaw.providers.base import LLMProvider, LLMResponse
from drclaw.session.manager import Message, Session, SessionManager
from drclaw.tools.background_tasks import BackgroundToolTaskManager
from drclaw.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from drclaw.usage.store import AgentUsageStore

_TOOL_RESULT_MAX_CHARS = 500
MAX_HOPS = 5
_MAX_ITERATIONS_FINALIZE_NOTICE = (
    "[Environment Notice] The iteration budget has been exhausted for this run. "
    "You must immediately provide your best final response based only on information already "
    "available in the conversation and tool results. Do not call tools. "
    "If anything is incomplete, clearly state what is missing."
)
_CROSS_AGENT_SOURCE_HEADER_PREFIX = "[Message Source: "
_AGENT_SOURCE_PREFIXES = ("proj:", "equip:", "claude_code:")
_AGENT_SOURCE_EXACT = {"main", "cron"}


class AgentLoop:
    """Generic agent loop engine.

    No Main/Project knowledge — constructor takes pre-built collaborators
    via dependency injection.  Each orchestrator (future Layer 5) wires its own.

    Concurrency: ``process_direct`` is NOT concurrency-safe for the same
    session key.  Callers must serialize externally (``_dispatch`` does this
    via ``_processing_lock``).  Two concurrent ``process_direct`` calls on
    different session keys are fine.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        session_manager: SessionManager,
        bus: MessageBus | None = None,
        memory_store: MemoryStore | None = None,
        max_iterations: int = 40,
        memory_window: int = 100,
        debug_logger: DebugLogger | None = None,
        max_history: int | None = None,
        agent_id: str = "main",
        session_key: str | None = None,
        usage_store: AgentUsageStore | None = None,
        background_task_manager: BackgroundToolTaskManager | None = None,
        tool_detach_timeout_seconds: float = 60,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.context_builder = context_builder
        self.session_manager = session_manager
        self.bus = bus
        self.memory_store = memory_store
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.debug_logger = debug_logger
        self.max_history = max_history
        self.agent_id = agent_id
        self._session_key: str | None = session_key
        self.usage_store = usage_store
        self.background_task_manager = background_task_manager
        self.tool_detach_timeout_seconds = max(0.001, tool_detach_timeout_seconds)

        self.on_tool_call: Callable[[str, dict[str, Any]], Any] | None = None

        self._running = False
        self._processing_lock = asyncio.Lock()
        self._current_inbound: InboundMessage | None = None
        self._active_session: Session | None = None
        self._last_turn_had_error = False

    @property
    def session_key(self) -> str:
        return self._session_key if self._session_key is not None else self.agent_id

    @session_key.setter
    def session_key(self, value: str | None) -> None:
        self._session_key = value

    def _prepare_message_tool(self, msg: InboundMessage) -> None:
        tool = self.tool_registry.get("message")
        if tool is None:
            return

        set_context = getattr(tool, "set_context", None)
        if callable(set_context):
            set_context(msg.channel, msg.chat_id)

        start_turn = getattr(tool, "start_turn", None)
        if callable(start_turn):
            start_turn()

    def _message_tool_sent_to_current_chat(self, msg: InboundMessage) -> bool:
        tool = self.tool_registry.get("message")
        if tool is None:
            return False

        sent_in_turn_for = getattr(tool, "sent_in_turn_for", None)
        if not callable(sent_in_turn_for):
            return False
        return bool(sent_in_turn_for(msg.channel, msg.chat_id))

    def _record_usage(self, response: LLMResponse) -> None:
        if self.usage_store is None:
            return

        input_tokens = max(0, int(response.input_tokens or 0))
        output_tokens = max(0, int(response.output_tokens or 0))
        input_cost_usd = max(0.0, float(response.input_cost_usd or 0.0))
        output_cost_usd = max(0.0, float(response.output_cost_usd or 0.0))
        total_cost_usd = max(
            0.0,
            float(response.total_cost_usd or (input_cost_usd + output_cost_usd)),
        )

        try:
            self.usage_store.record(
                agent_id=self.agent_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost_usd=input_cost_usd,
                output_cost_usd=output_cost_usd,
                total_cost_usd=total_cost_usd,
            )
        except Exception:
            logger.exception("Failed to record usage for agent {}", self.agent_id)

    # ------------------------------------------------------------------
    # Core iteration engine
    # ------------------------------------------------------------------

    async def _run_agent_loop(
        self,
        messages: list[dict[str, Any]],
        *,
        on_new_messages: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]], bool]:
        """Run the LLM ↔ tool loop until the model stops calling tools.

        Returns (final_content, tools_used, messages, had_error).

        *on_new_messages* is called after each iteration with the new messages
        appended during that iteration, enabling incremental session persistence.
        """
        tool_defs = self.tool_registry.get_definitions() or None
        tools_used: list[str] = []
        final_content: str | None = None
        had_error = False
        iteration = 0

        while iteration < self.max_iterations:
            mark = len(messages)
            if self.debug_logger:
                self.debug_logger.log_request(iteration, messages, tool_defs)
            response = await self.provider.complete(messages, tools=tool_defs)
            if self.debug_logger:
                self.debug_logger.log_response(iteration, response)
            self._record_usage(response)

            if response.stop_reason == "error":
                logger.error("LLM returned error stop_reason")
                final_content = response.content or "An error occurred while processing your request."
                had_error = True
                break

            if response.stop_reason == "max_tokens":
                logger.warning("LLM response truncated (max_tokens reached)")

            if response.tool_calls:
                # Build tool_calls list in OpenAI format for the assistant message.
                # arguments must be a JSON string per the OpenAI API spec.
                tc_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                self.context_builder.add_assistant_message(
                    messages, response.content, tool_calls=tc_dicts
                )

                for tc in response.tool_calls:
                    if self.on_tool_call:
                        ret = self.on_tool_call(tc.name, tc.arguments)
                        if asyncio.iscoroutine(ret):
                            await ret
                    tool_task: asyncio.Task[str] | None = None
                    try:
                        tool_task = asyncio.create_task(
                            self.tool_registry.execute(tc.name, tc.arguments)
                        )
                        if self.background_task_manager is None:
                            result = await tool_task
                        else:
                            result = await asyncio.wait_for(
                                asyncio.shield(tool_task),
                                timeout=self.tool_detach_timeout_seconds,
                            )
                    except asyncio.TimeoutError:
                        if (
                            tool_task is None
                            or self.background_task_manager is None
                        ):
                            logger.exception("Tool execution timed out unexpectedly: {}", tc.name)
                            result = (
                                "Error: tool execution timed out before background handoff."
                            )
                        elif tool_task.done():
                            # Task finished between shield timeout and here —
                            # grab the result directly instead of detaching.
                            try:
                                result = tool_task.result()
                            except Exception:
                                logger.exception("Tool crashed: {}", tc.name)
                                result = f"Error: tool '{tc.name}' crashed unexpectedly."
                        else:
                            inbound = self._current_inbound
                            channel = inbound.channel if inbound else "system"
                            chat_id = inbound.chat_id if inbound else self.agent_id
                            rec = await self.background_task_manager.register_detached(
                                task=tool_task,
                                tool_name=tc.name,
                                tool_args=tc.arguments,
                                channel=channel,
                                chat_id=chat_id,
                                bus=self.bus,
                                publish_inbound_callback=self.bus is not None,
                            )
                            result = self.background_task_manager.build_detached_tool_result(rec)
                    except Exception:
                        if tool_task and not tool_task.done():
                            tool_task.cancel()
                        logger.exception("Tool execution crashed: {}", tc.name)
                        result = f"Error: tool '{tc.name}' crashed unexpectedly."
                    if self.debug_logger:
                        self.debug_logger.log_tool_exec(iteration, tc.name, tc.arguments, result)
                    self.context_builder.add_tool_result(messages, tc.id, tc.name, result)
                    tools_used.append(tc.name)

                if on_new_messages:
                    on_new_messages(messages[mark:])
                iteration += 1
                continue

            # No tool calls — model is done
            mark = len(messages)
            self.context_builder.add_assistant_message(messages, response.content)
            final_content = response.content
            if on_new_messages:
                on_new_messages(messages[mark:])
            break

        if iteration >= self.max_iterations and final_content is None:
            mark = len(messages)
            messages.append({"role": "system", "content": _MAX_ITERATIONS_FINALIZE_NOTICE})
            if self.debug_logger:
                self.debug_logger.log_request(iteration, messages, None)

            final_response = await self.provider.complete(messages, tools=None)

            if self.debug_logger:
                self.debug_logger.log_response(iteration, final_response)
            self._record_usage(final_response)

            if final_response.stop_reason == "error":
                logger.error("LLM returned error stop_reason during max-iteration finalize pass")
                final_content = "An error occurred while finalizing your request."
                had_error = True
            else:
                if final_response.tool_calls:
                    logger.warning(
                        "LLM requested tools during max-iteration finalize pass; "
                        "ignoring tool calls"
                    )
                final_content = final_response.content

            if not final_content:
                final_content = (
                    "I've reached the maximum number of processing steps. "
                    "Here's what I have so far — please follow up if you need more."
                )

            self.context_builder.add_assistant_message(messages, final_content)
            if on_new_messages:
                on_new_messages(messages[mark:])

        return final_content, tools_used, messages, had_error

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _save_turn(
        session: Session,
        messages: list[dict[str, Any]],
        skip: int,
        *,
        inbound_source: str,
        agent_source: str,
    ) -> None:
        """Filter and append new messages (after ``skip``) to session history.

        Skips system messages, empty assistants, and runtime context injections.
        Truncates large tool results.  Creates a shallow copy for truncated
        messages so the original ``messages`` list is never mutated.
        Ensures every persisted message has a non-empty ``source``.
        """
        for msg in messages[skip:]:
            role = msg.get("role")

            if role == "system":
                continue

            if role == "assistant" and not msg.get("content") and not msg.get("tool_calls"):
                continue

            if ContextBuilder.is_runtime_context(msg):
                continue

            # Truncate large tool results — shallow-copy to avoid mutating
            # the caller's message list.
            if role == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > _TOOL_RESULT_MAX_CHARS:
                    msg = {
                        **msg,
                        "content": content[:_TOOL_RESULT_MAX_CHARS] + "\n... (truncated)",
                    }

            source_raw = msg.get("source")
            source = source_raw.strip() if isinstance(source_raw, str) else ""
            if not source:
                source = inbound_source if role == "user" else agent_source
                msg = {**msg, "source": source}

            session.messages.append(cast(Message, msg))

    # ------------------------------------------------------------------
    # Public API — direct mode
    # ------------------------------------------------------------------

    async def process_direct(
        self,
        content: str,
        session_key: str,
        *,
        source: str = "user",
        channel: str | None = None,
        chat_id: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Process a single message without the bus.

        Loads session, builds context, runs the agent loop, saves results,
        and triggers memory consolidation if threshold exceeded.

        Not concurrency-safe for the same session key — callers must
        serialize externally (see ``_dispatch``).
        """
        session = self.session_manager.load(session_key)
        self._active_session = session
        try:
            history_raw = cast(list[dict[str, Any]], session.get_history(max_messages=self.max_history))
            history = self._sanitize_history_for_model(history_raw)
            messages = self.context_builder.build_messages(
                history,
                content,
                channel,
                chat_id,
                runtime_metadata=runtime_metadata,
            )
            skip = len(messages)

            # The user message lives inside `messages` (at skip-1) but that
            # region is excluded from _save_turn by the skip offset.  Append
            # it to the session explicitly so it persists across turns.
            source_text = source.strip() if isinstance(source, str) else "user"
            if not source_text:
                source_text = "user"
            user_message: dict[str, Any] = {"role": "user", "content": content, "source": source_text}
            attachments = self._runtime_attachments(runtime_metadata)
            if attachments:
                user_message["attachments"] = attachments
            session.messages.append(cast(Message, user_message))
            self.session_manager.save(session)

            def _persist_incremental(new_msgs: list[dict[str, Any]]) -> None:
                """Incrementally persist new messages from the agent loop."""
                self._save_turn(
                    session,
                    new_msgs,
                    0,
                    inbound_source=source_text,
                    agent_source=self.agent_id,
                )
                self.session_manager.save(session)

            self._last_turn_had_error = False
            final_content, _tools_used, all_msgs, had_error = await self._run_agent_loop(
                messages, on_new_messages=_persist_incremental,
            )
            self._last_turn_had_error = had_error

            await self._maybe_consolidate(session)

            return final_content or ""
        finally:
            self._active_session = None

    @staticmethod
    def _is_agent_source(source: str) -> bool:
        return source in _AGENT_SOURCE_EXACT or source.startswith(_AGENT_SOURCE_PREFIXES)

    @staticmethod
    def _sanitize_history_for_model(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Map persisted history to provider-safe payloads.

        Keeps only model-relevant fields and applies source-aware mapping:
        - cross-agent user messages get ``name=<source>``
        - and a content header so models without ``name`` support still see source
        """
        sanitized: list[dict[str, Any]] = []
        for msg in history:
            role = msg.get("role")
            if not isinstance(role, str) or not role:
                continue

            cleaned: dict[str, Any] = {"role": role, "content": msg.get("content")}
            source_raw = msg.get("source")
            source = source_raw.strip() if isinstance(source_raw, str) else ""
            if role == "user" and source and AgentLoop._is_agent_source(source):
                cleaned["name"] = source
                content = cleaned.get("content")
                if isinstance(content, str):
                    header = f"{_CROSS_AGENT_SOURCE_HEADER_PREFIX}{source}]"
                    if not content.startswith(header):
                        cleaned["content"] = f"{header}\n{content}"
            if role == "assistant" and "tool_calls" in msg:
                cleaned["tool_calls"] = msg.get("tool_calls")
            if role == "tool":
                if "tool_call_id" in msg:
                    cleaned["tool_call_id"] = msg.get("tool_call_id")
                if "name" in msg:
                    cleaned["name"] = msg.get("name")

            sanitized.append(cleaned)
        return sanitized

    @staticmethod
    def _runtime_attachments(runtime_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not runtime_metadata:
            return []
        raw = runtime_metadata.get("attachments")
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            file_id = item.get("id")
            if not isinstance(file_id, str):
                continue
            clean_id = file_id.strip().lower()
            if not clean_id or clean_id in seen:
                continue
            out_item: dict[str, Any] = {
                "id": clean_id,
                "name": str(item.get("name") or "file"),
                "mime": str(item.get("mime") or "application/octet-stream"),
                "size": (
                    max(0, int(item.get("size")))
                    if isinstance(item.get("size"), (int, float))
                    else 0
                ),
                "path": str(item.get("path") or ""),
            }
            download_url = item.get("download_url")
            if isinstance(download_url, str) and download_url.strip():
                out_item["download_url"] = download_url
            out.append(out_item)
            seen.add(clean_id)
        return out

    # ------------------------------------------------------------------
    # Startup consolidation
    # ------------------------------------------------------------------

    async def consolidate_on_startup(self, session_key: str) -> None:
        """Clear stale session on REPL startup.

        Always clears the session so the REPL starts fresh.  Long-term
        memory is already persisted in MEMORY.md by mid-session
        consolidation, so there is no need for an extra LLM call here.
        """
        session = self.session_manager.load(session_key)
        if not session.messages:
            return
        session.clear()
        self.session_manager.save(session)

    # ------------------------------------------------------------------
    # Memory consolidation
    # ------------------------------------------------------------------

    async def _maybe_consolidate(self, session: Session) -> None:
        """Trigger memory consolidation if unconsolidated messages exceed threshold."""
        if not self.memory_store:
            return
        unconsolidated = len(session.messages) - session.last_consolidated
        if unconsolidated >= self.memory_window:
            success = await self.memory_store.consolidate(
                session,
                self.provider,
                memory_window=self.memory_window,
                debug_logger=self.debug_logger,
                on_response=self._record_usage,
            )
            if success:
                self.session_manager.save(session)

    # ------------------------------------------------------------------
    # Public API — bus mode
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the bus-based interactive loop.

        Consumes inbound messages and dispatches them to the agent loop.
        Requires a MessageBus to be set.
        """
        if self.bus is None:
            raise RuntimeError("MessageBus required for run()")
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(self.agent_id), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Dispatch an inbound message through the agent loop under the processing lock."""
        if msg.hop_count >= MAX_HOPS:
            if self.bus:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text=(
                            f"Message rejected: hop count ({msg.hop_count})"
                            f" exceeds limit ({MAX_HOPS})."
                        ),
                        source=self.agent_id,
                        topic=self.agent_id,
                    )
                )
            return
        async with self._processing_lock:
            self._current_inbound = msg
            self._prepare_message_tool(msg)

            async def _publish_tool_call(name: str, args: dict[str, Any]) -> None:
                if self.bus is None:
                    return
                args_str = json.dumps(args, ensure_ascii=False) if args else ""
                text = f"{name}({args_str})"
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text=text,
                        source=self.agent_id,
                        topic=self.agent_id,
                        msg_type="tool_call",
                    )
                )

            self.on_tool_call = _publish_tool_call
            try:
                runtime_metadata = dict(msg.metadata)
                if self.agent_id != "main":
                    runtime_metadata.pop("webui_language", None)
                result = await self.process_direct(
                    msg.text,
                    self.session_key,
                    source=msg.source,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    runtime_metadata=runtime_metadata,
                )
            except Exception as exc:
                if self.bus:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            text=f"Error: {exc}",
                            source=self.agent_id,
                            topic=self.agent_id,
                            msg_type="error",
                        )
                    )
                raise
            finally:
                self._current_inbound = None
            if self.bus is None:
                raise RuntimeError("MessageBus disappeared during dispatch")

            if self._message_tool_sent_to_current_chat(msg):
                return

            delegated = bool(msg.metadata.get("delegated"))
            reply_to_topic_raw = msg.metadata.get("reply_to_topic")
            reply_channel_raw = msg.metadata.get("reply_channel")
            reply_chat_id_raw = msg.metadata.get("reply_chat_id")
            reply_to_topic = (
                reply_to_topic_raw.strip() if isinstance(reply_to_topic_raw, str) else ""
            )
            reply_channel = (
                reply_channel_raw.strip() if isinstance(reply_channel_raw, str) else ""
            )
            reply_chat_id = (
                reply_chat_id_raw.strip() if isinstance(reply_chat_id_raw, str) else ""
            )
            if delegated and reply_to_topic and reply_channel and reply_chat_id:
                await self.bus.publish_inbound(
                    InboundMessage(
                        channel=reply_channel,
                        chat_id=reply_chat_id,
                        text=f"Student report from {self.agent_id}:\n{result}",
                        source=self.agent_id,
                        hop_count=msg.hop_count + 1,
                        metadata={
                            "delegated_result": True,
                            "delegated_by": msg.metadata.get("delegated_by"),
                            "from_topic": msg.topic,
                            "webui_language": msg.metadata.get("webui_language"),
                        },
                    ),
                    topic=reply_to_topic,
                )
                return

            msg_type = "error" if self._last_turn_had_error else "chat"
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    text=result,
                    source=self.agent_id,
                    topic=self.agent_id,
                    msg_type=msg_type,
                )
            )

    def stop(self) -> None:
        """Signal the bus loop to stop."""
        self._running = False
