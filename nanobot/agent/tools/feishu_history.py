"""On-demand, current-conversation Feishu history tool."""
# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import Literal

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.channels.feishu.history import (
    FeishuConversationScope,
    FeishuHistoryCredentials,
    FeishuHistoryError,
    parse_history_time,
    read_feishu_history,
)
from nanobot.channels.feishu.instances import DEFAULT_INSTANCE_ID, feishu_instance_specs


@tool_parameters(
    tool_parameters_schema(
        limit=IntegerSchema(
            description="Maximum messages to return from the current Feishu chat or topic.",
            minimum=1,
            maximum=50,
            nullable=True,
        ),
        start_time=StringSchema(
            "Optional inclusive ISO 8601 timestamp with timezone, for example 2026-08-31T12:00:00Z.",
            nullable=True,
        ),
        end_time=StringSchema(
            "Optional inclusive ISO 8601 timestamp with timezone, for example 2026-08-31T13:00:00Z.",
            nullable=True,
        ),
        order=StringSchema(
            "Message order: asc for oldest first or desc for newest first.",
            enum=("asc", "desc"),
            nullable=True,
        ),
    )
)
class FeishuHistoryTool(Tool):
    """Read a small history window from the current Feishu conversation only."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "read_feishu_history"

    @property
    def description(self) -> str:
        return (
            "Read a bounded window of the current Feishu/Lark chat or current topic's history. "
            "Use only when the user asks to read this conversation's history or when current-topic "
            "context is necessary to answer. Never use it by default on every turn and never use it "
            "to look up another chat. It accepts only a limit, optional time window, and order."
        )

    @property
    def read_only(self) -> bool:
        return True


    @classmethod
    def _credentials_for_current_channel(
        cls,
        channel: str,
    ) -> FeishuHistoryCredentials | None:
        """Resolve the exact persisted instance matched by a runtime channel name."""
        if channel == "feishu":
            instance_id = DEFAULT_INSTANCE_ID
        elif channel.startswith("feishu.") and channel.removeprefix("feishu."):
            instance_id = channel.removeprefix("feishu.")
        else:
            return None

        try:
            from nanobot.channels.feishu.config import feishu_default_config
            from nanobot.config.loader import load_config, resolve_config_env_vars

            config = resolve_config_env_vars(load_config())
            section = getattr(config.channels, "feishu", None)
            specs = feishu_instance_specs(section, feishu_default_config())
        except Exception:
            return None

        for spec in specs:
            if spec.instance_id != instance_id:
                continue
            values = spec.config
            app_id = str(values.get("appId") or values.get("app_id") or "").strip()
            app_secret = str(values.get("appSecret") or values.get("app_secret") or "").strip()
            domain: Literal["feishu", "lark"] = (
                "lark" if str(values.get("domain") or "feishu").strip().lower() == "lark" else "feishu"
            )
            if app_id and app_secret:
                return FeishuHistoryCredentials(
                    app_id=app_id,
                    app_secret=app_secret,
                    domain=domain,
                )
        return None

    async def execute(
        self,
        limit: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        order: Literal["asc", "desc"] | None = None,
    ) -> ToolResult | str:
        request = current_request_context()
        if request is None:
            return ToolResult.error("Error: read_feishu_history requires an active Feishu chat request.")

        channel = request.channel.strip()
        credentials = self._credentials_for_current_channel(channel)
        if credentials is None:
            if channel == "feishu" or channel.startswith("feishu."):
                return ToolResult.error(
                    "Error: the active Feishu instance has no usable app credentials for history access."
                )
            return ToolResult.error(
                "Error: read_feishu_history is only available in the active Feishu/Lark conversation."
            )

        metadata = request.metadata
        # DMs route replies to an open_id; retain the event's chat id only as
        # request metadata, never as a model-controlled argument.
        source_chat_id = metadata.get("source_chat_id")
        chat_id = source_chat_id if isinstance(source_chat_id, str) and source_chat_id else request.chat_id
        thread_id = metadata.get("thread_id")
        root_id = metadata.get("root_id")
        scope = FeishuConversationScope(
            chat_id=chat_id.strip(),
            thread_id=thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else None,
            root_id=root_id.strip() if isinstance(root_id, str) and root_id.strip() else None,
        )

        try:
            return await read_feishu_history(
                credentials=credentials,
                scope=scope,
                limit=20 if limit is None else limit,
                start_time=parse_history_time(start_time, parameter="start_time"),
                end_time=parse_history_time(end_time, parameter="end_time"),
                order="desc" if order is None else order,
            )
        except FeishuHistoryError as exc:
            return ToolResult.error(f"Error reading current Feishu history: {exc}")


__all__ = ["FeishuHistoryTool"]
