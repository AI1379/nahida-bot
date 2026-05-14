# Runtime Settings TODO

This note tracks runtime-configurable settings that should not be implemented in
the first pass. The first pass only supports session-persisted reasoning display
and reasoning effort.

## Implemented first

- `/reasoning on|off`: session-level override for whether provider reasoning is
  sent back to the chat.
- `/reasoning effort <low|medium|high|max|default>`: session-level override for
  request-time reasoning effort. `default` removes the override and falls back
  to the provider/config default.

Runtime values live in session metadata under `runtime`. `config.yaml` remains
the startup/default source.

## Later runtime settings

- Reasoning display length, currently `router.reasoning_max_chars`.
- Context reasoning replay policy, currently `context.reasoning_policy` and
  `context.max_reasoning_tokens`.
- Agent loop limits such as `agent.max_steps`.
- Multimodal behavior such as `multimodal.image_fallback_mode`,
  `multimodal.media_context_policy`, and `multimodal.max_images_per_turn`.
- Memory retrieval budgets such as `memory.retrieval.max_injected_items` and
  `memory.retrieval.max_injected_chars`.
- Group context behavior such as `router.group_context.enabled`, max messages,
  TTL, and character budget.

Avoid moving startup resources into runtime settings without a lifecycle design:
provider definitions, API keys, base URLs, plugin paths, channel connection
settings, database paths, and server host/port should stay config-only for now.

## Command Parser TODO

The current command handling is simple command-name matching plus per-command
string splitting. That is acceptable for `/reasoning`, but it will not scale
well once runtime commands gain nested subcommands, typed arguments, validation,
aliases, and generated help.

Future design questions:

- Should runtime commands converge under `/settings`, while compatibility aliases
  like `/reasoning` remain?
- Should parser definitions be declarative so plugins can register subcommands
  and validation schemas?
- How should parser errors be localized and shown consistently across channels?
- Should permission checks happen at command, subcommand, or argument level?
- How should parser behavior interact with channel-specific prefixes and group
  mention handling?
