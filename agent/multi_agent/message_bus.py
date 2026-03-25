"""
Message Bus â€” async pub/sub inter-agent communication.

Agents send typed messages to named topics.
Subscribers receive messages via async queues.

Message types:
  TASK_ASSIGNED   â€” supervisor â†’ worker: here is your subtask
  TASK_RESULT     â€” worker â†’ supervisor: subtask is done
  TASK_FAILED     â€” worker â†’ supervisor: subtask failed
  STATUS_UPDATE   â€” worker â†’ supervisor: progress update
  BROADCAST       â€” any â†’ all: system-wide notification

Usage::

    bus = MessageBus()

    # Worker subscribes to its own inbox
    sub = await bus.subscribe("worker_1")

    # Supervisor publishes a task
    await bus.publish(AgentMessage(
        type=MessageType.TASK_ASSIGNED,
        sender="supervisor",
        recipient="worker_1",
        payload={"subtask_id": "step_1", "description": "Write auth.py"},
    ))

    # Worker receives it
    msg = await sub.receive(timeout=5.0)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    TASK_ASSIGNED = "task_assigned"
    TASK_RESULT = "task_result"
    TASK_FAILED = "task_failed"
    STATUS_UPDATE = "status_update"
    BROADCAST = "broadcast"
    HEARTBEAT = "heartbeat"


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass
class AgentMessage:
    """A message exchanged between agents on the bus."""

    type: MessageType
    sender: str
    recipient: str                       # agent id or "broadcast"
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str | None = None   # links replies to requests

    def to_json(self) -> str:
        d = asdict(self)
        d["type"] = self.type.value
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str) -> "AgentMessage":
        d = json.loads(s)
        d["type"] = MessageType(d["type"])
        return cls(**d)

    def reply(self, reply_type: MessageType, payload: dict[str, Any]) -> "AgentMessage":
        """Create a reply message with the correlation_id set."""
        return AgentMessage(
            type=reply_type,
            sender=self.recipient,
            recipient=self.sender,
            payload=payload,
            correlation_id=self.message_id,
        )


# ---------------------------------------------------------------------------
# Subscription handle
# ---------------------------------------------------------------------------


class Subscription:
    """
    A subscriber's handle to its inbox queue.

    Call `receive()` to get the next message.
    Call `unsubscribe()` to remove this subscription from the bus.
    """

    def __init__(self, agent_id: str, bus: "MessageBus") -> None:
        self.agent_id = agent_id
        self._bus = bus
        self._queue: asyncio.Queue[AgentMessage] = asyncio.Queue()

    async def receive(self, timeout: float = 30.0) -> AgentMessage | None:
        """
        Wait for and return the next message.

        Returns None on timeout.
        """
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def receive_nowait(self) -> AgentMessage | None:
        """Return the next message if available, else None."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()

    async def unsubscribe(self) -> None:
        await self._bus.unsubscribe(self.agent_id)

    def _put(self, msg: AgentMessage) -> None:
        """Internal: deliver a message to this subscription's queue."""
        self._queue.put_nowait(msg)


# ---------------------------------------------------------------------------
# Message Bus
# ---------------------------------------------------------------------------


class MessageBus:
    """
    In-process async pub/sub message bus for multi-agent communication.

    Each agent subscribes with its agent_id and receives messages
    addressed to it or broadcast to all.

    For production use, replace this with Redis Streams, RabbitMQ, or
    similar external message broker.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._history: list[AgentMessage] = []
        self._max_history = 500
        self._lock = asyncio.Lock()

    async def subscribe(self, agent_id: str) -> Subscription:
        """Register an agent and return its Subscription handle."""
        async with self._lock:
            if agent_id in self._subscriptions:
                return self._subscriptions[agent_id]
            sub = Subscription(agent_id=agent_id, bus=self)
            self._subscriptions[agent_id] = sub
            return sub

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent's subscription."""
        async with self._lock:
            self._subscriptions.pop(agent_id, None)

    async def publish(self, message: AgentMessage) -> int:
        """
        Deliver a message to its recipient(s).

        Returns the number of subscribers that received the message.
        """
        async with self._lock:
            self._history.append(message)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            delivered = 0
            if message.type == MessageType.BROADCAST or message.recipient == "broadcast":
                # Deliver to everyone except the sender
                for aid, sub in self._subscriptions.items():
                    if aid != message.sender:
                        sub._put(message)
                        delivered += 1
            else:
                sub = self._subscriptions.get(message.recipient)
                if sub:
                    sub._put(message)
                    delivered += 1

        return delivered

    async def send(
        self,
        sender: str,
        recipient: str,
        msg_type: MessageType,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Convenience wrapper: create and publish a message in one call."""
        msg = AgentMessage(
            type=msg_type,
            sender=sender,
            recipient=recipient,
            payload=payload or {},
        )
        await self.publish(msg)
        return msg

    def history(
        self,
        sender: str | None = None,
        recipient: str | None = None,
        msg_type: MessageType | None = None,
        last_n: int = 50,
    ) -> list[AgentMessage]:
        """Return filtered message history."""
        msgs = self._history[-last_n:]
        if sender:
            msgs = [m for m in msgs if m.sender == sender]
        if recipient:
            msgs = [m for m in msgs if m.recipient == recipient]
        if msg_type:
            msgs = [m for m in msgs if m.type == msg_type]
        return msgs

    def active_agents(self) -> list[str]:
        return list(self._subscriptions.keys())

    def stats(self) -> dict[str, Any]:
        return {
            "active_agents": len(self._subscriptions),
            "messages_in_history": len(self._history),
            "agents": self.active_agents(),
        }
