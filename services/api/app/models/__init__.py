"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.ai import ProviderConfig, UsageRecord
from app.models.conversation import Conversation, Message
from app.models.devices import Device, DevicePairingCode
from app.models.identity import User, UserPreference, UserSession
from app.models.memory import Memory, MemoryHistory
from app.models.platform import AuditEvent, Integration, Notification
from app.models.scheduling import ScheduledJob, ScheduleRun
from app.models.tasks import Task, TaskEvent

__all__ = [
    "AuditEvent",
    "Conversation",
    "Device",
    "DevicePairingCode",
    "Integration",
    "Memory",
    "MemoryHistory",
    "Message",
    "Notification",
    "ProviderConfig",
    "ScheduleRun",
    "ScheduledJob",
    "Task",
    "TaskEvent",
    "UsageRecord",
    "User",
    "UserPreference",
    "UserSession",
]
