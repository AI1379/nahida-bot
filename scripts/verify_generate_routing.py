"""Verify the /api/generate/text model-routing chain against a real config.

Builds ProviderSlots from the given config's providers section (metadata
only — the provider objects are stubs because routing never calls them)
and exercises ``resolve_for_task`` with the exact chain the Gateway
generate route uses: request model spec → ``webapi.generate.model``
config → ``primary`` tag → default provider. Run where the config lives,
e.g. on the server:

    .venv/bin/python scripts/verify_generate_routing.py config-run.yaml
"""

from __future__ import annotations

import os
import sys

from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.providers.router import ModelRouter
from nahida_bot.core.app import _provider_model_entries
from nahida_bot.core.config import load_settings


class _StubProvider:
    """Routing resolution never invokes the provider itself."""


def build_router(settings, *, strip_primary_tags: bool = False) -> ModelRouter:
    slots: list[ProviderSlot] = []
    for pid, cfg in settings.providers.items():
        entries = _provider_model_entries(cfg.models)
        tags_by_model = {
            name: ([] if strip_primary_tags else tags)
            for name, _, tags in entries
            if tags
        }
        slots.append(
            ProviderSlot(
                id=pid,
                provider=_StubProvider(),
                context_builder=None,
                default_model=entries[0][0] if entries else "",
                available_models=[name for name, _, _ in entries],
                capabilities_by_model={},
                tags_by_model=tags_by_model,
            )
        )
    default_id = settings.default_provider or ""
    return ModelRouter(ProviderManager(slots, default_id=default_id))


def describe(routed) -> str:
    if routed is None:
        return "None (route would return 503)"
    model = routed.model or routed.slot.default_model
    return f"{routed.slot.id}/{model} (reason={routed.reason})"


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config-run.yaml"
    settings = load_settings(
        config_path,
        env_path="./.env" if os.path.exists("./.env") else None,
    )

    print(f"default_provider = {settings.default_provider!r}")
    for pid, cfg in settings.providers.items():
        entries = _provider_model_entries(cfg.models)
        for name, _, tags in entries:
            print(f"  slot {pid}: model={name} tags={tags}")

    router = build_router(settings)

    any_model = next(
        (name for slot in router._pm._slots.values() for name in slot.available_models),
        "",
    )
    provider_id = next(iter(router._pm._slots), "")
    compound = f"{provider_id}/{any_model}"

    cases = [
        ("未配置 (explicit='')", ""),
        ("tag: primary", "primary"),
        ("tag: cheap", "cheap"),
        ("固定模型: provider/model 复合", compound),
        ("固定模型: 裸模型名", any_model),
        ("不存在的 spec（应穿过 explicit 落到 primary）", "no-such-tag-xyz"),
    ]
    print("\n== 实际路由链: explicit → primary → default ==")
    failures = 0
    for label, spec in cases:
        routed = router.resolve_for_task(
            "text_generate",
            explicit=spec,
            default_spec="primary",
            fallback="default",
        )
        ok = routed is not None
        failures += 0 if ok else 1
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {label:42s} -> {describe(routed)}")

    print("\n== 兜底腿: 无显式 primary tag 时 ==")
    no_primary_router = build_router(settings, strip_primary_tags=True)
    routed = no_primary_router.resolve_for_task(
        "text_generate",
        explicit="",
        default_spec="primary",
        fallback="default",
    )
    # "primary" implicitly matches every slot's default model, so it still
    # resolves even without explicit tags; fallback="default" only fires
    # when no provider slot exists at all.
    ok = routed is not None and routed.reason == "tag:primary"
    failures += 0 if ok else 1
    print(
        f"[{'OK ' if ok else 'FAIL'}] 全部去掉 primary tag          -> {describe(routed)}"
    )

    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
