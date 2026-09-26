"""Materializes the Build Agent's generated code into real files on disk.

Parses the ``build-solution`` workflow step's own ``output_text`` (see
``build-generation-v1`` in ``config/prompts/registry.yaml``), which follows
an exact, already-established fenced-code-block convention: one
```python``` block per specialist agent (first line ``# agent: <name>``),
one more ```python``` block for the orchestrator (first line
``# agent: orchestrator``), and exactly one ```tsx``` block for the UI
(first line ``// agent: ui``). Never invents, reformats, or summarizes the
agent's own code - every byte written to disk is exactly what the agent
returned.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "MaterializedBuild",
    "MaterializedCodeError",
    "generate_agent_config_module",
    "generate_backend_service_scaffold",
    "materialize_build",
]

# A ``` fence only counts when it OPENS ITS OWN LINE. Generated agent code
# legitimately contains triple backticks inside string literals (e.g. an
# agent that builds a markdown report with ``lines.append('```text')``);
# without the line anchors those would terminate the block early and
# materialize truncated, non-importable source.
_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]*```(python|tsx)[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_AGENT_MARKER_PATTERN: Final = re.compile(r"^#\s*agent:\s*(.+)$")
_UI_MARKER_PATTERN: Final = re.compile(r"^//\s*agent:\s*ui\s*$", re.IGNORECASE)

_ORCHESTRATOR_MARKER: Final = "orchestrator"

# The generated backend proxy's main.py always does
# ``from orchestrator import OrchestratorAgent`` (see _MAIN_PY_TEMPLATE
# below) - this exact literal class name is the contract between the
# LLM-generated orchestrator module and that deterministic scaffold. A
# differently named class (e.g. ``FactoryOrchestratorAgent``) makes the
# import silently fail closed inside main.py's own broad except, so the
# deployed mission would forever fall back to a generic conversational
# reply instead of ever running its real business logic - fail this
# earlier, at build time, with an actionable error instead.
_ORCHESTRATOR_CLASS_PATTERN: Final = re.compile(r"^class\s+OrchestratorAgent\b", re.MULTILINE)
_DIRECT_FOUNDRY_AGENT_IMPORT_PATTERN: Final = re.compile(
    r"^from agent_framework\.foundry import FoundryAgent(?P<suffix>[^\n]*)$",
    re.MULTILINE,
)
_EXACT_FILE_NAME_COMPARISON_PATTERN: Final = re.compile(
    r"\b[A-Za-z_$][\w$]*\.name\s*(?:===|!==|==|!=)\s*"
    r"(?P<quote>['\"`])[^'\"`\r\n]*\.[A-Za-z0-9]{1,10}(?P=quote)",
)
_UI_JSON_PARSE_PATTERN: Final = re.compile(r"\bJSON\.parse\s*\(")
_OVERBROAD_KEY_MATERIAL_PATTERN: Final = re.compile(
    r"(?:\.includes\s*\(\s*['\"]key['\"]\s*\)|"
    r"/[^/\r\n]*key[^/\r\n]*/[a-z]*\.test\s*\()",
    re.IGNORECASE,
)
_INTERACTIVE_INPUT_PATTERN: Final = re.compile(r"<(?:input|select|textarea)\b", re.IGNORECASE)
_FORM_PATTERN: Final = re.compile(r"<form\b", re.IGNORECASE)
_BUTTON_PATTERN: Final = re.compile(r"<button\b", re.IGNORECASE)
_FILE_INPUT_PATTERN: Final = re.compile(
    r"<input\b[^>]*\btype\s*=\s*['\"]file['\"]", re.IGNORECASE
)
_FILE_TEXT_READ_PATTERN: Final = re.compile(r"\b[A-Za-z_$][\w$]*\.text\s*\(")
_ON_SUBMIT_CALL_PATTERN: Final = re.compile(r"\bonSubmit\s*\(")
_ON_SUBMIT_ATTACHMENTS_PATTERN: Final = re.compile(
    r"\bonSubmit\s*\([^,]+,\s*[A-Za-z_$][\w$]*\s*\)", re.DOTALL
)
_DIRECT_INVOKE_PATTERN: Final = re.compile(
    r"\bfetch\s*\([^)]*['\"`]/invoke(?:/stream)?['\"`]", re.DOTALL
)
_INLINE_STYLE_PATTERN: Final = re.compile(r"\bstyle\s*=", re.IGNORECASE)
_INPUT_TAG_PATTERN: Final = re.compile(r"<input\b[^>]*>", re.IGNORECASE | re.DOTALL)
_HIDDEN_FILE_INPUT_STYLE_PATTERN: Final = re.compile(
    r"\bstyle\s*=\s*\{\{\s*display\s*:\s*(?P<quote>['\"])none(?P=quote)\s*\}\}",
    re.IGNORECASE,
)
_ON_SUBMIT_INLINE_STRINGIFY_PATTERN: Final = re.compile(
    r"\bonSubmit\s*\(\s*JSON\.stringify\(\s*(\{)"
)
_ON_SUBMIT_IDENTIFIER_PATTERN: Final = re.compile(
    r"\bonSubmit\s*\(\s*([A-Za-z_$][\w$]*)\s*[,)]"
)


class MaterializedCodeError(RuntimeError):
    """Raised when the Build Agent's output does not contain a materializable build."""


def _validate_orchestrator_delegations(
    orchestrator_module: str, agent_names: set[str]
) -> None:
    """Require every generated specialist to be invoked and visibly narrated."""

    try:
        tree = ast.parse(orchestrator_module)
    except SyntaxError as exc:
        raise MaterializedCodeError(
            f"The generated orchestrator module is not valid Python: {exc}."
        ) from exc

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def called_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    def configured_agent_name(value: ast.expr) -> str | None:
        if (
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id == "AGENT_FOUNDRY_NAMES"
            and isinstance(value.slice, ast.Constant)
            and isinstance(value.slice.value, str)
        ):
            return value.slice.value
        return None

    def expression_reference(value: ast.expr) -> str | None:
        if isinstance(value, ast.Name):
            return value.id
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
        ):
            return f"self.{value.attr}"
        return None

    def enclosing_scope(node: ast.AST, *, class_scope: bool = False) -> ast.AST:
        current = parents.get(node)
        scope_types = (ast.ClassDef,) if class_scope else (ast.FunctionDef, ast.AsyncFunctionDef)
        while current is not None:
            if isinstance(current, scope_types):
                return current
            current = parents.get(current)
        return tree

    def scoped_reference(value: ast.expr, node: ast.AST) -> tuple[ast.AST, str] | None:
        reference = expression_reference(value)
        if reference is None:
            return None
        scope = enclosing_scope(node, class_scope=reference.startswith("self."))
        return scope, reference

    function_definitions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    unsupported_tool_factories = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "FunctionTool"
        and node.func.attr == "from_function"
    ]
    if unsupported_tool_factories:
        raise MaterializedCodeError(
            "The generated orchestrator calls unsupported "
            "FunctionTool.from_function(...). The installed Agent Framework exposes no "
            "from_function factory; construct FunctionTool(name=<specialist name>, "
            "func=<async delegate>) and await that tool instead."
        )

    dynamic_resolvers: dict[str, dict[str, int]] = {}
    narration_helpers: dict[str, dict[str, int]] = {}
    for function_name, function in function_definitions.items():
        argument_names = [argument.arg for argument in function.args.args]
        method_offset = int(bool(argument_names and argument_names[0] in {"self", "cls"}))
        call_positions = {
            name: index - method_offset
            for index, name in enumerate(argument_names)
            if index >= method_offset
        }
        mapped_aliases: dict[str, str] = {}
        mapped_parameters: set[str] = set()
        resolver_agent_references: set[str] = set()
        has_awaited_run = False

        for node in ast.walk(function):
            assigned_value: ast.expr | None = None
            assigned_targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                assigned_value = node.value
                assigned_targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                assigned_value = node.value
                assigned_targets = [node.target]
            if (
                isinstance(assigned_value, ast.Subscript)
                and isinstance(assigned_value.value, ast.Name)
                and assigned_value.value.id == "AGENT_FOUNDRY_NAMES"
                and isinstance(assigned_value.slice, ast.Name)
                and assigned_value.slice.id in call_positions
            ):
                for target in assigned_targets:
                    if isinstance(target, ast.Name):
                        mapped_aliases[target.id] = assigned_value.slice.id

            if isinstance(node, ast.Call) and called_name(node) == "MissionFoundryAgent":
                for keyword in node.keywords:
                    if keyword.arg != "agent_name":
                        continue
                    mapped_parameter: str | None = None
                    if (
                        isinstance(keyword.value, ast.Subscript)
                        and isinstance(keyword.value.value, ast.Name)
                        and keyword.value.value.id == "AGENT_FOUNDRY_NAMES"
                        and isinstance(keyword.value.slice, ast.Name)
                        and keyword.value.slice.id in call_positions
                    ):
                        mapped_parameter = keyword.value.slice.id
                    elif isinstance(keyword.value, ast.Name):
                        mapped_parameter = mapped_aliases.get(keyword.value.id)
                    if mapped_parameter is None:
                        continue
                    mapped_parameters.add(mapped_parameter)
                    parent = parents.get(node)
                    targets: list[ast.expr] = []
                    if isinstance(parent, ast.Assign):
                        targets = parent.targets
                    elif isinstance(parent, ast.AnnAssign):
                        targets = [parent.target]
                    resolver_agent_references.update(
                        reference
                        for target in targets
                        if (reference := expression_reference(target)) is not None
                    )

            if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                continue
            awaited_call = node.value
            if (
                isinstance(awaited_call.func, ast.Attribute)
                and awaited_call.func.attr == "run"
                and expression_reference(awaited_call.func.value)
                in resolver_agent_references
            ):
                has_awaited_run = True
            if (
                isinstance(awaited_call.func, ast.Name)
                and awaited_call.func.id in call_positions
            ):
                forwarded_parameters = {
                    argument.id: call_positions[argument.id]
                    for argument in awaited_call.args
                    if isinstance(argument, ast.Name)
                    and argument.id in call_positions
                }
                if forwarded_parameters:
                    narration_helpers.setdefault(function_name, {}).update(
                        forwarded_parameters
                    )

        if has_awaited_run and mapped_parameters:
            dynamic_resolvers[function_name] = {
                parameter: call_positions[parameter] for parameter in mapped_parameters
            }

    wrapper_agents: dict[str, set[str]] = {}
    resolver_call_agents: dict[str, set[str]] = {}
    for function_name, function in function_definitions.items():
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            resolver_name = called_name(node) or ""
            resolver_parameters = dynamic_resolvers.get(resolver_name)
            if resolver_parameters is None or not isinstance(
                parents.get(node), ast.Await
            ):
                continue
            for parameter, position in resolver_parameters.items():
                argument: ast.expr | None = (
                    node.args[position] if position < len(node.args) else None
                )
                if argument is None:
                    argument = next(
                        (
                            keyword.value
                            for keyword in node.keywords
                            if keyword.arg == parameter
                        ),
                        None,
                    )
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.value in agent_names
                ):
                    wrapper_agents.setdefault(function_name, set()).add(argument.value)
                    resolver_call_agents.setdefault(resolver_name, set()).add(argument.value)

    def nested_delegate_maps_and_runs(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        delegate_name: str,
        mapped_parameter: str,
    ) -> bool:
        delegate = next(
            (
                node
                for node in ast.walk(function)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == delegate_name
                and parents.get(node) is function
            ),
            None,
        )
        if delegate is None:
            return False

        agent_references: set[str] = set()
        for node in ast.walk(delegate):
            if not isinstance(node, ast.Call) or called_name(node) != "MissionFoundryAgent":
                continue
            mapped = any(
                keyword.arg == "agent_name"
                and isinstance(keyword.value, ast.Subscript)
                and isinstance(keyword.value.value, ast.Name)
                and keyword.value.value.id == "AGENT_FOUNDRY_NAMES"
                and isinstance(keyword.value.slice, ast.Name)
                and keyword.value.slice.id == mapped_parameter
                for keyword in node.keywords
            )
            if not mapped:
                continue
            parent = parents.get(node)
            targets: list[ast.expr] = []
            if isinstance(parent, ast.Assign):
                targets = parent.targets
            elif isinstance(parent, ast.AnnAssign):
                targets = [parent.target]
            agent_references.update(
                reference
                for target in targets
                if (reference := expression_reference(target)) is not None
            )

        return any(
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "run"
            and expression_reference(node.value.func.value) in agent_references
            for node in ast.walk(delegate)
        )

    dynamic_tool_helpers: dict[str, str] = {}
    dynamic_helper_narration: set[str] = set()
    for function_name, resolver_parameters in dynamic_resolvers.items():
        function = function_definitions[function_name]
        for mapped_parameter in resolver_parameters:
            tool_references: set[str] = set()
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or called_name(node) != "FunctionTool":
                    continue
                name_parameter = next(
                    (
                        keyword.value.id
                        for keyword in node.keywords
                        if keyword.arg == "name" and isinstance(keyword.value, ast.Name)
                    ),
                    None,
                )
                delegate_name = next(
                    (
                        keyword.value.id
                        for keyword in node.keywords
                        if keyword.arg in {"coroutine", "func"}
                        and isinstance(keyword.value, ast.Name)
                    ),
                    None,
                )
                if (
                    name_parameter != mapped_parameter
                    or delegate_name is None
                    or not nested_delegate_maps_and_runs(
                        function, delegate_name, mapped_parameter
                    )
                ):
                    continue
                parent = parents.get(node)
                targets: list[ast.expr] = []
                if isinstance(parent, ast.Assign):
                    targets = parent.targets
                elif isinstance(parent, ast.AnnAssign):
                    targets = [parent.target]
                tool_references.update(
                    reference
                    for target in targets
                    if (reference := expression_reference(target)) is not None
                )

            tool_is_awaited = any(
                isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and expression_reference(node.value.func) in tool_references
                for node in ast.walk(function)
            )
            if not tool_references or not tool_is_awaited:
                continue
            dynamic_tool_helpers[function_name] = mapped_parameter

            parameter_narration_count = 0
            for node in ast.walk(function):
                if not (
                    isinstance(node, ast.Await)
                    and isinstance(node.value, ast.Call)
                    and called_name(node.value) == "on_progress"
                ):
                    continue
                if any(
                    isinstance(argument, ast.JoinedStr)
                    and any(
                        isinstance(part, ast.FormattedValue)
                        and isinstance(part.value, ast.Name)
                        and part.value.id == mapped_parameter
                        for part in argument.values
                    )
                    for argument in node.value.args
                ):
                    parameter_narration_count += 1
            if parameter_narration_count >= 2:
                dynamic_helper_narration.add(function_name)

    configured_name_aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            mapped_name = configured_agent_name(node.value)
            if mapped_name is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        configured_name_aliases.setdefault(target.id, set()).add(mapped_name)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            mapped_name = configured_agent_name(node.value)
            if mapped_name is not None and isinstance(node.target, ast.Name):
                configured_name_aliases.setdefault(node.target.id, set()).add(mapped_name)

    direct_aliases: dict[tuple[ast.AST, str], str] = {}
    direct_agent_instances: dict[tuple[ast.AST, str], str] = {}
    directly_awaited_agents: set[str] = set()
    ordered_nodes = sorted(
        ast.walk(tree),
        key=lambda node: (
            getattr(node, "lineno", -1),
            getattr(node, "col_offset", -1),
        ),
    )
    for node in ordered_nodes:
        assigned_value: ast.expr | None = None
        assigned_targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            assigned_value = node.value
            assigned_targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assigned_value = node.value
            assigned_targets = [node.target]

        mapped_name = (
            configured_agent_name(assigned_value)
            if assigned_value is not None
            else None
        )
        if mapped_name in agent_names:
            for target in assigned_targets:
                reference = scoped_reference(target, node)
                if reference is not None:
                    direct_aliases[reference] = mapped_name

        if (
            isinstance(assigned_value, ast.Call)
            and called_name(assigned_value) == "MissionFoundryAgent"
        ):
            agent_name: str | None = None
            for keyword in assigned_value.keywords:
                if keyword.arg != "agent_name":
                    continue
                agent_name = configured_agent_name(keyword.value)
                if agent_name is None:
                    alias_reference = scoped_reference(keyword.value, node)
                    if alias_reference is not None:
                        agent_name = direct_aliases.get(alias_reference)
            if agent_name in agent_names:
                for target in assigned_targets:
                    instance_reference = scoped_reference(target, node)
                    if instance_reference is not None:
                        direct_agent_instances[instance_reference] = agent_name

        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        awaited_call = node.value
        if not (
            isinstance(awaited_call.func, ast.Attribute)
            and awaited_call.func.attr == "run"
        ):
            continue
        instance_reference = scoped_reference(awaited_call.func.value, node)
        if instance_reference is not None:
            agent_name = direct_agent_instances.get(instance_reference)
            if agent_name is not None:
                directly_awaited_agents.add(agent_name)

    mapped_agents: set[str] = set()
    indirect_tool_agents: dict[str, str] = {}
    function_tool_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or called_name(node) != "FunctionTool":
            continue
        function_tool_count += 1
        tool_agent_name = next(
            (
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
                and keyword.value.value in agent_names
            ),
            None,
        )
        wrapper_name = next(
            (
                keyword.value.attr
                for keyword in node.keywords
                if keyword.arg in {"coroutine", "func"}
                and isinstance(keyword.value, ast.Attribute)
            ),
            None,
        )
        if (
            tool_agent_name is None
            or wrapper_name is None
            or tool_agent_name not in wrapper_agents.get(wrapper_name, set())
        ):
            continue
        parent = parents.get(node)
        targets: list[ast.expr] = []
        if isinstance(parent, ast.Assign):
            targets = parent.targets
        elif isinstance(parent, ast.AnnAssign):
            targets = [parent.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                indirect_tool_agents[target.attr] = tool_agent_name
                mapped_agents.add(tool_agent_name)

    dynamically_delegated_agents: set[str] = set()
    for helper_name in dynamic_tool_helpers:
        helper_agents = resolver_call_agents.get(helper_name, set())
        dynamically_delegated_agents.update(helper_agents)
        mapped_agents.update(helper_agents)
    function_tool_count += len(dynamically_delegated_agents)

    narration_counts = {name: 0 for name in agent_names}
    awaited_tool_agents: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and called_name(node) == "MissionFoundryAgent":
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg != "agent_name":
                    continue
                mapped_name = configured_agent_name(value)
                if mapped_name is not None:
                    mapped_agents.add(mapped_name)
                elif isinstance(value, ast.Name):
                    mapped_agents.update(configured_name_aliases.get(value.id, set()))
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        awaited_call = node.value
        if (
            isinstance(awaited_call.func, ast.Attribute)
            and isinstance(awaited_call.func.value, ast.Name)
            and awaited_call.func.value.id == "self"
        ):
            indirect_agent = indirect_tool_agents.get(awaited_call.func.attr)
            if indirect_agent is not None:
                awaited_tool_agents.add(indirect_agent)
        awaited_name = called_name(awaited_call)
        narration_arguments: list[ast.expr] = []
        if awaited_name == "on_progress":
            narration_arguments = awaited_call.args
        elif awaited_name in narration_helpers:
            for parameter, position in narration_helpers[awaited_name].items():
                if position < len(awaited_call.args):
                    narration_arguments.append(awaited_call.args[position])
                    continue
                narration_arguments.extend(
                    keyword.value
                    for keyword in awaited_call.keywords
                    if keyword.arg == parameter
                )
        else:
            continue
        narration = next(
            (
                argument.value
                for argument in narration_arguments
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            ),
            None,
        )
        if narration is None:
            continue
        for agent_name in agent_names:
            if agent_name in narration:
                narration_counts[agent_name] += 1

    for helper_name in dynamic_helper_narration:
        for agent_name in resolver_call_agents.get(helper_name, set()):
            narration_counts[agent_name] = max(narration_counts[agent_name], 2)

    missing_mappings = sorted(agent_names - mapped_agents)
    missing_narration = sorted(
        name for name, count in narration_counts.items() if count < 2
    )
    failures: list[str] = []
    if function_tool_count < len(agent_names):
        failures.append(
            f"FunctionTool delegations ({function_tool_count}/{len(agent_names)})"
        )
    executed_specialist_count = len(
        directly_awaited_agents | awaited_tool_agents | dynamically_delegated_agents
    )
    if executed_specialist_count < len(agent_names):
        failures.append(
            f"awaited specialist runs ({executed_specialist_count}/{len(agent_names)})"
        )
    if missing_mappings:
        failures.append(
            "AGENT_FOUNDRY_NAMES mappings for " + ", ".join(missing_mappings)
        )
    if missing_narration:
        failures.append(
            "start/completion progress narration for " + ", ".join(missing_narration)
        )
    if failures:
        raise MaterializedCodeError(
            "The generated orchestrator does not execute and visibly report every "
            "specialist. Missing: " + "; ".join(failures) + "."
        )


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "agent"


def _use_mission_foundry_runtime(code: str) -> str:
    """Routes generated Foundry calls through Genie's deterministic adapter."""

    return _DIRECT_FOUNDRY_AGENT_IMPORT_PATTERN.sub(
        r"from mission_foundry_runtime import MissionFoundryAgent as FoundryAgent\g<suffix>",
        code,
    )


def _extract_balanced_braces(text: str, open_index: int) -> str | None:
    """Returns ``text[open_index:...]`` through its matching ``}``, inclusive.

    String/template literals are skipped while counting depth so a stray
    ``{``/``}`` inside quoted content (e.g. a label string) never breaks the
    match.
    """

    depth = 0
    in_string: str | None = None
    i = open_index
    while i < len(text):
        ch = text[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in "'\"`":
            in_string = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : i + 1]
        i += 1
    return None


def _top_level_object_entries(object_literal: str) -> list[str]:
    """Splits a ``{...}`` object literal's body into top-level ``key: value`` entries."""

    body = object_literal.strip().removeprefix("{").removesuffix("}")

    entries: list[str] = []
    current: list[str] = []
    depth = 0
    in_string: str | None = None
    i = 0
    while i < len(body):
        ch = body[i]
        if in_string is not None:
            current.append(ch)
            if ch == "\\":
                if i + 1 < len(body):
                    current.append(body[i + 1])
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in "'\"`":
            in_string = ch
            current.append(ch)
            i += 1
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            entries.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        entries.append("".join(current))
    return [entry for entry in entries if entry.strip()]


def _submit_payload_literals(ui_component: str) -> list[str]:
    """Extracts object literals serialized into the ``onSubmit`` message."""

    literals: list[str] = []
    for match in _ON_SUBMIT_INLINE_STRINGIFY_PATTERN.finditer(ui_component):
        literal = _extract_balanced_braces(ui_component, match.start(1))
        if literal is not None:
            literals.append(literal)

    for submit_match in _ON_SUBMIT_IDENTIFIER_PATTERN.finditer(ui_component):
        message_identifier = submit_match.group(1)
        stringify_assignment_pattern = re.compile(
            r"\b(?:const|let|var)\s+"
            + re.escape(message_identifier)
            + r"\s*=\s*JSON\.stringify\(\s*(\{|[A-Za-z_$][\w$]*)"
        )
        stringify_assignment = None
        for match in stringify_assignment_pattern.finditer(ui_component[: submit_match.start()]):
            stringify_assignment = match
        if stringify_assignment is None:
            continue

        stringify_argument = stringify_assignment.group(1)
        if stringify_argument == "{":
            literal = _extract_balanced_braces(ui_component, stringify_assignment.start(1))
            if literal is not None:
                literals.append(literal)
            continue

        payload_assignment_pattern = re.compile(
            r"\b(?:const|let|var)\s+"
            + re.escape(stringify_argument)
            + r"\s*=\s*(\{)"
        )
        payload_open_index: int | None = None
        for match in payload_assignment_pattern.finditer(
            ui_component[: stringify_assignment.start()]
        ):
            payload_open_index = match.start(1)
        if payload_open_index is not None:
            literal = _extract_balanced_braces(ui_component, payload_open_index)
            if literal is not None:
                literals.append(literal)
    return literals


def _has_nested_object_value(object_literal: str) -> bool:
    for entry in _top_level_object_entries(object_literal):
        _, separator, value = entry.partition(":")
        if separator and value.strip().startswith("{"):
            return True
    return False


def _has_disallowed_inline_style(ui_component: str) -> bool:
    """Allows only the non-visual hidden native control behind a file dropzone."""

    def remove_allowed_hidden_style(match: re.Match[str]) -> str:
        input_tag = match.group(0)
        if not _FILE_INPUT_PATTERN.search(input_tag):
            return input_tag
        return _HIDDEN_FILE_INPUT_STYLE_PATTERN.sub("", input_tag)

    normalized = _INPUT_TAG_PATTERN.sub(remove_allowed_hidden_style, ui_component)
    return _INLINE_STYLE_PATTERN.search(normalized) is not None


@dataclass(frozen=True)
class MaterializedBuild:
    """The Build Agent's generated code, parsed into real, named files."""

    agent_modules: dict[str, str] = field(default_factory=dict)
    orchestrator_module: str | None = None
    ui_component: str | None = None

    def write_to_directory(
        self, root: Path, *, backend_service_scaffold: dict[str, str] | None = None
    ) -> list[Path]:
        """Writes every parsed piece to real files under ``root``, returns their paths.

        ``backend_service_scaffold`` (see ``generate_backend_service_scaffold``),
        when supplied, is written alongside the orchestrator/agent modules so
        ``root`` is a real, buildable backend service directory (with its own
        ``Dockerfile``) ready for ``BackendDeploymentService.deploy``.
        """

        root.mkdir(parents=True, exist_ok=True)
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for agent_name, code in self.agent_modules.items():
            path = agents_dir / f"{_slugify(agent_name)}.py"
            path.write_text(_use_mission_foundry_runtime(code), encoding="utf-8")
            written.append(path)

        if self.orchestrator_module is not None:
            path = root / "orchestrator.py"
            path.write_text(
                _use_mission_foundry_runtime(self.orchestrator_module), encoding="utf-8"
            )
            written.append(path)

        if self.ui_component is not None:
            path = root / "MissionApp.tsx"
            path.write_text(self.ui_component, encoding="utf-8")
            written.append(path)

        for file_name, content in (backend_service_scaffold or {}).items():
            path = root / file_name
            path.write_text(content, encoding="utf-8")
            written.append(path)

        return written


def materialize_build(output_text: str) -> MaterializedBuild:
    """Parses the Build Agent's ``output_text`` into a ``MaterializedBuild``.

    Raises ``MaterializedCodeError`` if no fenced code blocks at all could
    be found - a real failure, never silently treated as an empty build.
    """

    agent_modules: dict[str, str] = {}
    orchestrator_module: str | None = None
    ui_component: str | None = None

    for language, body in _FENCE_PATTERN.findall(output_text):
        lines = body.splitlines()
        first_line = lines[0].strip() if lines else ""

        if language.lower() == "python":
            marker_match = _AGENT_MARKER_PATTERN.match(first_line)
            if marker_match is None:
                continue
            agent_name = marker_match.group(1).strip()
            if agent_name.lower() == _ORCHESTRATOR_MARKER:
                orchestrator_module = body.strip("\n")
            else:
                agent_modules[agent_name] = body.strip("\n")
        elif language.lower() == "tsx":
            if _UI_MARKER_PATTERN.match(first_line):
                ui_component = body.strip("\n")

    if not agent_modules and orchestrator_module is None and ui_component is None:
        raise MaterializedCodeError(
            "No materializable agent, orchestrator, or UI code block was found in "
            "the build-solution step's output."
        )

    if orchestrator_module is not None and not _ORCHESTRATOR_CLASS_PATTERN.search(orchestrator_module):
        raise MaterializedCodeError(
            "The generated orchestrator module does not define a top-level "
            "'class OrchestratorAgent' - this mission's backend proxy always "
            "does 'from orchestrator import OrchestratorAgent', so any other "
            "class name would silently fall back to a generic conversational "
            "reply instead of running this mission's real pipeline."
        )

    missing_components: list[str] = []
    if not agent_modules:
        missing_components.append("specialist agent modules")
    if orchestrator_module is None:
        missing_components.append("the orchestrator module")
    if ui_component is None:
        missing_components.append("the UI component")
    if missing_components:
        raise MaterializedCodeError(
            "The generated build is incomplete and cannot be connected end to end. "
            "Missing: " + ", ".join(missing_components) + "."
        )

    _validate_orchestrator_delegations(orchestrator_module, set(agent_modules))

    if ui_component is not None and _EXACT_FILE_NAME_COMPARISON_PATTERN.search(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI compares an uploaded file's name to an exact "
            "literal. Generated prototypes must validate uploaded content and file "
            "type, never an end-user-controlled filename or example filename."
        )

    has_file_input = bool(
        ui_component is not None and _FILE_INPUT_PATTERN.search(ui_component)
    )

    if has_file_input and ui_component is not None and _UI_JSON_PARSE_PATTERN.search(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI parses uploaded JSON content before submission. "
            "Mission UIs may validate only that an upload exists, is readable and "
            "non-empty, and has the required general file type. The provisioned "
            "backend/orchestrator must own schema and business-rule validation so "
            "every upload reaches the real mission process."
        )

    if (
        has_file_input
        and ui_component is not None
        and _OVERBROAD_KEY_MATERIAL_PATTERN.search(ui_component)
    ):
        raise MaterializedCodeError(
            "The generated mission UI uses a broad '*key*' substring scan. That can "
            "reject legitimate uploaded content or filenames before the provisioned "
            "backend runs. Explicit key-artifact and blindness policy checks belong "
            "in the mission's backend/orchestrator."
        )

    if ui_component is not None and _DIRECT_INVOKE_PATTERN.search(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI calls the backend invoke endpoint directly. "
            "Every generated component must hand off through its onSubmit prop so "
            "the deterministic Mission Queue owns backend transport and streaming."
        )

    if ui_component is not None and _has_disallowed_inline_style(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI contains an inline style prop. Mission Input must "
            "use semantic HTML and the deterministic shell's genie-form, genie-form-section, "
            "genie-form-grid, genie-field, genie-field-help, genie-actions, genie-dropzone, "
            "and genie-btn classes so every prototype remains visually coherent."
        )

    if not _ON_SUBMIT_CALL_PATTERN.search(ui_component):
        raise MaterializedCodeError(
            "The generated mission UI never calls its onSubmit prop. Every prototype "
            "must hand one flat JSON message to the deterministic shell so the "
            "provisioned backend and orchestrator actively run."
        )

    if has_file_input and ui_component is not None:
        if not _FILE_TEXT_READ_PATTERN.search(ui_component):
            raise MaterializedCodeError(
                "The generated mission UI renders a file input but never reads the "
                "selected File with File.text() before submission."
            )
        if not _ON_SUBMIT_ATTACHMENTS_PATTERN.search(ui_component):
            raise MaterializedCodeError(
                "The generated mission UI renders a file input but does not pass an "
                "attachments array to onSubmit. Uploaded content must reach the "
                "provisioned backend unchanged."
            )

    if ui_component is not None:
        missing_semantic_classes: list[str] = []
        interactive_control_count = len(
            _INTERACTIVE_INPUT_PATTERN.findall(ui_component)
        )
        if _FORM_PATTERN.search(ui_component) and "genie-form" not in ui_component:
            missing_semantic_classes.append("genie-form")
        if interactive_control_count > 1:
            if "genie-form-section" not in ui_component:
                missing_semantic_classes.append("genie-form-section")
            if "genie-form-grid" not in ui_component:
                missing_semantic_classes.append("genie-form-grid")
        if (
            interactive_control_count
            and "genie-field" not in ui_component
        ):
            missing_semantic_classes.append("genie-field")
        if has_file_input and "genie-dropzone" not in ui_component:
            missing_semantic_classes.append("genie-dropzone")
        if _BUTTON_PATTERN.search(ui_component) and "genie-btn" not in ui_component:
            missing_semantic_classes.append("genie-btn")
        if missing_semantic_classes:
            raise MaterializedCodeError(
                "The generated mission UI does not use the deterministic shell's "
                "semantic visual system. Add these required classes: "
                + ", ".join(missing_semantic_classes)
                + "."
            )

    if ui_component is not None and any(
        _has_nested_object_value(literal) for literal in _submit_payload_literals(ui_component)
    ):
        raise MaterializedCodeError(
            "The generated mission UI's submit payload groups fields inside nested "
            "objects (for example {\"evaluation_config\": {\"primary_model_id\": ...}}). "
            "The UI/orchestrator contract requires one flat JSON object with exactly "
            "one key per rendered field - a nested payload silently breaks every "
            "'config.get(...)' read in the orchestrator's json.loads(ui_message)."
        )

    return MaterializedBuild(
        agent_modules=agent_modules,
        orchestrator_module=orchestrator_module,
        ui_component=ui_component,
    )


_MAIN_PY_TEMPLATE = '''"""Real, deterministically generated backend proxy for one deployed mission.

Never LLM-authored: this file is generated by
``app.deploy_launch.code_materializer.generate_backend_service_scaffold``
every time a mission is deployed. Its job is to receive the mission UI's
same-origin calls, persist every uploaded attachment's FULL content to this
mission's own working directory (never truncated or folded into a single
chat turn, so every requirement in an uploaded package is genuinely
considered), and run this mission's own real, deterministic Orchestrator
Agent (``orchestrator.py``, generated alongside this file) against it. Only
when that structured run cannot proceed (for example, a free-form
conversational request rather than the JSON payload this mission's own
input zone(s) compose) does it fall back to a direct conversational reply
from this mission's Orchestrator Agent, already provisioned in Azure AI
Foundry during the "Deploy Agents to Foundry" pipeline step - the
Orchestrator Agent itself is never reachable directly from the browser.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from agent_framework.foundry import FoundryAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title={mission_title!r})
{authentication_middleware}

_logger = logging.getLogger("mission.backend")

_ORCHESTRATOR_AGENT_NAME = "{orchestrator_agent_name}"

# Generous but bounded - guards against unbounded memory/disk usage from an
# oversized upload (OWASP: resource consumption). Plain text only, no binary
# storage - kept deliberately simple.
_MAX_ATTACHMENT_CHARS = 200_000


class Attachment(BaseModel):
    name: str
    content: str


class InvokeRequest(BaseModel):
    message: str
    attachments: list[Attachment] = []


class InvokeResponse(BaseModel):
    output_text: str


def _check_attachment_size(attachments: list[Attachment]) -> None:
    total_chars = sum(len(attachment.content) for attachment in attachments)
    if total_chars > _MAX_ATTACHMENT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Attached file content exceeds the {{_MAX_ATTACHMENT_CHARS:,}} character limit.",
        )


def _persist_attachments(attachments: list[Attachment]) -> None:
    """Writes every uploaded attachment's FULL content to this mission's own
    working directory so the real, deterministic Orchestrator/specialist
    agent pipeline can read every uploaded requirement in full - never
    truncated or summarized into a single chat turn, so every requirement
    in an uploaded package is genuinely taken into consideration.
    """
    if not attachments:
        return
    working_dir = Path(os.getenv("FACTORY_WORKING_DIR", "/data"))
    working_dir.mkdir(parents=True, exist_ok=True)
    for attachment in attachments:
        # Strip any path components from the browser-supplied file name so
        # a crafted name cannot escape the working directory (OWASP: path
        # traversal).
        safe_name = Path(attachment.name).name
        if not safe_name:
            continue
        (working_dir / safe_name).write_text(attachment.content, encoding="utf-8")


def _compose_message(request: InvokeRequest) -> str:
    """Folds any uploaded file attachments into one message for a direct
    conversational reply from the Orchestrator Agent.

    Only used as a fallback (see ``_run_orchestrator_pipeline`` below) when
    this mission's real, deterministic Orchestrator pipeline cannot accept
    the submitted request. Each attachment's full text content is embedded
    ahead of the user's own message, clearly labeled by file name.
    """
    if not request.attachments:
        return request.message
    sections = [
        f"--- Attached file: {{attachment.name}} ---\\n{{attachment.content}}"
        for attachment in request.attachments
    ]
    return "\\n\\n".join([*sections, request.message])


def _is_structured_mission_request(message: str) -> bool:
    try:
        json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


async def _run_orchestrator_pipeline(
    message: str, *, on_progress: Callable[[str], Awaitable[None]] | None = None
) -> str | None:
    """Runs this mission's real, deterministic Orchestrator pipeline.

    ``on_progress``, when given, is forwarded to ``OrchestratorAgent.run``
    so each specialist hand-off can be narrated to the caller AS IT
    HAPPENS rather than only once the entire pipeline has finished (see
    ``_stream_agent_response`` below, which is what makes the mission UI's
    live Agent Pipeline animation actually light up node by node instead
    of jumping straight from all-pending to all-complete).

    Returns the pipeline's own structured result as JSON text, or ``None``
    when this mission's Orchestrator cannot accept the submitted request
    (for example, ``message`` is a free-form conversational request rather
    than the JSON-encoded configuration this mission's own input zone(s)
    compose) - callers fall back to a direct conversational reply in that
    case.
    """
    try:
        from orchestrator import OrchestratorAgent
    except ImportError:
        if _is_structured_mission_request(message):
            raise
        return None
    try:
        orchestrator = OrchestratorAgent()
        run_parameters = inspect.signature(orchestrator.run).parameters.values()
        supports_progress = any(
            parameter.name == "on_progress"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in run_parameters
        )
        if supports_progress:
            result = await orchestrator.run(message, on_progress=on_progress)
        else:
            # An orchestrator generated before the on_progress contract can
            # still run; it simply cannot emit specialist hand-off narration.
            result = await orchestrator.run(message)
    except Exception:
        # The Orchestrator is generated code whose exact failure modes
        # cannot be enumerated in advance. A structured request came from
        # the generated mission UI and must never be disguised as a
        # successful conversational response; propagate it so deployed
        # acceptance tests fail and trigger the bounded repair workflow.
        if _is_structured_mission_request(message):
            _logger.exception("Structured mission orchestrator execution failed.")
            raise
        # Plain-text quick requests are outside the generated form contract
        # and may still use the mission's conversational Foundry fallback.
        _logger.warning(
            "Orchestrator pipeline run did not complete; falling back to a "
            "conversational reply.",
            exc_info=True,
        )
        return None
    return json.dumps(result)


async def _conversational_reply(message: str) -> str:
    endpoint = os.environ["FOUNDRY_ENDPOINT"]
    project_name = os.environ["FOUNDRY_PROJECT_NAME"]
    async with DefaultAzureCredential() as credential:
        async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
            agent = FoundryAgent(
                project_client=project_client,
                agent_name=_ORCHESTRATOR_AGENT_NAME,
                agent_version=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "1"),
            )
            response = await agent.run(message)
            return (getattr(response, "text", None) or "").strip()


@app.get("/health")
async def health() -> dict[str, str]:
    return {{"status": "ok"}}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    try:
        from orchestrator import OrchestratorAgent

        OrchestratorAgent()
    except Exception as exc:
        _logger.exception("Generated mission orchestrator failed readiness validation.")
        raise HTTPException(
            status_code=503,
            detail="Generated mission orchestrator is not ready.",
        ) from exc
    return {{"status": "ready"}}


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    _check_attachment_size(request.attachments)
    _persist_attachments(request.attachments)

    pipeline_output = await _run_orchestrator_pipeline(request.message)
    if pipeline_output is not None:
        return InvokeResponse(output_text=pipeline_output)

    output_text = await _conversational_reply(_compose_message(request))
    return InvokeResponse(output_text=output_text)


async def _stream_agent_response(request: InvokeRequest) -> AsyncIterator[str]:
    """Yields Server-Sent Events as the mission's response streams in.

    Each event line is a JSON object: ``{{"progress": "<agent hand-off>"}}``
    for orchestrator progress, ``{{"delta": "<incremental text>"}}`` for
    conversational output, then exactly one final
    ``{{"done": true, "output_text": "<full response>"}}`` once finished -
    lets the mission UI show the Orchestrator genuinely working in real
    time instead of waiting on one long blocking call. When this mission's
    real, deterministic Orchestrator pipeline runs, it is started as a
    background task and its own ``on_progress`` hand-off narration
    (e.g. "Handing off to <Agent Name>...") is relayed as its own delta
    event THE MOMENT each specialist agent starts/finishes - never
    buffered until the whole pipeline completes - so the mission UI's live
    Agent Pipeline visualization can actually light up node by node while
    real work is happening, not just flash from all-pending to
    all-complete once the entire run is already done.
    """
    _check_attachment_size(request.attachments)
    _persist_attachments(request.attachments)

    progress_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _on_progress(narration: str) -> None:
        await progress_queue.put(narration)

    pipeline_result: dict[str, str | None] = {{"output": None}}

    async def _run_pipeline() -> None:
        try:
            pipeline_result["output"] = await _run_orchestrator_pipeline(
                request.message, on_progress=_on_progress
            )
        finally:
            await progress_queue.put(None)

    pipeline_task = asyncio.create_task(_run_pipeline())
    while True:
        narration = await progress_queue.get()
        if narration is None:
            break
        yield "data: " + json.dumps(dict(progress=narration)) + "\\n\\n"
    await pipeline_task

    pipeline_output = pipeline_result["output"]
    if pipeline_output is not None:
        yield "data: " + json.dumps(dict(done=True, output_text=pipeline_output)) + "\\n\\n"
        return

    message = _compose_message(request)
    endpoint = os.environ["FOUNDRY_ENDPOINT"]
    project_name = os.environ["FOUNDRY_PROJECT_NAME"]
    async with DefaultAzureCredential() as credential:
        async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
            agent = FoundryAgent(
                project_client=project_client,
                agent_name=_ORCHESTRATOR_AGENT_NAME,
                agent_version=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "1"),
            )
            accumulated = ""
            async for update in agent.run(message, tools=None, stream=True):
                delta = getattr(update, "text", None) or ""
                if not delta:
                    continue
                accumulated += delta
                yield "data: " + json.dumps(dict(delta=delta)) + "\\n\\n"
            yield "data: " + json.dumps(dict(done=True, output_text=accumulated.strip())) + "\\n\\n"


@app.post("/invoke/stream")
async def invoke_stream(request: InvokeRequest) -> StreamingResponse:
    return StreamingResponse(_stream_agent_response(request), media_type="text/event-stream")
'''

_AGENT_CONFIG_PY_TEMPLATE = '''"""Deterministically generated agent-name configuration - never LLM-authored.

Maps this mission's own logical specialist agent names (exactly as named in
the approved architecture's "## Multi-Agent Workflow" section) to their
real, already-provisioned Azure AI Foundry agent names, written fresh by
``app.deploy_launch.code_materializer.generate_agent_config_module`` every
time this mission is deployed. The generated ``orchestrator.py``'s
``call_<agent>`` delegation tools import this module and look up each
specialist's Foundry agent name here - never hardcoding it.
"""
from __future__ import annotations

AGENT_FOUNDRY_NAMES: dict[str, str] = {agent_foundry_names!r}
'''

_MISSION_FOUNDRY_RUNTIME_PY = '''"""Stable runtime boundary for generated mission agents.

Generated code depends on this small Genie-owned API instead of coupling itself
to changing Azure AI Projects and Microsoft Agent Framework constructor and
response details.
"""
from __future__ import annotations

import json
import os
from typing import Any

from agent_framework.foundry import FoundryAgent as _SdkFoundryAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential


class MissionAgentResult(dict[str, Any]):
    """Dictionary result that also exposes Agent Framework-style attributes."""

    def __init__(self, *, raw_text: str, value: Any) -> None:
        mapping = value if isinstance(value, dict) else {"output_text": raw_text}
        super().__init__(mapping)
        self.text = raw_text
        self.value = value


class MissionFoundryAgent:
    """Runs one existing mission agent using verified keyword-only SDK calls."""

    def __init__(
        self,
        agent_name: str | None = None,
        *,
        agent_version: str | None = None,
        **_: Any,
    ) -> None:
        if not agent_name:
            raise ValueError("A non-empty mission Foundry agent name is required.")
        self._agent_name = agent_name
        self._agent_version = agent_version

    async def run(self, messages: Any, **kwargs: Any) -> MissionAgentResult:
        if kwargs.get("stream"):
            raise ValueError("MissionFoundryAgent streaming is not supported inside delegation tools.")

        input_text = messages if isinstance(messages, str) else json.dumps(messages)
        endpoint = os.environ["FOUNDRY_ENDPOINT"]
        async with DefaultAzureCredential() as credential:
            async with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
                agent_version = self._agent_version
                if not agent_version:
                    details = await project_client.agents.get(self._agent_name)
                    agent_version = details.versions.latest.version
                agent = _SdkFoundryAgent(
                    project_client=project_client,
                    agent_name=self._agent_name,
                    agent_version=agent_version,
                )
                response = await agent.run(input_text, tools=kwargs.get("tools"))

        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text:
            raise RuntimeError(
                f"Mission Foundry agent '{self._agent_name}' returned no output text."
            )
        try:
            value: Any = json.loads(raw_text)
        except json.JSONDecodeError:
            value = {"output_text": raw_text}
        return MissionAgentResult(raw_text=raw_text, value=value)
'''

_REQUIREMENTS_TXT = """fastapi>=0.115,<1.0
uvicorn>=0.32,<1.0
azure-ai-projects>=2.3,<3.0
azure-identity>=1.19,<2.0
agent-framework>=1.0
"""

_DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def generate_agent_config_module(agent_foundry_names: dict[str, str]) -> str:
    """Returns the real, deterministic ``agent_config.py`` module content mapping
    every one of this mission's own logical agent names (specialists and the
    orchestrator itself) to their real, already-provisioned Foundry agent
    names - never LLM-authored, so the generated ``orchestrator.py`` always has
    a real, non-hardcoded source of truth for which Foundry agent to call."""

    return _AGENT_CONFIG_PY_TEMPLATE.format(agent_foundry_names=agent_foundry_names)


def generate_backend_service_scaffold(
    *,
    mission_title: str,
    orchestrator_agent_name: str,
    agent_foundry_names: dict[str, str],
) -> dict[str, str]:
    """Returns the real, deterministic mission backend service scaffold.

    This includes ``mission_foundry_runtime.py``, the stable boundary between
    LLM-generated orchestration logic and version-sensitive SDK behavior. The
    scaffold is
    never LLM-authored, so every deployed mission's backend proxy is
    consistent and auditable."""

    return {
        "main.py": _MAIN_PY_TEMPLATE.format(
            mission_title=f"{mission_title} Backend",
            orchestrator_agent_name=orchestrator_agent_name,
            authentication_middleware="",
        ),
        "requirements.txt": _REQUIREMENTS_TXT,
        "Dockerfile": _DOCKERFILE,
        "agent_config.py": generate_agent_config_module(agent_foundry_names),
        "mission_foundry_runtime.py": _MISSION_FOUNDRY_RUNTIME_PY,
    }
