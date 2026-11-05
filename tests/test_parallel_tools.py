"""Tool fan-out respects registration, barriers, permissions and transcript order."""

import asyncio
import json

import pytest

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers import ProviderResponse, ToolCall
from nahida_bot.agent.runtime import AgentRunContext, NullAgentRunStore, RunRecorder
from nahida_bot.core.config import AgentConfig
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor
from tests.test_plugin_tool_executor import _ToolCallingProvider


def setup_tools(entries, *, limit=4):
    registry = ToolRegistry()
    for entry in entries:
        registry.register(entry)
    executor = RegistryToolExecutor(registry)
    loop = AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(),
        tool_executor=executor,
        config=AgentConfig(max_parallel_tools=limit),
    )
    return loop, executor.definitions()


def entry(name, handler, *, safe=True):
    return ToolEntry(
        name, name, {"type": "object"}, handler, "test", parallel_safe=safe
    )


async def test_safe_tools_overlap_but_results_remain_in_call_order():
    first_started, second_finished = asyncio.Event(), asyncio.Event()
    finished = []

    async def first():
        first_started.set()
        await second_finished.wait()
        finished.append("first")
        return "first result"

    async def second():
        await first_started.wait()
        finished.append("second")
        second_finished.set()
        return "second result"

    loop, definitions = setup_tools([entry("first", first), entry("second", second)])
    async with asyncio.timeout(2):
        results = await loop._execute_tools(
            response=ProviderResponse(
                content=None,
                tool_calls=[
                    ToolCall("one", "first", {}),
                    ToolCall("two", "second", {}),
                ],
            ),
            tools=definitions,
        )
    assert finished == ["second", "first"]
    assert [m.metadata["tool_call_id"] for m in results] == ["one", "two"]
    assert [json.loads(m.content)["output"] for m in results] == [
        "first result",
        "second result",
    ]


async def test_unsafe_tool_is_a_barrier_between_parallel_read_segments():
    state, readers = 0, 0
    observations = []

    async def read():
        nonlocal readers
        readers += 1
        observed = state
        await asyncio.sleep(0)
        assert state == observed
        observations.append(observed)
        readers -= 1
        return str(observed)

    async def write():
        nonlocal state
        assert readers == 0
        state += 1
        return "written"

    loop, definitions = setup_tools(
        [entry("read", read), entry("write", write, safe=False)]
    )
    calls = [
        ToolCall(str(i), name, {})
        for i, name in enumerate(["read", "read", "write", "read", "read"])
    ]
    results = await loop._execute_tools(
        response=ProviderResponse(content=None, tool_calls=calls), tools=definitions
    )
    assert observations == [0, 0, 1, 1]
    assert [json.loads(m.content)["output"] for m in results] == [
        "0",
        "0",
        "written",
        "1",
        "1",
    ]


@pytest.mark.parametrize(
    "safe,duplicate,limit,expected",
    [
        (True, False, 2, 2),
        (False, False, 4, 1),
        (True, True, 4, 1),
        (True, False, 1, 1),
    ],
)
async def test_fanout_is_bounded_and_unknown_or_duplicate_calls_are_serial(
    safe, duplicate, limit, expected
):
    active = peak = calls = 0

    async def read():
        nonlocal active, peak, calls
        active += 1
        peak = max(peak, active)
        calls += 1
        await asyncio.sleep(0.005)
        active -= 1
        return "ok"

    loop, definitions = setup_tools([entry("read", read, safe=safe)], limit=limit)
    results = await loop._execute_tools(
        response=ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall("same" if duplicate else str(i), "read", {}) for i in range(8)
            ],
        ),
        tools=definitions,
    )
    assert peak == expected and calls == len(results) == 8


async def test_stop_cleans_active_calls_and_marks_unstarted_siblings_cancelled():
    started, stop = asyncio.Event(), asyncio.Event()
    active = calls = 0

    async def read():
        nonlocal active, calls
        active += 1
        calls += 1
        if active == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    loop, definitions = setup_tools([entry("read", read)], limit=2)
    task = asyncio.create_task(
        loop._execute_tools(
            response=ProviderResponse(
                content=None,
                tool_calls=[ToolCall(str(i), "read", {}) for i in range(6)],
            ),
            tools=definitions,
            stop_event=stop,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        results = await asyncio.wait_for(task, 2)
        assert active == 0 and calls == 2
        assert len(results) == 6
        assert all(
            json.loads(m.content)["error"]["code"] == "tool_cancelled" for m in results
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_parallel_ledger_commits_have_monotonic_sequences():
    class Store(NullAgentRunStore):
        def __init__(self):
            self.sequences = []

        async def append_event(self, run_id, *, sequence, **kwargs):
            if sequence % 2:
                await asyncio.sleep(0.005)
            self.sequences.append(sequence)

    async def read():
        await asyncio.sleep(0)
        return "ok"

    store = Store()
    recorder = RunRecorder(store, AgentRunContext(run_id="run"))
    loop, definitions = setup_tools([entry("read", read)])
    results = await loop._execute_tools(
        response=ProviderResponse(
            content=None, tool_calls=[ToolCall(str(i), "read", {}) for i in range(5)]
        ),
        tools=definitions,
        recorder=recorder,
    )
    assert len(results) == 5
    assert store.sequences == list(range(1, len(store.sequences) + 1))


async def test_parallel_failure_does_not_abort_other_readers():
    async def fails():
        raise RuntimeError("broken reader")

    async def succeeds():
        return "ok"

    loop, definitions = setup_tools(
        [entry("fails", fails), entry("succeeds", succeeds)]
    )
    results = await loop._execute_tools(
        response=ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall("one", "fails", {}),
                ToolCall("two", "succeeds", {}),
            ],
        ),
        tools=definitions,
    )
    assert json.loads(results[0].content)["status"] == "error"
    assert json.loads(results[1].content)["output"] == "ok"


async def test_parallel_permission_denial_never_executes_protected_handler():
    from nahida_bot.identity.authorization import AuthorizationGate

    protected_calls = []

    async def protected():
        protected_calls.append(True)
        return "secret"

    async def public():
        return "public"

    secret = ToolEntry(
        "protected",
        "protected",
        {"type": "object"},
        protected,
        "test",
        requires_admin=True,
        parallel_safe=True,
    )
    loop, definitions = setup_tools([secret, entry("public", public)])
    loop.authorization = AuthorizationGate(frozenset({"test:user:admin"}), enabled=True)
    results = await loop._execute_tools(
        response=ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall("one", "protected", {}),
                ToolCall("two", "public", {}),
            ],
        ),
        tools=definitions,
        sender_account_key="test:user:other",
    )
    assert json.loads(results[0].content)["error"]["code"] == "not_authorized"
    assert json.loads(results[1].content)["output"] == "public"
    assert not protected_calls


async def test_batch_freezes_handler_and_policy_during_hot_registration():
    registry = ToolRegistry()
    observations = []

    async def replacement():
        observations.append("unsafe replacement")
        return "unsafe"

    async def old_reader():
        observations.append("old")
        registry.unregister("read")
        registry.register(entry("read", replacement, safe=False))
        await asyncio.sleep(0)
        return "old"

    registry.register(entry("read", old_reader))
    executor = RegistryToolExecutor(registry)
    loop = AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(),
        tool_executor=executor,
    )
    results = await loop._execute_tools(
        response=ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall("one", "read", {}),
                ToolCall("two", "read", {}),
            ],
        ),
        tools=executor.definitions(),
    )
    assert observations == ["old", "old"]
    assert [json.loads(m.content)["output"] for m in results] == ["old", "old"]
    assert not executor.parallel_safe(ToolCall("three", "read", {}))


async def test_external_cancellation_waits_for_all_parallel_handlers_to_clean_up():
    started = asyncio.Event()
    active = 0

    async def read():
        nonlocal active
        active += 1
        if active == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            active -= 1

    loop, definitions = setup_tools([entry("read", read)], limit=2)
    task = asyncio.create_task(
        loop._execute_tools(
            response=ProviderResponse(
                content=None,
                tool_calls=[ToolCall(str(i), "read", {}) for i in range(4)],
            ),
            tools=definitions,
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
