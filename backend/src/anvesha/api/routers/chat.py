import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from anvesha import chat as chat_service
from anvesha.api.deps import (
    BrokerDep,
    DatabaseDep,
    GatewayDep,
    HttpDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SourceServiceDep,
    ToolsDep,
    VaultDep,
)
from anvesha.api.schemas.chat import (
    ChatExchange,
    ChatMessageCreate,
    ChatMessageRead,
    ChatThreadCreate,
    ChatThreadDetail,
    ChatThreadRead,
    ChatThreadUpdate,
)
from anvesha.chat import ChatEnvironment, ChatService
from anvesha.config import Settings
from anvesha.db.models import ChatMessage, ChatThread
from anvesha.db.session import Database
from anvesha.events.broker import RunEventBroker
from anvesha.events.stream import KEEPALIVE_SECONDS, POLL_SECONDS, format_sse
from anvesha.llm.credentials import resolve_route
from anvesha.sources.service import require_project

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _chat_service(request: Request) -> ChatService:
    return cast(ChatService, request.app.state.chat)


@router.get("/projects/{project_id}/chats", response_model=list[ChatThreadRead])
async def list_chats(
    project_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[ChatThread]:
    await require_project(session, principal.workspace_id, project_id)
    result = await session.scalars(
        select(ChatThread)
        .where(
            ChatThread.workspace_id == principal.workspace_id, ChatThread.project_id == project_id
        )
        .order_by(ChatThread.updated_at.desc(), ChatThread.id)
        .limit(limit)
        .offset(offset)
    )
    return result.all()


@router.post(
    "/projects/{project_id}/chats",
    response_model=ChatThreadRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat(
    project_id: uuid.UUID, payload: ChatThreadCreate, session: SessionDep, principal: PrincipalDep
) -> ChatThread:
    project = await require_project(session, principal.workspace_id, project_id)
    thread = ChatThread(
        workspace_id=principal.workspace_id,
        project_id=project.id,
        created_by_id=principal.user_id,
        title=payload.title or chat_service.DEFAULT_TITLE,
    )
    session.add(thread)
    await session.commit()
    return thread


@router.get("/chats/{thread_id}", response_model=ChatThreadDetail)
async def get_chat(
    thread_id: uuid.UUID, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> ChatThreadDetail:
    thread = await chat_service.get_thread(session, principal, thread_id)
    if await chat_service.expire_stale_replies(session, settings, thread.id):
        await session.commit()
    messages = (
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    ).all()
    return ChatThreadDetail(
        **ChatThreadRead.model_validate(thread).model_dump(),
        messages=[ChatMessageRead.model_validate(message) for message in messages],
    )


@router.patch("/chats/{thread_id}", response_model=ChatThreadRead)
async def rename_chat(
    thread_id: uuid.UUID, payload: ChatThreadUpdate, session: SessionDep, principal: PrincipalDep
) -> ChatThread:
    thread = await chat_service.get_thread(session, principal, thread_id)
    thread.title = payload.title
    await session.commit()
    return thread


@router.delete("/chats/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    thread_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    thread = await chat_service.get_thread(session, principal, thread_id)
    await session.delete(thread)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chats/{thread_id}/messages",
    response_model=ChatExchange,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message(
    thread_id: uuid.UUID,
    payload: ChatMessageCreate,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    db: DatabaseDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    sources: SourceServiceDep,
    tools: ToolsDep,
    http: HttpDep,
    vault: VaultDep,
) -> ChatExchange:
    """Ask the Research Assistant about the project. Uses the ``lead`` role's model.

    The reply is written in the background: follow it at
    ``/chat-messages/{assistant_message.id}/stream``, or poll the message.
    """
    # Fail now, not in the background, if no model is set up.
    await resolve_route(session, settings, vault, principal.workspace_id, "lead")
    question, reply = await _chat_service(request).send(
        session,
        ChatEnvironment(
            db=db,
            settings=settings,
            gateway=gateway,
            sources=sources,
            tools=tools,
            http=http,
            vault=vault,
        ),
        principal,
        thread_id,
        payload.content,
    )
    return ChatExchange(
        user_message=ChatMessageRead.model_validate(question),
        assistant_message=ChatMessageRead.model_validate(reply),
    )


@router.get("/chat-messages/{message_id}", response_model=ChatMessageRead)
async def get_chat_message(
    message_id: uuid.UUID, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> ChatMessage:
    message = await chat_service.get_message(session, principal, message_id)
    if message.status == "pending" and await chat_service.expire_stale_replies(
        session, settings, message.thread_id
    ):
        await session.commit()
        await session.refresh(message)
    return message


@router.get(
    "/chat-messages/{message_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_chat_message(
    message_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    db: DatabaseDep,
    broker: BrokerDep,
) -> StreamingResponse:
    """Follow a reply as Server-Sent Events.

    ``progress`` events carry what the assistant is doing (``activity``); a final
    ``message`` event carries the finished (or failed) reply, followed by ``end``.
    """
    message = await chat_service.get_message(session, principal, message_id)
    await session.close()
    return StreamingResponse(
        _follow_reply(db, broker, settings, message.id, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _follow_reply(
    db: Database,
    broker: RunEventBroker,
    settings: Settings,
    message_id: uuid.UUID,
    is_disconnected: Callable[[], Awaitable[bool]],
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> AsyncIterator[bytes]:
    yield b"retry: 3000\n\n"
    seen: tuple[int, object] | None = None
    with broker.subscribe(message_id) as wake:
        while True:
            wake.clear()
            async with db.session() as session:
                message = await session.get(ChatMessage, message_id)
                if (
                    message is not None
                    and message.status == "pending"
                    and await chat_service.expire_stale_replies(
                        session, settings, message.thread_id
                    )
                ):
                    await session.commit()
                    await session.refresh(message)
                payload = ChatMessageRead.model_validate(message) if message else None
            if payload is None:
                yield format_sse(None, "end", {"status": "deleted"})
                return
            if payload.status != "pending":
                yield format_sse(None, "message", payload.model_dump(mode="json"))
                yield format_sse(None, "end", {"status": payload.status})
                return
            activity = payload.details.get("activity") or []
            # The list is capped, so the newest step (not the length) tells what's new.
            marker = (len(activity), activity[-1].get("at") if activity else None)
            if marker != seen:
                seen = marker
                yield format_sse(None, "progress", {"activity": activity})
            if await is_disconnected():
                return
            timeout = keepalive_seconds if broker.healthy else min(POLL_SECONDS, keepalive_seconds)
            try:
                await asyncio.wait_for(wake.wait(), timeout=timeout)
            except TimeoutError:
                yield b": keepalive\n\n"
