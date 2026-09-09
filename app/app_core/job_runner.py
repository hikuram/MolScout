"""Launch and track MolScout CLI jobs."""

from __future__ import annotations

import os
import json
import shlex
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .artifact_manager import scan_job_artifacts
from .config import OUTPUT_CATEGORY_DIRS, WORKFLOW_LABELS
from .paths import APP_DIR, CORE_DIR, PROJECT_ROOT
from .session_manager import get_job, save_job
from .utils import now_iso, parse_iso, read_json

FAILURE_MARKERS = (
    "Traceback (most recent call last):",
    "abort:",
    "[Fail",
    "Canceled:",
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


CANCEL_GRACE_SECONDS = max(1.0, _env_float("MOLSCOUT_CANCEL_GRACE_SECONDS", 15.0))
FORCE_KILL_SETTLE_SECONDS = 5.0


def process_group_id(job: dict) -> int | None:
    value = job.get("process_group_id") or job.get("pid")
    try:
        pgid = int(value)
    except (TypeError, ValueError):
        return None
    return pgid if pgid > 0 else None


def _proc_stat_group(stat_text: str) -> tuple[str, int] | None:
    right_paren = stat_text.rfind(")")
    if right_paren < 0:
        return None
    fields = stat_text[right_paren + 1 :].strip().split()
    if len(fields) < 3:
        return None
    try:
        return fields[0], int(fields[2])
    except ValueError:
        return None


def process_group_is_running(pgid: int | None) -> bool:
    if not pgid or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

    proc_root = Path("/proc")
    if not proc_root.exists():
        return True

    target_seen = False
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            parsed = _proc_stat_group((entry / "stat").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if parsed is None:
            continue
        state, process_pgid = parsed
        if process_pgid != pgid:
            continue
        target_seen = True
        if state != "Z":
            return True
    # killpg(0) can still succeed for a group containing zombies only. If the
    # target group could not be inspected, keep treating it as live (fail safe).
    return False if target_seen else True


def seconds_since(value: str | None) -> float | None:
    stamp = parse_iso(value)
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())


def runtime_status_path_for(stdout_log: Path) -> Path:
    return stdout_log.with_suffix(".runtime.json")


def read_runtime_status(path: Path | None) -> dict:
    if not path:
        return {}
    return read_json(path, {})


def csv_has_payload(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if not lines:
        return False
    return len(lines) >= 2 or any("," in line for line in lines)


def find_result_file(output_dir: Path, result_name: str) -> Path:
    candidates = [output_dir / result_name, output_dir / "Tables" / result_name]
    for path in candidates:
        if path.exists():
            return path
    matches = sorted(output_dir.rglob(result_name)) if output_dir.exists() else []
    return matches[0] if matches else candidates[0]


def molscout_log_candidates(output_dir: Path) -> list[Path]:
    candidates = [output_dir / "molscout.log", output_dir / "Logs" / "molscout.log"]
    if output_dir.exists():
        candidates.extend(sorted(output_dir.rglob("molscout.log")))
    unique: dict[Path, Path] = {}
    for candidate in candidates:
        unique[candidate.resolve() if candidate.exists() else candidate] = candidate
    return list(unique.values())


def unique_output_path(target: Path) -> Path:
    if not target.exists():
        return target
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def organize_run_output_files(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0

    category_roots = set(OUTPUT_CATEGORY_DIRS.values())
    moved = 0
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(output_dir)
        if relative == Path("resolved_config.json"):
            continue
        if relative.parts and relative.parts[0] in category_roots:
            continue
        category = OUTPUT_CATEGORY_DIRS.get(path.suffix.lower())
        if not category:
            continue
        target = output_dir / category / relative
        target = unique_output_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        moved += 1
    return moved


def find_failure_marker(job: dict) -> str:
    output_dir = Path(job["output_dir"])
    candidates = [Path(job["stdout_log"]), *molscout_log_candidates(output_dir)]
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in FAILURE_MARKERS:
            if marker in text:
                return f"Detected {marker} in {path.name}"
    return ""


def validate_job_outputs(job: dict) -> tuple[bool, str]:
    output_dir = Path(job["output_dir"])
    if not output_dir.exists():
        return False, "output directory was not created"

    result_path = find_result_file(output_dir, job.get("result_name", "result.csv"))
    workflow = job.get("workflow", "")
    if workflow == "Figure refresh only":
        has_figure = any(path.is_file() for pattern in ("*.png", "*.jpg", "*.jpeg") for path in output_dir.rglob(pattern))
        if has_figure or csv_has_payload(result_path):
            return True, ""
        return False, "expected refreshed figure or result CSV was not generated"

    if not csv_has_payload(result_path):
        return False, f"expected result file does not exist or is empty: {result_path.name}"

    return True, ""


def finalize_job(job: dict, runtime: dict, exit_code: int | None, *, process_group_running: bool) -> dict:
    requested_cancel = bool(job.get("cancel_requested_at"))
    runtime_phase = runtime.get("phase", "")
    failure_marker = find_failure_marker(job)

    if requested_cancel:
        if job.get("force_kill_sent_at"):
            job["status"] = "cancelled"
            job["completion_reason"] = "force_killed_after_cancel_timeout"
            job["status_message"] = "Cancellation required SIGKILL after the graceful-stop timeout."
            job["exit_code"] = 137
        elif runtime_phase == "cancelled" or exit_code in {130, 143} or (not process_group_running and exit_code is None):
            job["status"] = "cancelled"
            job["completion_reason"] = "cancelled_by_user"
            job["status_message"] = "stopped by user cancellation."
            if exit_code is None:
                job["exit_code"] = 143
        elif exit_code == 0:
            ok, reason = validate_job_outputs(job)
            if ok and not failure_marker:
                job["status"] = "completed"
                job["completion_reason"] = "finished_before_cancel_took_effect"
                job["status_message"] = "cancellation request arrived after calculation completed."
            else:
                job["status"] = "cancelled"
                job["completion_reason"] = "cancelled_by_user"
                job["status_message"] = "cancellation was requested and the run did not complete successfully."
        else:
            job["status"] = "cancelled"
            job["completion_reason"] = "cancelled_by_user"
            job["status_message"] = "stopped by user cancellation."
    elif exit_code == 0:
        ok, reason = validate_job_outputs(job)
        if failure_marker:
            job["status"] = "failed"
            job["completion_reason"] = "failure_marker_detected"
            job["status_message"] = failure_marker
        elif ok:
            job["status"] = "completed"
            job["completion_reason"] = "outputs_verified"
            job["status_message"] = "process exited successfully and expected outputs were confirmed."
        else:
            job["status"] = "failed"
            job["completion_reason"] = "missing_expected_outputs"
            job["status_message"] = reason
    elif exit_code is not None:
        job["status"] = "failed"
        job["completion_reason"] = "nonzero_exit"
        job["status_message"] = f"process exited with code {exit_code}."
    elif runtime_phase == "failed":
        job["status"] = "failed"
        job["completion_reason"] = "runtime_reported_failure"
        job["status_message"] = runtime.get("signal", "") or "runtime wrapper reported failure."
    else:
        job["status"] = "failed"
        job["completion_reason"] = "process_disappeared_without_runtime_status"
        job["status_message"] = "process exited, but final runtime metadata was not recorded."

    try:
        moved_count = organize_run_output_files(Path(job["output_dir"]))
    except OSError as exc:
        job["output_organize_error"] = str(exc)
    else:
        job["output_organized_at"] = now_iso()
        job["output_organized_files"] = moved_count
        job["output_organize_error"] = ""

    job["finished_at"] = runtime.get("finished_at") or now_iso()
    job["queue_position"] = None

    try:
        catalog_result = scan_job_artifacts(
            str(job["session_id"]),
            job,
            source="job_finalize",
        )
    except Exception as exc:
        # Artifact indexing must never change a completed calculation into a failed job.
        job["artifact_catalog_error"] = f"{type(exc).__name__}: {exc}"
    else:
        job["artifact_cataloged_at"] = now_iso()
        job["artifact_catalog_count"] = catalog_result["artifact_count"]
        job["artifact_catalog_marked_missing"] = catalog_result["marked_missing"]
        job["artifact_catalog_error"] = ""
    return job


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    paths = [str(APP_DIR), str(CORE_DIR), str(PROJECT_ROOT)]
    current = env.get("PYTHONPATH")
    if current:
        paths.append(current)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def build_command(
    *,
    workflow: str,
    script_name: str,
    output_dir: Path,
    charge: int,
    method: str,
    result_name: str,
    reactant_path: Path | None,
    product_path: Path | None,
    input_path: Path | None,
    cat_paths: list[Path] | None = None,
    workflow_steps: dict[str, bool] | None = None,
    temperature: float | None = None,
    tblite_method: str | None = None,
    config_overrides: dict | None = None,
    config_path: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "app_core.workflow_runner",
        "--workflow",
        workflow,
        "--directory",
        str(output_dir),
        "--charge",
        str(charge),
        "--method",
        method,
        "--result",
        result_name,
    ]
    step_flags = workflow_steps or {}
    if step_flags.get("initial_path"):
        command.append("--initial-path")
    if step_flags.get("ts_opt"):
        command.append("--ts-opt")
    if step_flags.get("irc"):
        command.append("--irc")
    if step_flags.get("vib"):
        command.append("--vib")
    if step_flags.get("refine"):
        command.append("--refine")
    if temperature is not None:
        command.extend(["--temperature", str(temperature)])
    if tblite_method:
        command.extend(["--tblite-method", tblite_method])
    if config_overrides and config_path is not None:
        raise ValueError("Use either config_overrides or config_path, not both.")
    if config_overrides:
        generated_config_path = output_dir.parent / "submitted_config.json"
        generated_config_path.write_text(
            json.dumps(config_overrides, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        command.extend(["--config", str(generated_config_path)])
    elif config_path is not None:
        command.extend(["--config", str(config_path)])
    if cat_paths:
        command.extend(["--catfiles", *[str(path) for path in cat_paths]])
    elif input_path is not None:
        command.extend(["--input", str(input_path)])
    else:
        if reactant_path is not None:
            command.extend(["--reactant", str(reactant_path)])
        if product_path is not None:
            command.extend(["--product", str(product_path)])
    return command


def workflow_script_name(workflow: str) -> str:
    return WORKFLOW_LABELS[workflow]["script"]


def start_job_process(job: dict) -> dict:
    stdout_log = Path(job["stdout_log"])
    exit_code_file = stdout_log.with_suffix(".exit")
    runtime_status_file = runtime_status_path_for(stdout_log)
    if exit_code_file.exists():
        exit_code_file.unlink()
    if runtime_status_file.exists():
        runtime_status_file.unlink()

    command_str = shlex.join(job["command"])
    runtime_file_q = shlex.quote(str(runtime_status_file))
    exit_file_q = shlex.quote(str(exit_code_file))
    wrapper = f"""
set +e
RUNTIME_FILE={runtime_file_q}
EXIT_FILE={exit_file_q}
write_runtime() {{
  python3 - "$RUNTIME_FILE" "$1" "$2" "$3" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, phase, exit_code, signal_name = sys.argv[1:5]
payload = {{
    "phase": phase,
    "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "exit_code": None if exit_code == "" else int(exit_code),
    "signal": signal_name or "",
}}
tmp_path = f"{{path}}.tmp.{{os.getpid()}}"
with open(tmp_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2)
os.replace(tmp_path, path)
PY
}}
trap 'printf "%s" "143" > "$EXIT_FILE"; write_runtime cancelled 143 SIGTERM; exit 143' TERM
trap 'printf "%s" "130" > "$EXIT_FILE"; write_runtime cancelled 130 SIGINT; exit 130' INT
{command_str}
code=$?
printf '%s' "$code" > "$EXIT_FILE"
if [ "$code" -eq 0 ]; then
  write_runtime finished "$code" ""
else
  write_runtime failed "$code" ""
fi
exit "$code"
""".strip()

    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("w", encoding="utf-8") as handle:
        handle.write("Command:\n")
        handle.write(command_str + "\n\n")
        handle.flush()
        proc = subprocess.Popen(
            ["bash", "-lc", wrapper],
            cwd=str(APP_DIR),
            env=build_env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    job["pid"] = proc.pid
    job["process_group_id"] = proc.pid
    job["status"] = "running"
    job["started_at"] = now_iso()
    job["updated_at"] = now_iso()
    job["exit_code"] = None
    job["exit_code_file"] = str(exit_code_file)
    job["runtime_status_file"] = str(runtime_status_file)
    job["completion_reason"] = ""
    job["status_message"] = ""
    job["cancel_requested_at"] = None
    job["force_kill_sent_at"] = None
    save_job(job)
    return job


def sync_job_status(job: dict) -> dict:
    exit_code = None
    exit_code_file = job.get("exit_code_file")
    if exit_code_file and Path(exit_code_file).exists():
        try:
            exit_code = int(Path(exit_code_file).read_text(encoding="utf-8").strip())
        except ValueError:
            exit_code = None

    runtime = read_runtime_status(Path(job["runtime_status_file"])) if job.get("runtime_status_file") else {}
    if runtime.get("exit_code") is not None:
        exit_code = runtime["exit_code"]

    if job.get("status") not in {"running", "cancel_requested"}:
        return job

    pgid = process_group_id(job)
    group_running = process_group_is_running(pgid)

    if job.get("status") == "cancel_requested" and group_running:
        force_kill_sent_at = job.get("force_kill_sent_at")
        if not force_kill_sent_at:
            elapsed = seconds_since(job.get("cancel_requested_at"))
            if elapsed is None:
                job["cancel_requested_at"] = now_iso()
                job["status_message"] = (
                    f"Cancellation is pending. Waiting up to {CANCEL_GRACE_SECONDS:.0f} seconds "
                    "before forced termination."
                )
                save_job(job)
                return job
            if elapsed >= CANCEL_GRACE_SECONDS:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    group_running = False
                except PermissionError:
                    job["status_message"] = (
                        "SIGKILL escalation failed with permission denied. "
                        "The queue remains held until the process group stops."
                    )
                    save_job(job)
                    return job
                else:
                    job["force_kill_sent_at"] = now_iso()
                    job["status_message"] = (
                        "Graceful cancellation timed out; SIGKILL was sent to the process group. "
                        "Waiting for stop confirmation before advancing the queue."
                    )
                    save_job(job)
                    return job
        else:
            force_elapsed = seconds_since(force_kill_sent_at)
            if force_elapsed is not None and force_elapsed >= FORCE_KILL_SETTLE_SECONDS:
                message = (
                    "SIGKILL was sent, but the process group is still active. "
                    "The queue is held for safety; container or host intervention may be required."
                )
                if job.get("status_message") != message:
                    job["status_message"] = message
                    save_job(job)
            return job

    if group_running:
        if exit_code is not None:
            message = (
                "The wrapper process exited, but processes remain in the job process group. "
                "The queue is held until the group stops."
            )
            if job.get("status_message") != message:
                job["status_message"] = message
                save_job(job)
        return job

    if job.get("force_kill_sent_at"):
        exit_code = 137
    if exit_code is not None:
        job["exit_code"] = exit_code
        finalize_job(job, runtime, exit_code, process_group_running=False)
    else:
        finalize_job(job, runtime, None, process_group_running=False)
    save_job(job)
    return job


def stop_job(job: dict) -> str:
    pgid = process_group_id(job)
    if not process_group_is_running(pgid):
        return "not_running"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "not_running"
    except PermissionError:
        job["status_message"] = "Cancellation failed: permission denied while signaling the process group."
        save_job(job)
        return "error"
    job["status"] = "cancel_requested"
    job["cancel_requested_at"] = now_iso()
    job["force_kill_sent_at"] = None
    job["completion_reason"] = ""
    job["status_message"] = (
        f"SIGTERM sent to the process group. Waiting up to {CANCEL_GRACE_SECONDS:.0f} seconds "
        "before forced termination."
    )
    save_job(job)
    return "signaled"


def reload_job(session_id: str, job_id: str) -> dict | None:
    job = get_job(session_id, job_id)
    if not job:
        return None
    return sync_job_status(job)
