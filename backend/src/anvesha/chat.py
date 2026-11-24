"""Project chat: conversations with the Research Assistant about a project's evidence.

Sending a message stores it together with a ``pending`` reply, then the reply is written
in the background, so it is saved even if the browser disconnects. Progress and the
finished reply are announced on the event channel (keyed by the reply's id) for streams
in any API process. A reply that never finishes (the process died) is marked failed once
it is older than the reply timeout.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.agents.assistant import Assistant, AssistantReply, Progress, Turn
from anvesha.agents.context import WorkspaceCompleter
from anvesha.agents.evidence import load_run_findings
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import ChatMessage, ChatThread, Project, Report
from anvesha.db.session import Database
from anvesha.errors import AppError, NotFoundError
from anvesha.events import clip, notify
from anvesha.llm.budget import Budget
from anvesha.llm.gateway import LLMGateway
from anvesha.sources.service import SourceService
from anvesha.tenancy import Principal
from anvesha.tools import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New chat"
# Extra time before an unfinished reply counts as abandoned (beyond the reply timeout).
STALE_GRACE_SECONDS = 60
MAX_ACTIVITY = 20


class ReplyInProgressError(AppError):
    code = "reply_in_progress"
    http_status = 409


@dataclass(frozen=True)
class ChatEnvironment:
    """The services a reply needs, taken from the request that started it."""

    db: Database
    settings: Settings
    gateway: LLMGateway
    sources: SourceService
    tools: ToolRegistry
    http: httpx2.AsyncClient
    vault: SecretBox | None


@dataclass(frozen=True)
class ReplyJob:
    message_id: uuid.UUID
    user_message_id: uuid.UUID
    thread_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    question: str


async def get_thread(
    session: AsyncSession, principal: Principal, thread_id: uuid.UUID, *, lock: bool = False
) -> ChatThread:
    query = select(ChatThread).where(
        ChatThread.id == thread_id, ChatThread.workspace_id == principal.workspace_id
    )
    if lock:
        query = query.with_for_update()
    thread = await session.scalar(query)
    if thread is None:
        raise NotFoundError("Chat not found")
    return thread


async def get_message(
    session: AsyncSession, principal: Principal, message_id: uuid.UUID
) -> ChatMessage:
    message = await session.scalar(
        select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.workspace_id == principal.workspace_id
        )
    )
    if message is None:
        raise NotFoundError("Message not found")
    return message


def stale_before(settings: Settings) -> datetime:
    return datetime.now(UTC) - timedelta(
        seconds=settings.chat_reply_timeout_seconds + STALE_GRACE_SECONDS
    )


async def expire_stale_replies(
    session: AsyncSession, settings: Settings, thread_id: uuid.UUID
) -> bool:
    """Mark replies abandoned by a crashed process as failed. Returns whether any were."""
    result = await session.execute(
        update(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.status == "pending",
            ChatMessage.updated_at < stale_before(settings),
        )
        .values(
            status="failed",
            error_code="interrupted",
            error_message="This reply was interrupted. Send your message again.",
        )
        .returning(ChatMessage.id)
    )
    expired = list(result.scalars())
    for message_id in expired:
        await notify(session, message_id)
    return bool(expired)


class ChatService:
    """Writes replies in the background and keeps track of them in this process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.chat_max_concurrent_replies)
        self._tasks: set[asyncio.Task[None]] = set()

    async def send(
        self,
        session: AsyncSession,
        env: ChatEnvironment,
        principal: Principal,
        thread_id: uuid.UUID,
        content: str,
    ) -> tuple[ChatMessage, ChatMessage]:
        thread = await get_thread(session, principal, thread_id, lock=True)
        await expire_stale_replies(session, env.settings, thread.id)
        question = ChatMessage(
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            role="user",
            status="complete",
            content=content,
            # now() is fixed for the whole transaction; the clock keeps the question
            # ordered before its reply.
            created_at=func.clock_timestamp(),
        )
        session.add(question)
        await session.flush()  # the question is created before (and sorts before) the reply
        reply = ChatMessage(
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            role="assistant",
            status="pending",
            content="",
            details={"activity": []},
            created_at=func.clock_timestamp(),
        )
        session.add(reply)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ReplyInProgressError(
                "The assistant is still answering your previous message in this chat."
            ) from exc
        if thread.title == DEFAULT_TITLE:
            thread.title = clip(content, 80) or DEFAULT_TITLE
        thread.updated_at = func.now()
        await session.commit()
        await session.refresh(question)
        await session.refresh(reply)
        job = ReplyJob(
            message_id=reply.id,
            user_message_id=question.id,
            thread_id=thread.id,
            project_id=thread.project_id,
            workspace_id=thread.workspace_id,
            question=content,
        )
        self._spawn(self._write_reply(env, job), name=f"chat-reply-{reply.id}")
        return question, reply

    def _spawn(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        """Stop replies still being written (they are marked failed)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_idle(self) -> None:
        """Wait for replies being written (used by tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _write_reply(self, env: ChatEnvironment, job: ReplyJob) -> None:
        activity: list[dict[str, Any]] = []

        async def progress(step: dict[str, Any]) -> None:
            activity.append(step | {"at": datetime.now(UTC).isoformat()})
            await self._update(
                env.db, job.message_id, {"details": {"activity": activity[-MAX_ACTIVITY:]}}
            )

        try:
            async with self._semaphore:
                async with asyncio.timeout(env.settings.chat_reply_timeout_seconds):
                    reply = await self._generate(env, job, progress)
        except TimeoutError:
            await self._fail(
                env.db,
                job.message_id,
                "timeout",
                "The assistant took too long to answer. Try a narrower question.",
            )
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._fail(
                    env.db,
                    job.message_id,
                    "interrupted",
                    "The server stopped while writing this reply. Send your message again.",
                )
            raise
        except AppError as exc:
            await self._fail(env.db, job.message_id, exc.code, str(exc))
        except Exception:
            logger.exception("Chat reply %s failed", job.message_id)
            await self._fail(
                env.db,
                job.message_id,
                "internal_error",
                "Something went wrong while writing this reply.",
            )
        else:
            await self._update(
                env.db,
                job.message_id,
                {
                    "status": "complete",
                    "content": reply.content,
                    "citations": reply.citations,
                    "details": reply.details | {"activity": activity[-MAX_ACTIVITY:]},
                },
            )

    async def _generate(
        self, env: ChatEnvironment, job: ReplyJob, progress: Progress
    ) -> AssistantReply:
        async with env.db.session() as session:
            objective = await session.scalar(
                select(Project.objective).where(Project.id == job.project_id)
            )
            report = await session.scalar(
                select(Report)
                .where(Report.project_id == job.project_id)
                .order_by(Report.version.desc())
                .limit(1)
            )
            report_markdown = report.markdown if report else None
            report_run_id = report.run_id if report else None
            earlier = (
                await session.scalars(
                    select(ChatMessage)
                    .where(
                        ChatMessage.thread_id == job.thread_id,
                        ChatMessage.id.not_in((job.message_id, job.user_message_id)),
                    )
                    .order_by(ChatMessage.created_at, ChatMessage.id)
                )
            ).all()
        findings = await load_run_findings(env.db, report_run_id) if report_run_id else []
        assistant = Assistant(
            completer=WorkspaceCompleter(
                gateway=env.gateway,
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                budget=Budget(max_cost_usd=env.settings.chat_max_cost_usd),
            ),
            tools=env.tools,
            tool_context=ToolContext(
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                db=env.db,
                settings=env.settings,
                http=env.http,
                vault=env.vault,
                gateway=env.gateway,
                sources=env.sources,
            ),
            max_steps=env.settings.chat_max_steps,
            on_progress=progress,
        )
        return await assistant.reply(
            objective=objective or "",
            question=job.question,
            history=conversation_turns(earlier),
            findings=findings,
            report=report_markdown,
        )

    async def _update(self, db: Database, message_id: uuid.UUID, values: dict[str, Any]) -> None:
        async with db.session() as session:
            await session.execute(
                update(ChatMessage)
                .where(ChatMessage.id == message_id, ChatMessage.status == "pending")
                .values(**values)
            )
            await notify(session, message_id)
            await session.commit()

    async def _fail(self, db: Database, message_id: uuid.UUID, code: str, message: str) -> None:
        await self._update(
            db,
            message_id,
            {"status": "failed", "error_code": code[:64], "error_message": message[:4000]},
        )


def conversation_turns(messages: Sequence[ChatMessage]) -> list[Turn]:
    """Earlier questions with their answers. Questions without an answer are skipped."""
    turns: list[Turn] = []
    question: str | None = None
    for message in messages:
        if message.role == "user":
            question = message.content
        elif question is not None and message.status == "complete":
            turns.append(Turn(question=question, answer=message.content))
            question = None
        else:
            question = None
    return turns
