"""Human-readable command bodies; model-facing tool results stay separate."""

from typing import Any

from nahida_bot.core.system_text import compact_label, format_text_section


def state_icon(state: str) -> str:
    return {
        "idle": "🟢",
        "running": "⏳",
        "queued": "⏳",
        "succeeded": "✅",
        "done": "✅",
        "failed": "⚠️",
        "crashed": "⚠️",
        "timed_out": "⚠️",
        "cancelled": "⏹️",
    }.get(state, "ℹ️")


def format_task(task: Any) -> str:
    """Show one task's state before its identifiers and bounded result."""
    state = task.status.value
    lines = [
        f"Task: {task.task_id}",
        f"Name: {compact_label(task.title, limit=120)}",
        f"Child session: {task.child_session_id or '(none)'}",
    ]
    if task.summary:
        lines.extend(("", "Summary:", task.summary[:1000]))
    if task.error:
        lines.extend(("", "Error:", task.error[:1000]))
    return format_text_section(
        state.replace("_", " ").capitalize(), lines, icon=state_icon(state)
    )


def format_cron_jobs(jobs: list[Any]) -> str:
    blocks = []
    for job in jobs:
        timeout = getattr(job, "timeout_seconds", None)
        lines = [
            f"Status: {'Active' if job.is_active else 'Inactive'}",
            f"Mode: {job.mode} | Executor: {job.executor_type}",
            f"Runs: {job.run_count}",
            f"Next run: {job.next_fire_at if job.is_active else '(none)'}",
            f"Timeout: {str(timeout) + ' s' if timeout is not None else 'Scheduler default'}",
            f"Prompt: {compact_label(job.prompt, limit=120)}",
        ]
        if job.failure_count:
            lines.append(
                f"⚠️ Failures: {job.failure_count} | Last error: {job.last_error}"
            )
        blocks.append(format_text_section(job.job_id, lines))
    return format_text_section(
        f"Scheduled tasks ({len(jobs)})", ("\n\n".join(blocks),), icon="🗓️"
    )


_COMMAND_GROUPS = (
    ("Runtime", "⚙️", {"status", "model", "reasoning", "quota"}),
    ("Conversation", "💬", {"new", "reset", "stop", "steer", "mute", "unmute"}),
    ("Tasks", "🗓️", {"cron", "agents", "agent_stop", "agent_wait"}),
    ("Memory & identity", "🧠", {"memory", "identity"}),
    ("Help & plugins", "🔌", set()),
)


def format_help(commands: list[Any], skills: list[Any]) -> str:
    sections = []
    remaining = sorted(commands, key=lambda command: command.name)
    for title, icon, names in _COMMAND_GROUPS:
        entries = [
            command for command in remaining if not names or command.name in names
        ]
        if not entries:
            continue
        remaining = [command for command in remaining if command not in entries]
        lines = []
        for command in entries:
            aliases = f" ({', '.join(command.aliases)})" if command.aliases else ""
            description = f" — {command.description}" if command.description else ""
            lines.append(f"/{command.name}{aliases}{description}")
        sections.append(format_text_section(title, lines, icon=icon))
    if skills:
        lines = ["Use /<name> or ask the agent to use a skill."]
        lines.extend(
            f"/{skill.name} — {compact_label(skill.description, limit=100)}"
            for skill in sorted(skills, key=lambda skill: skill.name)
        )
        sections.append(format_text_section("Skills", lines, icon="📚"))
    return "\n\n".join(sections) or "ℹ️ No commands or skills available."


def format_quota(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return "ℹ️ No configured providers are available."
    blocks = []
    for report in reports:
        report_id = str(report.get("provider_id") or "unknown")
        snapshot = report.get("snapshot")
        if not isinstance(snapshot, dict):
            blocks.append(
                f"⚠️ {report_id}: {report.get('error') or 'Quota unavailable'}"
            )
            continue
        label = str(snapshot.get("provider_label") or report_id)
        title = f"{report_id} ({label})"
        lines = []
        if snapshot.get("plan_name"):
            lines.append(f"Plan: {snapshot['plan_name']}")
        for window in snapshot.get("windows") or []:
            if isinstance(window, dict):
                lines.append(_quota_window(window))
        if report.get("error"):
            lines.append(f"⚠️ Note: {report['error']} (showing last successful result)")
        blocks.append(format_text_section(title, lines))
    return format_text_section("Provider quota", ("\n\n".join(blocks),), icon="💳")


def _quota_window(window: dict[str, Any]) -> str:
    name = str(window.get("name") or "Quota")
    remaining, used, limit = (
        window.get(key) for key in ("percent_remaining", "used", "limit")
    )
    unit = str(window.get("unit") or "")
    if used is not None and limit is not None:
        value = f"{used:g}/{limit:g} {unit}".strip()
    elif limit is not None:
        value = f"{limit:g} {unit}".strip()
    elif remaining is not None:
        value = f"{remaining:g}% remaining"
    else:
        value = "Not reported"
    if used is not None and limit is not None and remaining is not None:
        value += f" ({remaining:g}% remaining)"
    text = f"{name}: {value}"
    if window.get("reset_at"):
        text += f"\n  Reset: {window['reset_at']}"
    return text
