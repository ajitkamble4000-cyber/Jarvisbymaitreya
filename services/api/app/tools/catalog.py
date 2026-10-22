"""Every tool the assistant may propose, with the description the model reads and the policy it runs under.

Device tools: ``device_action`` and ``required_capability`` both equal the tool name, which is also the
action the desktop agent understands (docs/desktop-protocol.md). Server tools run inside the backend.
Approval defaults to off for server tools; a device permission of ``ask`` still forces approval for
device tools, as enforced by the task engine.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.tools.args import (
    AppAvailabilityArgs,
    CancelTaskArgs,
    CreateReminderArgs,
    GetTaskArgs,
    ListDevicesArgs,
    ListTasksArgs,
    LaunchAppArgs,
    OpenFolderArgs,
    OpenProjectArgs,
    OpenUrlArgs,
    ReadFileArgs,
    RunCheckArgs,
    SaveMemoryArgs,
    SendReminderArgs,
    SystemInfoArgs,
)
from app.tools.base import ExecutionTarget, RiskLevel, ToolSpec


def _device_tool(
    name: str,
    title: str,
    description: str,
    args_model: type[BaseModel],
    risk: RiskLevel,
    approval_required: bool,
    examples: tuple[str, ...],
    category: str,
    notes: str = "",
    action: str | None = None,
) -> ToolSpec:
    # ``action`` is the wire action and capability name when it differs from the tool name.
    wire_action = action or name
    return ToolSpec(
        name=name,
        title=title,
        description=description,
        args_model=args_model,
        risk=risk,
        target=ExecutionTarget.DEVICE,
        category=category,
        device_action=wire_action,
        required_capability=wire_action,
        approval_required=approval_required,
        example_commands=examples,
        notes=notes,
    )


def _server_tool(
    name: str,
    title: str,
    description: str,
    args_model: type[BaseModel],
    risk: RiskLevel,
    examples: tuple[str, ...],
    category: str,
    notes: str = "",
) -> ToolSpec:
    return ToolSpec(
        name=name,
        title=title,
        description=description,
        args_model=args_model,
        risk=risk,
        target=ExecutionTarget.SERVER,
        category=category,
        approval_required=False,
        example_commands=examples,
        notes=notes,
    )


TOOL_SPECS: tuple[ToolSpec, ...] = (
    _device_tool(
        "open_url",
        "Open a web page",
        "Open an approved web address in the default browser on one of the user's paired devices. "
        "Pass the exact approved URL, or the label of an approved site. Any other address is refused.",
        OpenUrlArgs,
        RiskLevel.LOW,
        approval_required=False,
        examples=("Open YouTube.", "Open the status page on my laptop."),
        category="web",
        notes="Runs without approval only when the device permission for open_url is allow.",
    ),
    _device_tool(
        "launch_app",
        "Launch an app",
        "Start an approved application on a paired device. Pass the approved app id or its label.",
        LaunchAppArgs,
        RiskLevel.MEDIUM,
        approval_required=True,
        examples=("Open Chrome on my laptop.", "Launch Visual Studio Code."),
        category="apps",
    ),
    _device_tool(
        "open_folder",
        "Open a folder",
        "Open an approved folder in the file manager on a paired device. Pass the approved folder id or label, never a path.",
        OpenFolderArgs,
        RiskLevel.LOW,
        approval_required=False,
        examples=("Open my Downloads folder.", "Show the Downloads folder on the laptop."),
        category="files",
    ),
    _device_tool(
        "open_project",
        "Open a project",
        "Open an approved project in an approved editor on a paired device. The editor defaults to vscode.",
        OpenProjectArgs,
        RiskLevel.MEDIUM,
        approval_required=True,
        examples=("Open the JARVIS project.", "Open the JARVIS project in Visual Studio Code."),
        category="projects",
    ),
    _device_tool(
        "system_info",
        "Check system information",
        "Read basic facts about a paired device: host name, operating system, memory and uptime.",
        SystemInfoArgs,
        RiskLevel.LOW,
        approval_required=False,
        examples=("How much memory does my laptop have?", "Check the status of my laptop."),
        category="system",
    ),
    _device_tool(
        "read_file",
        "Read an approved file",
        "Read the content of an approved file on a paired device (at most 64 KB). Pass the approved file id or label.",
        ReadFileArgs,
        RiskLevel.HIGH,
        approval_required=True,
        examples=("Read my meeting notes from the laptop.", "Show the contents of the meeting notes file."),
        category="files",
    ),
    _device_tool(
        "app_availability",
        "Check whether an app is installed",
        "Check whether an approved application is installed on a paired device. Nothing is launched.",
        AppAvailabilityArgs,
        RiskLevel.LOW,
        approval_required=False,
        examples=("Is Chrome installed on my laptop?", "Check whether VS Code is available."),
        category="apps",
    ),
    _device_tool(
        "run_project_check",
        "Run an approved project check",
        "Run one pre-approved project check (for example the test suite) on a paired device. Pass the approved "
        "check id or label. The command is defined on the device, never in the chat, and every run needs approval.",
        RunCheckArgs,
        RiskLevel.HIGH,
        approval_required=True,
        examples=("Run the jarvis checks on my laptop.", "Run my tests."),
        category="development",
        notes="Always needs approval, even when the device permission is allow. Runs without a shell.",
        action="run_check",
    ),
    _server_tool(
        "list_devices",
        "List devices",
        "List the user's paired devices with their connection status, default flag and capabilities.",
        ListDevicesArgs,
        RiskLevel.LOW,
        examples=("What devices do I have?", "Is my laptop online?"),
        category="devices",
    ),
    _server_tool(
        "list_tasks",
        "List tasks",
        "List recent tasks, optionally filtered by status. Returns at most 50 items.",
        ListTasksArgs,
        RiskLevel.LOW,
        examples=("What is waiting for my approval?", "Show my failed tasks."),
        category="tasks",
    ),
    _server_tool(
        "get_task",
        "Get a task",
        "Return the status, result or error of one task by its id.",
        GetTaskArgs,
        RiskLevel.LOW,
        examples=("What happened to that task?", "Did the last launch succeed?"),
        category="tasks",
    ),
    _server_tool(
        "cancel_task",
        "Cancel a task",
        "Cancel a queued, awaiting-approval or running task. Finished tasks cannot be cancelled.",
        CancelTaskArgs,
        RiskLevel.MEDIUM,
        examples=("Cancel that task.", "Stop the launch request."),
        category="tasks",
        notes="A running task may still complete on the device if the device is offline.",
    ),
    _server_tool(
        "save_memory",
        "Save a memory",
        "Save a fact, preference, project detail or task context the user wants remembered. "
        "Passwords, keys and tokens are refused.",
        SaveMemoryArgs,
        RiskLevel.MEDIUM,
        examples=("Remember that I prefer dark mode.", "Remember that the JARVIS repo is my main project."),
        category="memory",
    ),
    _server_tool(
        "create_reminder",
        "Create a reminder",
        "Schedule a one-time reminder. 'when' is HH:MM (next occurrence in the user's timezone) "
        "or an ISO 8601 datetime with an offset.",
        CreateReminderArgs,
        RiskLevel.MEDIUM,
        examples=("Remind me at 18:30 to call the bank.", "Remind me tomorrow at 09:00 to submit the report."),
        category="reminders",
        notes="Needs the scheduling service. Refuses honestly when it is not available.",
    ),
    _server_tool(
        "send_reminder",
        "Send a reminder",
        "Deliver a reminder as a notification. Run by the scheduler when a reminder is due.",
        SendReminderArgs,
        RiskLevel.LOW,
        examples=("Send the due reminder.",),
        category="reminders",
    ),
)
