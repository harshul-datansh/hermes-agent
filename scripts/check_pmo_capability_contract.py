"""Verify that Datansh PM-OS remains an additive Hermes extension.

This complements ``check_pmo_kanban_sync.py``.  The sync checker proves the
copied Kanban tree's provenance and intentional divergences; this checker
proves that the PM profile and PMO-only Python additions do not grow a second
agent runtime or replace Hermes capability configuration.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import subprocess
from pathlib import Path
from typing import TypedDict


CORE_PATHS = (
    "agent",
    "run_agent.py",
    "toolsets.py",
    "model_tools.py",
    "gateway",
    "hermes_cli/kanban_db.py",
    "hermes_state.py",
    "plugins/kanban",
    "tools",
)
# PM-OS may use a very small, reviewable Hermes seam when a plugin-only change
# cannot safely express a task-local runtime property. These files still count
# toward the 10% customization budget; every other core/upstream path remains
# byte-for-byte protected by validate_core_boundary().
APPROVED_CORE_DIVERGENCES = {
    "gateway/run.py",
    "gateway/session.py",
    "hermes_cli/kanban_db.py",
    "tools/environments/docker.py",
    "tools/file_tools.py",
    "tools/terminal_tool.py",
}
DIST_REQUIRED_FILES = {
    "README.md",
    "SOUL.md",
    "distribution.yaml",
    "skills/datansh-pm-os/SKILL.md",
}
DIST_OWNED_PAYLOAD = ("SOUL.md", "skills/datansh-pm-os")
FORBIDDEN_MODULE_PARTS = {"pmo_db", "pmo_authz", "pmo_tools"}
FORBIDDEN_TOOL_MODULES = {"model_tools", "toolsets", "tools.registry"}
DIRECT_DB_EXCEPTION_MODULES = {"report.py", "sandbox.py"}
DIRECT_SQL_EXCEPTION_MODULES = {"sandbox.py"}
PARALLEL_RUNTIME_NAME = re.compile(
    r"(?:^|_)(?:agent_loop|agent_runtime|message_runtime|message_router|"
    r"message_store|memory_runtime|memory_store|session_runtime|session_store|"
    r"dispatcher|tool_registry)(?:_|$)",
    re.IGNORECASE,
)
PARALLEL_RUNTIME_CLASS = re.compile(
    r"(?:Agent|Message|Memory|Session|Dispatcher|Router|ToolRegistry).*"
    r"(?:Runtime|Store|Manager|Engine|Loop|Dispatcher|Router)$",
    re.IGNORECASE,
)
CREATE_TABLE = re.compile(r"\bcreate\s+table\b", re.IGNORECASE)
MAX_CUSTOMIZATION_RATIO = 0.10
NON_PRODUCTION_PREFIXES = (
    ".github/",
    "design/",
    "docs/",
    "examples/",
    "plans/",
    "tests/",
    "tests-js/",
    "website/",
)
NON_PRODUCTION_SUFFIXES = {".md", ".rst", ".snap", ".txt"}


class CustomizationBudget(TypedDict):
    numerator: int
    denominator: int
    ratio: float
    paths: tuple[str, ...]


def _load_sync_checker(repo_root: Path):
    path = repo_root / "scripts" / "check_pmo_kanban_sync.py"
    spec = importlib.util.spec_from_file_location("check_pmo_kanban_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_owned_payload(manifest_text: str) -> tuple[str, ...]:
    """Read the narrow distribution_owned list without a YAML dependency."""

    owned: list[str] = []
    in_owned = False
    for line in manifest_text.splitlines():
        if line.strip() == "distribution_owned:":
            in_owned = True
            continue
        if in_owned and re.match(r"^\s+-\s+", line):
            owned.append(re.sub(r"^\s+-\s+", "", line).strip(" '\""))
            continue
        if in_owned and line.strip() and not line.startswith((" ", "\t")):
            break
    return tuple(owned)


def validate_distribution(repo_root: Path) -> list[str]:
    dist = repo_root / "distributions" / "datansh-pm-os"
    errors: list[str] = []
    actual = {
        path.relative_to(dist).as_posix()
        for path in dist.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    missing = DIST_REQUIRED_FILES - actual
    if missing:
        errors.append(f"distribution missing required files: {sorted(missing)}")

    allowed = {
        path
        for path in actual
        if path in {"README.md", "SOUL.md", "distribution.yaml"}
        or path.startswith("skills/datansh-pm-os/")
    }
    unexpected = actual - allowed
    if unexpected:
        errors.append(f"distribution contains replacement payload: {sorted(unexpected)}")

    manifest = dist / "distribution.yaml"
    if manifest.is_file():
        owned = _manifest_owned_payload(manifest.read_text(encoding="utf-8"))
        if owned != DIST_OWNED_PAYLOAD:
            errors.append(
                "distribution_owned must contain only SOUL.md and the PM skill; "
                f"found {owned!r}"
            )
    return errors


def _import_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def validate_pmo_source(path: Path) -> list[str]:
    """Return contract violations in one PMO-only production module."""

    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: cannot parse: {exc}"]

    imports = _import_names(tree)
    for module in sorted(imports):
        parts = set(module.split("."))
        if parts & FORBIDDEN_MODULE_PARTS:
            errors.append(f"{path}: imports forbidden replacement module {module}")
        if module in FORBIDDEN_TOOL_MODULES or module.startswith("tools.registry"):
            errors.append(f"{path}: imports tool-registration surface {module}")
        if module == "mcp" or module.startswith("mcp."):
            errors.append(f"{path}: imports an MCP runtime instead of using Hermes MCP")
        if module == "sqlite3" and path.name not in DIRECT_DB_EXCEPTION_MODULES:
            errors.append(
                f"{path}: imports sqlite3 instead of using an existing Hermes state API"
            )

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if PARALLEL_RUNTIME_NAME.search(node.name) or (
                isinstance(node, ast.ClassDef) and PARALLEL_RUNTIME_CLASS.search(node.name)
            ):
                errors.append(f"{path}: defines parallel runtime surface {node.name}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_MODULE_PARTS:
            errors.append(f"{path}: uses forbidden replacement symbol {node.id}")
        if isinstance(node, ast.Call):
            func = node.func
            call_name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            if call_name in {"register", "register_tool", "register_tools"}:
                errors.append(f"{path}: registers a model tool ({call_name})")
            if (
                call_name in {"execute", "executemany", "executescript"}
                and path.name not in DIRECT_SQL_EXCEPTION_MODULES
            ):
                errors.append(
                    f"{path}: embeds SQL outside a Hermes store module ({call_name})"
                )

    if CREATE_TABLE.search(text):
        errors.append(f"{path}: defines a database schema (CREATE TABLE)")
    return sorted(set(errors))


def pmo_addition_paths(repo_root: Path) -> list[Path]:
    """Discover Python files added beyond the recorded Kanban baseline."""

    sync = _load_sync_checker(repo_root)
    source_commit = sync.read_source_commit(repo_root)
    baseline = set(sync.source_files(repo_root, source_commit))
    pmo_root = repo_root / "plugins" / "pmo"
    return sorted(
        path
        for path in pmo_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and path.relative_to(pmo_root).as_posix() not in baseline
    )


def _is_baseline_production_path(relative: str) -> bool:
    """Classify the recorded Hermes tree for the file-count denominator."""

    normalized = relative.replace("\\", "/").lstrip("./")
    if not normalized or normalized.startswith(NON_PRODUCTION_PREFIXES):
        return False
    path = Path(normalized)
    if path.suffix.casefold() in NON_PRODUCTION_SUFFIXES:
        return False
    if path.name.casefold() in {"license", "license.txt", "readme", "readme.md"}:
        return False
    if path.name.startswith("test_") or path.name.endswith("_test.py"):
        return False
    return "__pycache__" not in path.parts


def baseline_production_paths(repo_root: Path, source_commit: str) -> tuple[str, ...]:
    """Return unique non-documentation production files at the fork baseline."""

    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", source_commit],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return tuple(
        sorted(
            path
            for path in result.stdout.splitlines()
            if _is_baseline_production_path(path)
        )
    )


def pmo_customization_paths(repo_root: Path) -> tuple[str, ...]:
    """Return PM-OS-owned production files, excluding the byte-identical copy.

    The copied Kanban baseline contributes zero to the numerator. Only changed
    or added files in that copy plus shipped platform/profile/skill and operator
    script surfaces count. Tests, docs, plans, CI definitions, and READMEs do not.
    """

    sync = _load_sync_checker(repo_root)
    source_commit = sync.read_source_commit(repo_root)
    paths: set[str] = set()
    differences = sync.compare_tree(
        repo_root,
        source_commit,
        "plugins/pmo",
        ignored_files=sync.PMO_METADATA_FILES,
    )
    for difference in differences:
        kind, separator, relative = difference.partition(": ")
        if separator and kind in {"changed", "extra"}:
            paths.add(f"plugins/pmo/{relative}")

    core_result = subprocess.run(
        ["git", "diff", "--name-only", source_commit, "--", *CORE_PATHS],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    paths.update(
        path
        for path in core_result.stdout.splitlines()
        if path in APPROVED_CORE_DIVERGENCES
    )

    shipped_roots = (
        "plugins/platforms/pmo",
        "distributions/datansh-pm-os",
        "skills/productivity/datansh-pm-os",
    )
    for root_name in shipped_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name.casefold() == "readme.md":
                continue
            paths.add(path.relative_to(repo_root).as_posix())

    scripts = repo_root / "scripts"
    for pattern in ("pmo_*.py", "check_pmo_*.py"):
        for path in scripts.glob(pattern):
            if path.is_file():
                paths.add(path.relative_to(repo_root).as_posix())
    return tuple(sorted(paths))


def customization_budget(repo_root: Path) -> CustomizationBudget:
    """Measure PM-OS unique production-file customization against Hermes."""

    sync = _load_sync_checker(repo_root)
    source_commit = sync.read_source_commit(repo_root)
    denominator_paths = baseline_production_paths(repo_root, source_commit)
    numerator_paths = pmo_customization_paths(repo_root)
    denominator = len(denominator_paths)
    numerator = len(numerator_paths)
    ratio = numerator / denominator if denominator else 1.0
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": ratio,
        "paths": numerator_paths,
    }


def validate_customization_budget(
    *, numerator: int, denominator: int, maximum: float = MAX_CUSTOMIZATION_RATIO
) -> list[str]:
    if denominator <= 0:
        return ["customization budget denominator must be positive"]
    if numerator < 0:
        return ["customization budget numerator must be non-negative"]
    ratio = numerator / denominator
    if ratio > maximum:
        return [
            "PM-OS customization exceeds the unique production-file budget: "
            f"{numerator}/{denominator} ({ratio:.2%}) > {maximum:.0%}"
        ]
    return []


def validate_core_boundary(repo_root: Path) -> list[str]:
    sync = _load_sync_checker(repo_root)
    source_commit = sync.read_source_commit(repo_root)
    result = subprocess.run(
        ["git", "diff", "--name-only", source_commit, "--", *CORE_PATHS],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    changed = {line for line in result.stdout.splitlines() if line}
    unexpected = sorted(changed - APPROVED_CORE_DIVERGENCES)
    stale_approvals = sorted(APPROVED_CORE_DIVERGENCES - changed)
    errors = [
        f"unapproved core/upstream path changed from PM baseline: {path}"
        for path in unexpected
    ]
    errors.extend(
        f"approved core divergence is no longer present; remove or review it: {path}"
        for path in stale_approvals
    )
    return errors


def validate(repo_root: Path) -> list[str]:
    sync = _load_sync_checker(repo_root)
    source_commit = sync.read_source_commit(repo_root)
    errors = validate_distribution(repo_root)
    errors.extend(validate_core_boundary(repo_root))
    errors.extend(
        f"upstream Kanban divergence: {item}"
        for item in sync.compare_tree(repo_root, source_commit, "plugins/kanban")
    )
    for path in pmo_addition_paths(repo_root):
        errors.extend(validate_pmo_source(path))
    budget = customization_budget(repo_root)
    errors.extend(
        validate_customization_budget(
            numerator=budget["numerator"],
            denominator=budget["denominator"],
        )
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    errors = validate(repo_root)
    if errors:
        print("PM-OS capability contract: FAILED")
        for error in errors:
            print(f"  - {error}")
        return 1
    additions = pmo_addition_paths(repo_root)
    budget = customization_budget(repo_root)
    print("PM-OS capability contract: clean")
    print("  distribution: additive SOUL + skill payload only")
    print(f"  PMO additions checked: {len(additions)}")
    print(
        "  customization budget: "
        f"{budget['numerator']}/{budget['denominator']} production files "
        f"({budget['ratio']:.2%}, maximum {MAX_CUSTOMIZATION_RATIO:.0%})"
    )
    print(
        "  Hermes core: only approved narrow divergences; upstream Kanban unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
