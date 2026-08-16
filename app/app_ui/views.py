"""Shared Streamlit GUI for MolScout remote usage."""

from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app_core.archive_manager import (
    build_job_archive,
    build_merged_csv_archive,
    build_selected_jobs_archive,
    build_session_archive,
)
from app_core.config import (
    DEFAULT_ALPB_SOLVENT,
    DEFAULT_ORBMOL_VERSION,
    DEFAULT_TBLITE_ACCURACY,
    IMAGE_EXTENSIONS,
    INPUT_EXTENSIONS,
    JSON_EXTENSIONS,
    LOG_EXTENSIONS,
    METHOD_OPTIONS,
    ORBMOL_VERSION_OPTIONS,
    PACKAGE_CHECKS,
    RESULT_CANDIDATES,
    SAMPLE_INPUT_ROOT,
    TABLE_EXTENSIONS,
    WORKFLOW_LABELS,
    XYZ_EXTENSIONS,
)
from app_core.job_runner import build_command, reload_job, stop_job, workflow_script_name
from app_core.job_manifest import manifest_input, write_job_manifest
from app_core.paths import APP_DIR, AUTO_REFRESH_SECONDS, SESSION_RETENTION_DAYS, WORKER_LOG_FILE, ensure_app_dirs
from app_core.queue_manager import (
    delete_job_from_queue,
    enqueue_job,
    queue_snapshot,
    remove_from_queue,
    reorder_queue_for_session,
    sync_queue_state,
    worker_is_running,
)
from app_core.session_manager import (
    create_job,
    create_session,
    default_pyscf_config,
    get_job,
    get_session,
    job_dir,
    list_existing_inputs,
    list_jobs,
    list_sessions,
    save_job,
    save_session,
    session_dir,
    touch_session,
)
from app_core.system_monitor import system_snapshot
from app_core.utils import file_size_label, now_iso, safe_name, tail_text

REFRESHABLE_JOB_STATUSES = {"running", "cancel_requested", "queued"}
APP_TZ = ZoneInfo("Asia/Tokyo")
APP_TIME_LABEL = "JST"
ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
)


def parse_app_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value or "").strip()
        if not text or text == "-":
            return None
        match = ISO_TIMESTAMP_RE.search(text)
        if match:
            text = match.group(0)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(APP_TZ)


def format_app_time(value: object, fallback: str = "-") -> str:
    stamp = parse_app_time(value)
    if stamp is None:
        return fallback
    return stamp.strftime(f"%Y-%m-%d %H:%M {APP_TIME_LABEL}")


def format_worker_log_time(text: str) -> str:
    def replace_match(match: re.Match[str]) -> str:
        following = text[match.end():match.end() + 8]
        if following.lstrip().startswith(APP_TIME_LABEL):
            return match.group(0)
        converted = format_app_time(match.group(0), fallback=match.group(0))
        return converted

    return ISO_TIMESTAMP_RE.sub(replace_match, text)

WORKFLOW_DISPLAY_LABELS = {
    "Lite workflow": "Lite workflow（Refine省略）",
    "Full workflow": "Full workflow（全ステージ）",
    "IRC workflow only": "IRC workflow only",
    "VIB workflow only": "VIB workflow only",
    "Figure refresh only": "Figure refresh only",
}

WORKFLOW_STEP_FIELDS = {
    "initial_path": "Initial Path",
    "ts_opt": "TS Opt",
    "irc": "IRC",
    "vib": "Vib & Thermo",
    "refine": "Energy Refine",
}

WORKFLOW_PRESET_STEPS = {
    "Lite workflow": {
        "initial_path": True,
        "ts_opt": True,
        "irc": True,
        "vib": True,
        "refine": False,
    },
    "Full workflow": {
        "initial_path": True,
        "ts_opt": True,
        "irc": True,
        "vib": True,
        "refine": True,
    },
    "IRC workflow only": {
        "initial_path": False,
        "ts_opt": True,
        "irc": True,
        "vib": False,
        "refine": False,
    },
    "VIB workflow only": {
        "initial_path": False,
        "ts_opt": False,
        "irc": False,
        "vib": True,
        "refine": False,
    },
    "Figure refresh only": {
        "initial_path": False,
        "ts_opt": False,
        "irc": False,
        "vib": False,
        "refine": False,
    },
}

SUBMIT_DEFAULTS_KEY = "submit_defaults"
PATH_SUBMIT_SECTION = "path_search"
CONCAT_SUBMIT_SECTION = "concat"

INITIAL_PATH_DEFAULTS = {
    "init_path_method": "DMF",
    "nmove": 40,
    "dmf_convergence": "tight",
    "update_teval": False,
    "neb_images": 10,
    "neb_spring_constant": 0.1,
    "neb_climb": True,
    "scan_preset": "custom",
    "scan_type": "bond",
    "scan_type_previous": "bond",
    "scan_indices_text": "0,1",
    "scan_steps": 10,
    "scan_step_mode": "steps",
    "scan_step_size": 0.05,
    "scan_range_mode": "auto_to_absolute",
    "scan_start_auto": True,
    "scan_start_val": 0.0,
    "scan_end_val": 2.0,
    "scan_start_delta": 0.0,
    "scan_end_delta": 1.0,
}

MODULE_DEFAULTS = {
    "use_sella_in_opt": False,
    "opt_fmax": 0.01,
    "tsopt_fmax": 0.0004,
    "refine_calc_type": "pyscf_high",
    "sella_internal_auto": True,
    "sella_internal": True,
    "irc_dx_init": 0.06,
    "irc_dx_max": 0.12,
    "irc_dx_min": 0.02,
    "opt_optpoints_again_on": False,
    "fixed_atoms_text": "",
}

METHOD_DEFAULTS = {
    "method": "orbmol",
    "custom": "orbmol",
    "orbmol_version": DEFAULT_ORBMOL_VERSION,
    "alpb_solvent": "None",
    "tblite": "hybrid",
    "tblite_accuracy": DEFAULT_TBLITE_ACCURACY,
}

PATH_SUBMIT_DEFAULTS = {
    "preset": next(iter(WORKFLOW_LABELS)),
    **INITIAL_PATH_DEFAULTS,
    **METHOD_DEFAULTS,
    **{f"workflow_step_{name}": value for name, value in WORKFLOW_PRESET_STEPS[next(iter(WORKFLOW_LABELS))].items()},
    "charge": 0,
    "mult": 1,
    "temp": 298.15,
    "result_name": "result.csv",
    "refine_input_on": True,
    "pick_optpoints_on": True,
    "save_fig_on": True,
    **MODULE_DEFAULTS,
}

CONCAT_SUBMIT_DEFAULTS = {
    **METHOD_DEFAULTS,
    "do_opt": False,
    "do_vib": False,
    "do_refine": False,
    "charge": 0,
    "mult": 1,
    "temp": 298.15,
    "result_name": "result.csv",
    "save_fig_on": True,
    "pick_optpoints_on": False,
    **MODULE_DEFAULTS,
}


def session_submit_defaults(session: dict, section: str) -> dict[str, object]:
    defaults = session.get(SUBMIT_DEFAULTS_KEY, {})
    if not isinstance(defaults, dict):
        return {}
    section_defaults = defaults.get(section, {})
    return dict(section_defaults) if isinstance(section_defaults, dict) else {}


def prefixed_state_keys(prefix: str, names: list[str]) -> dict[str, str]:
    return {name: f"{prefix}_{name}" for name in names}


def path_submit_state_keys(session_id: str) -> dict[str, str]:
    path_prefix = f"{session_id}_path"
    keys = {
        "preset": f"{session_id}_preset",
        **prefixed_state_keys(path_prefix, list(INITIAL_PATH_DEFAULTS)),
        **prefixed_state_keys(session_id, list(METHOD_DEFAULTS)),
        **{f"workflow_step_{name}": workflow_step_key(session_id, name) for name in WORKFLOW_STEP_FIELDS},
        "charge": f"{session_id}_charge",
        "mult": f"{session_id}_mult",
        "temp": f"{session_id}_temp",
        "result_name": f"{session_id}_res",
        "refine_input_on": f"{session_id}_refine_input_on",
        "pick_optpoints_on": f"{session_id}_pick_optpoints",
        "save_fig_on": f"{session_id}_savefig",
    }
    keys.update(prefixed_state_keys(path_prefix, list(MODULE_DEFAULTS)))
    return keys


def concat_submit_state_keys(session_id: str) -> dict[str, str]:
    prefix = f"{session_id}_cat"
    keys = {
        **prefixed_state_keys(prefix, list(METHOD_DEFAULTS)),
        "do_opt": f"{prefix}_do_opt",
        "do_vib": f"{prefix}_do_vib",
        "do_refine": f"{prefix}_do_refine",
        "charge": f"{prefix}_charge",
        "mult": f"{prefix}_mult",
        "temp": f"{prefix}_temp",
        "result_name": f"{prefix}_res",
        "save_fig_on": f"{prefix}_savefig",
        "pick_optpoints_on": f"{prefix}_pick_optpoints",
    }
    keys.update(prefixed_state_keys(prefix, list(MODULE_DEFAULTS)))
    return keys


def apply_submit_defaults(
    session: dict,
    section: str,
    built_in_defaults: dict[str, object],
    state_keys: dict[str, str],
    *,
    force: bool = False,
) -> dict[str, object]:
    defaults = {**built_in_defaults, **session_submit_defaults(session, section)}
    for name, key in state_keys.items():
        if name in defaults and (force or key not in st.session_state):
            st.session_state[key] = defaults[name]
    return defaults


def save_submit_defaults(session: dict, section: str, values: dict[str, object]) -> None:
    updated = dict(session)
    defaults = dict(updated.get(SUBMIT_DEFAULTS_KEY, {}))
    defaults[section] = values
    updated[SUBMIT_DEFAULTS_KEY] = defaults
    save_session(updated)


def submit_reset_pending_key(session_id: str, section: str) -> str:
    return f"{session_id}_{section}_reset_pending"


def sync_submit_default_trackers(session_id: str, preset: str) -> None:
    st.session_state[f"{session_id}_workflow_dependent_preset_applied"] = preset
    st.session_state[f"{session_id}_workflow_step_preset_applied"] = preset


def workflow_step_key(session_id: str, step_name: str) -> str:
    return f"{session_id}_workflow_step_{step_name}"


def workflow_step_defaults(preset: str, mode: str) -> dict[str, bool]:
    defaults = dict(WORKFLOW_PRESET_STEPS.get(preset, WORKFLOW_PRESET_STEPS["Full workflow"]))
    if mode != "reactant_product":
        defaults["initial_path"] = False
    return defaults


def sync_workflow_step_state(session_id: str, preset: str, mode: str) -> None:
    defaults = workflow_step_defaults(preset, mode)
    tracker_key = f"{session_id}_workflow_step_preset_applied"
    preset_changed = st.session_state.get(tracker_key) != preset
    for step_name, default_value in defaults.items():
        key = workflow_step_key(session_id, step_name)
        if preset_changed or key not in st.session_state:
            st.session_state[key] = default_value
    if mode != "reactant_product":
        st.session_state[workflow_step_key(session_id, "initial_path")] = False
    st.session_state[tracker_key] = preset


def reset_scan_settings(prefix: str) -> None:
    st.session_state[f"{prefix}_scan_type"] = "bond"
    st.session_state[f"{prefix}_scan_type_previous"] = "bond"
    st.session_state[f"{prefix}_scan_indices_text"] = "0,1"
    st.session_state[f"{prefix}_scan_steps"] = 10
    st.session_state[f"{prefix}_scan_step_mode"] = "steps"
    st.session_state[f"{prefix}_scan_step_size"] = 0.05
    st.session_state[f"{prefix}_scan_range_mode"] = "auto_to_absolute"
    st.session_state[f"{prefix}_scan_start_auto"] = True
    st.session_state[f"{prefix}_scan_start_val"] = 0.0
    st.session_state[f"{prefix}_scan_end_val"] = 2.0
    st.session_state[f"{prefix}_scan_start_delta"] = 0.0
    st.session_state[f"{prefix}_scan_end_delta"] = 1.0
    st.session_state[f"{prefix}_scan_preset"] = "custom"


def reset_initial_path_detail_settings(prefix: str) -> None:
    st.session_state[f"{prefix}_nmove"] = 40
    st.session_state[f"{prefix}_dmf_convergence"] = "tight"
    st.session_state[f"{prefix}_update_teval"] = False
    st.session_state[f"{prefix}_neb_images"] = 10
    st.session_state[f"{prefix}_neb_spring_constant"] = 0.1
    st.session_state[f"{prefix}_neb_climb"] = True
    reset_scan_settings(prefix)


def sync_workflow_dependent_state(session_id: str, preset: str) -> None:
    tracker_key = f"{session_id}_workflow_dependent_preset_applied"
    if st.session_state.get(tracker_key) == preset:
        return

    path_prefix = f"{session_id}_path"
    st.session_state[f"{path_prefix}_init_path_method"] = "DMF"
    reset_initial_path_detail_settings(path_prefix)
    st.session_state[f"{session_id}_method"] = "orbmol"
    st.session_state[f"{session_id}_custom"] = "orbmol"
    st.session_state[f"{session_id}_orbmol_version"] = DEFAULT_ORBMOL_VERSION
    st.session_state[f"{session_id}_alpb_solvent"] = "None"
    st.session_state[f"{session_id}_tblite"] = "hybrid"
    st.session_state[f"{session_id}_tblite_accuracy"] = DEFAULT_TBLITE_ACCURACY
    st.session_state[f"{session_id}_source_mode"] = "新たにアップロードする"
    st.session_state[tracker_key] = preset


def theme_mode() -> str:
    base = st.get_option("theme.base")
    return base if base in {"light", "dark"} else "light"


def inject_css() -> None:
    light = theme_mode() == "light"
    if light:
        card_bg = "#ffffff"
        accent = "#0f766e"
        accent_soft = "#ecfeff"
        border = "#dbe4ea"
        heading_muted = "#475569"
        primary = "#d97706"
        primary_hover = "#b45309"
        shadow = "0 10px 30px rgba(15, 23, 42, 0.06)"
    else:
        card_bg = "#111827"
        accent = "#5eead4"
        accent_soft = "#0f2d35"
        border = "#334155"
        heading_muted = "#cbd5e1"
        primary = "#f59e0b"
        primary_hover = "#d97706"
        shadow = "0 10px 30px rgba(2, 6, 23, 0.35)"

    st.markdown(
        f"""
<style>
.block-container {{
  padding-top: 4.5rem;
}}
.block-container h2 {{
  color: {heading_muted};
}}
div[data-testid="stMetric"] {{
  background: {card_bg};
  border: 1px solid {border};
  border-radius: 14px;
  padding: 0.8rem 1rem;
  box-shadow: {shadow};
  min-height: 112px;
}}
div[data-testid="stMetricLabel"] {{
  font-size: 0.82rem;
}}
div[data-testid="stMetricValue"] {{
  font-size: clamp(1.15rem, 1rem + 0.45vw, 1.65rem);
  line-height: 1.15;
  overflow-wrap: anywhere;
}}
div[data-testid="stMetricDelta"] {{
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}}
div[data-testid="stVerticalBlock"] div[data-testid="stExpander"] {{
  border-radius: 12px;
}}
.app-badge {{
  display: inline-block;
  padding: 0.22rem 0.55rem;
  border-radius: 999px;
  background: {accent_soft};
  color: {accent};
  border: 1px solid {border};
  font-size: 0.82rem;
  margin-right: 0.4rem;
}}
.status-card {{
  border: 1px solid {border};
  border-radius: 14px;
  background: {card_bg};
  box-shadow: {shadow};
  padding: 0.8rem 0.95rem;
  margin-bottom: 0.7rem;
  min-height: 116px;
}}
.status-card-label {{
  font-size: 0.8rem;
  color: {accent};
  font-weight: 700;
  letter-spacing: 0.01em;
  text-transform: uppercase;
}}
.status-card-main {{
  margin-top: 0.2rem;
  font-size: 1.2rem;
  font-weight: 700;
  line-height: 1.15;
}}
.status-card-sub {{
  margin-top: 0.25rem;
  font-size: 0.86rem;
  opacity: 0.78;
}}
.status-card-chart {{
  margin-top: 0.5rem;
  height: 42px;
}}
.status-card-chart svg {{
  width: 100%;
  height: 42px;
  display: block;
}}
.status-card-chart path.area {{
  opacity: 0.12;
}}
.status-card-chart path.line {{
  fill: none;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}}
.job-table-header {{
  display: grid;
  grid-template-columns: 88px 1.1fr 1.2fr 0.9fr 1.1fr;
  gap: 0.65rem;
  align-items: center;
  padding: 0.6rem 0.8rem;
  border: 1px solid {border};
  border-radius: 12px;
  background: {accent_soft};
  color: {accent};
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.01em;
  margin-bottom: 0.35rem;
}}
div[role="radiogroup"] {{
  gap: 0.5rem;
}}
div[data-baseweb="segmented-control"] {{
  flex-wrap: wrap;
  gap: 0.4rem;
}}
div[data-baseweb="segmented-control"] [role="radiogroup"] {{
  background: transparent;
  box-shadow: none;
}}
div[data-baseweb="segmented-control"] label {{
  min-height: 2.4rem;
}}
div[data-baseweb="segmented-control"] label > div {{
  font-size: 0.92rem;
  font-weight: 600;
  line-height: 1.2;
  overflow-wrap: anywhere;
}}
div[data-testid="stBaseButton-primary"] > button {{
  background: {primary};
  border-color: {primary};
  color: white;
  font-weight: 600;
}}
div[data-testid="stBaseButton-primary"] > button:hover {{
  background: {primary_hover};
  border-color: {primary_hover};
}}
div[data-testid="stSelectbox"] label,
div[data-testid="stFileUploader"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stNumberInput"] label {{
  font-size: 0.92rem;
}}
.workflow-summary {{
  border: 1px solid {border};
  border-radius: 12px;
  background: {card_bg};
  padding: 0.55rem 0.7rem;
  min-height: 72px;
}}
.workflow-summary-label {{
  color: {accent};
  font-size: 0.74rem;
  font-weight: 700;
  text-transform: uppercase;
  line-height: 1.2;
}}
.workflow-summary-value {{
  margin-top: 0.18rem;
  font-size: 0.94rem;
  font-weight: 650;
  line-height: 1.25;
  overflow-wrap: anywhere;
}}
.workflow-step {{
  display: grid;
  grid-template-columns: 3.2rem 4.5rem minmax(0, 1fr);
  gap: 0.75rem;
  align-items: center;
  border: 1px solid {border};
  border-left: 4px solid {accent};
  border-radius: 12px;
  background: {card_bg};
  padding: 0.62rem 0.75rem;
  margin-bottom: 0.45rem;
}}
.workflow-step.inactive {{
  opacity: 0.46;
  border-left-color: {border};
  border-style: dashed;
}}
.workflow-step.base {{
  border-left-color: {accent};
}}
.workflow-step-number {{
  font-size: 0.9rem;
  font-weight: 750;
  opacity: 0.72;
}}
.workflow-step-badge {{
  justify-self: start;
  min-width: 3.4rem;
  text-align: center;
  border-radius: 999px;
  padding: 0.18rem 0.5rem;
  font-size: 0.74rem;
  font-weight: 750;
  border: 1px solid {border};
  background: {accent_soft};
  color: {accent};
}}
.workflow-step-badge.off {{
  background: transparent;
  color: inherit;
}}
.workflow-step-title {{
  font-size: 0.96rem;
  font-weight: 650;
  line-height: 1.25;
  overflow-wrap: anywhere;
}}
.workflow-step-detail {{
  margin-top: 0.15rem;
  font-size: 0.78rem;
  line-height: 1.25;
  opacity: 0.72;
  overflow-wrap: anywhere;
}}
.workflow-methods {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.45rem;
  margin: -0.2rem 0 0.5rem 8.45rem;
}}
.workflow-method {{
  border: 1px solid {border};
  border-radius: 999px;
  padding: 0.24rem 0.55rem;
  text-align: center;
  font-size: 0.78rem;
  font-weight: 650;
  opacity: 0.58;
}}
.workflow-method.active {{
  color: {accent};
  background: {accent_soft};
  opacity: 1;
}}
.workflow-method.disabled {{
  opacity: 0.34;
}}
</style>
""",
        unsafe_allow_html=True,
    )


def ensure_worker_running() -> None:
    if worker_is_running():
        return
    cmd = [sys.executable, "-m", "app_core.queue_worker"]
    WORKER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with WORKER_LOG_FILE.open("a", encoding="utf-8") as worker_log:
        worker_log.write(f"[{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}] launching worker: {' '.join(cmd)}\n")
        worker_log.flush()
        subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(APP_DIR),
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _first_available_import(module_names: list[str]) -> tuple[str | None, str | None]:
    errors = []
    for module_name in module_names:
        try:
            if importlib.util.find_spec(module_name) is not None:
                return module_name, None
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    return None, "\n".join(errors)


def _first_distribution_version(distribution_names: list[str]) -> tuple[str | None, str | None]:
    for distribution_name in distribution_names:
        try:
            return importlib.metadata.version(distribution_name), distribution_name
        except importlib.metadata.PackageNotFoundError:
            continue
    return None, None


def dependency_rows() -> list[dict[str, str]]:
    rows = []
    for check in PACKAGE_CHECKS:
        package = str(check["package"])
        imports = [str(name) for name in check.get("imports", [package])]
        distributions = [str(name) for name in check.get("distributions", [package])]
        required = bool(check.get("required", True))
        import_name, import_error = _first_available_import(imports)
        version, distribution_name = _first_distribution_version(distributions)
        installed = import_name is not None
        status = "ready" if installed else "missing"
        if not installed and not required:
            status = "optional"
        rows.append(
            {
                "package": package,
                "label": str(check["label"]),
                "status": status,
                "version": version or "-",
                "import": import_name or ", ".join(imports),
                "distribution": distribution_name or ", ".join(distributions),
                "note": import_error or "",
            }
        )
    return rows


def list_sample_cases() -> list[str]:
    if not SAMPLE_INPUT_ROOT.exists():
        return []
    return sorted(path.name for path in SAMPLE_INPUT_ROOT.iterdir() if path.is_dir())


def sample_case_files(case_name: str) -> tuple[Path | None, Path | None]:
    case_dir = SAMPLE_INPUT_ROOT / case_name
    reactant = case_dir / "reactant.xyz"
    product = case_dir / "product.xyz"
    return (reactant if reactant.exists() else None, product if product.exists() else None)


def list_session_files(session_id: str, extensions: set[str]) -> list[Path]:
    root = session_dir(session_id)
    if not root.exists():
        return []
    return sorted(
        [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def write_uploaded_file(uploaded_file, target_dir: Path, filename: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name(filename, fallback="input.xyz")
    with target_path.open("wb") as out:
        shutil.copyfileobj(uploaded_file, out)
    return target_path


def copy_source_file(source_path: Path, target_dir: Path, filename: str | None = None) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = safe_name(filename or source_path.name, fallback=source_path.name)
    target_path = target_dir / target_name
    shutil.copy2(source_path, target_path)
    return target_path


def all_result_files(run_dir: Path) -> list[Path]:
    if not run_dir.exists():
        return []
    files: list[Path] = []
    for name in RESULT_CANDIDATES:
        path = run_dir / name
        if path.exists():
            files.append(path)
        categorized_path = run_dir / category_for_result_candidate(name) / name
        if categorized_path.exists():
            files.append(categorized_path)
    for pattern in ["*.csv", "*.png", "*.jpg", "*.jpeg", "*.log", "*.txt", "*.json", "*.xyz", "*.traj"]:
        files.extend(sorted(run_dir.rglob(pattern)))
    unique = {}
    for path in files:
        unique[path.resolve()] = path
    return sorted(unique.values(), key=lambda item: str(item.relative_to(run_dir)))


def all_job_json_files(job: dict) -> tuple[Path, list[Path]]:
    """Return JSON files from the complete job directory."""
    root = job_dir(str(job["session_id"]), str(job["job_id"]))
    if not root.exists():
        return root, []
    files = sorted(
        [path for path in root.rglob("*.json") if path.is_file()],
        key=lambda item: str(item.relative_to(root)),
    )
    return root, files


def category_for_result_candidate(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in LOG_EXTENSIONS:
        return "Logs"
    if suffix in JSON_EXTENSIONS:
        return "Json"
    if suffix in XYZ_EXTENSIONS:
        return "XYZ"
    if suffix in TABLE_EXTENSIONS:
        return "Tables"
    return ""


def find_molscout_log(files: list[Path], run_dir: Path) -> Path | None:
    preferred = [run_dir / "Logs" / "molscout.log", run_dir / "molscout.log"]
    for path in preferred:
        if path.exists():
            return path
    matches = [path for path in files if path.name == "molscout.log"]
    return matches[0] if matches else None


def render_molscout_log_expander(files: list[Path], run_dir: Path) -> None:
    log_path = find_molscout_log(files, run_dir)
    with st.expander("molscout.log を表示", expanded=False):
        if log_path is None:
            st.info("molscout.log はまだありません。")
            return
        st.caption(f"log file: `{log_path.relative_to(run_dir)}`")
        st.code(tail_text(log_path, max_lines=500) or "(empty file)", language="text")


def parse_int_list(value: str) -> list[int]:
    if not value.strip():
        return []
    items = []
    for chunk in value.split(","):
        text = chunk.strip()
        if not text:
            continue
        items.append(int(text))
    return items


def workflow_preview_badge(active: bool | None) -> tuple[str, str]:
    if active is True:
        return "ON", "このステージは実行対象です。"
    if active is False:
        return "OFF", "このステージはスキップされます。"
    return "BASE", "ワークフローの共通ステージです。"


def workflow_preview_title(detail: str, outputs: str = "") -> str:
    tooltip = detail
    if outputs:
        tooltip = f"{tooltip}\nOutputs: {outputs}"
    return html.escape(tooltip, quote=True)


def render_workflow_summary_item(label: str, value: str) -> None:
    st.markdown(
        f"""
<div class="workflow-summary">
  <div class="workflow-summary-label">{html.escape(label)}</div>
  <div class="workflow-summary-value">{html.escape(value)}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_workflow_preview_step(
    number: int,
    title: str,
    active: bool | None,
    detail: str,
    outputs: str = "",
) -> None:
    badge, status_help = workflow_preview_badge(active)
    state_class = "inactive" if active is False else "base" if active is None else "active"
    badge_class = "off" if active is False else ""
    tooltip = workflow_preview_title(status_help, outputs)
    st.markdown(
        f"""
<div class="workflow-step {state_class}" title="{tooltip}">
  <div class="workflow-step-number">{number:02d}</div>
  <div class="workflow-step-badge {badge_class}">{badge}</div>
  <div>
    <div class="workflow-step-title">{html.escape(title)}</div>
    <div class="workflow-step-detail">{html.escape(detail)}</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_initial_path_method_preview(selected_method: str, enabled: bool) -> None:
    methods = ["DMF", "NEB", "SCAN"] if selected_method != "CAT" else ["CAT"]
    items = []
    for method_name in methods:
        state_class = "active" if selected_method == method_name else "disabled" if not enabled else ""
        tooltip = (
            f"{method_name} is selected."
            if selected_method == method_name
            else f"{method_name} is available."
            if enabled
            else f"{method_name} is unavailable while Initial Path is OFF."
        )
        items.append(
            f'<div class="workflow-method {state_class}" title="{html.escape(tooltip, quote=True)}">'
            f"{html.escape(method_name)}</div>"
        )
    st.markdown(f'<div class="workflow-methods">{"".join(items)}</div>', unsafe_allow_html=True)


SCAN_TYPE_SPECS = {
    "bond": {
        "example": "0,1",
        "count": 2,
        "unit": "Angstrom",
        "unit_label": "Å",
        "default_step_size": 0.05,
        "default_end_delta": 1.0,
        "help": "bond scan は 2 個の atom indices を指定します。例: 0,1",
    },
    "angle": {
        "example": "0,1,2",
        "count": 3,
        "unit": "degree",
        "unit_label": "deg",
        "default_step_size": 5.0,
        "default_end_delta": 30.0,
        "help": "angle scan は 3 個の atom indices を指定します。例: 0,1,2",
    },
    "dihedral": {
        "example": "0,1,2,3",
        "count": 4,
        "unit": "degree",
        "unit_label": "deg",
        "default_step_size": 10.0,
        "default_end_delta": 360.0,
        "help": "dihedral scan は 4 個の atom indices を指定します。例: 0,1,2,3",
    },
}

SCAN_PRESET_OPTIONS = {
    "custom": {
        "label": "Custom",
        "scan_type": None,
        "range_mode": None,
        "step_mode": None,
        "start_delta": None,
        "end_delta": None,
        "start_val": None,
        "end_val": None,
        "step_size": None,
        "steps": None,
        "description": "手動で scan type、atom indices、範囲、刻みを設定します。",
    },
    "dihedral_twist_pm360": {
        "label": "Dihedral twist ±360 deg / 10 deg",
        "scan_type": "dihedral",
        "range_mode": "relative_window",
        "step_mode": "step_size",
        "start_delta": -360.0,
        "end_delta": 360.0,
        "start_val": None,
        "end_val": None,
        "step_size": 10.0,
        "steps": None,
        "description": "4原子を指定し、現在の二面角を中心に -360 -> +360 deg を走査します。",
    },
    "dihedral_twist_plus360": {
        "label": "Dihedral twist current -> +360 deg / 10 deg",
        "scan_type": "dihedral",
        "range_mode": "relative_forward",
        "step_mode": "step_size",
        "start_delta": 0.0,
        "end_delta": 360.0,
        "start_val": None,
        "end_val": None,
        "step_size": 10.0,
        "steps": None,
        "description": "配座探索・回転障壁用に、現在の二面角から +360 deg まで回します。",
    },
    "dihedral_twist_minus360": {
        "label": "Dihedral twist current -> -360 deg / 10 deg",
        "scan_type": "dihedral",
        "range_mode": "relative_forward",
        "step_mode": "step_size",
        "start_delta": 0.0,
        "end_delta": -360.0,
        "start_val": None,
        "end_val": None,
        "step_size": 10.0,
        "steps": None,
        "description": "逆方向に現在の二面角から -360 deg まで回します。",
    },
    "angle_wag_60_180": {
        "label": "Angle wag / bend 60 -> 180 deg",
        "scan_type": "angle",
        "range_mode": "absolute",
        "step_mode": "step_size",
        "start_delta": None,
        "end_delta": None,
        "start_val": 60.0,
        "end_val": 180.0,
        "step_size": 5.0,
        "steps": None,
        "description": "3原子角を 60 -> 180 deg の絶対角で平面上走査します。",
    },
    "angle_wag_pm30": {
        "label": "Angle wag / bend ±30 deg / 5 deg",
        "scan_type": "angle",
        "range_mode": "relative_window",
        "step_mode": "step_size",
        "start_delta": -30.0,
        "end_delta": 30.0,
        "start_val": None,
        "end_val": None,
        "step_size": 5.0,
        "steps": None,
        "description": "中心原子を軸に、現在角を中心として ±30 deg を首振りします。",
    },
    "bond_stretch_plus1": {
        "label": "Bond stretch current -> +1.0 Å / 0.05 Å",
        "scan_type": "bond",
        "range_mode": "relative_forward",
        "step_mode": "step_size",
        "start_delta": 0.0,
        "end_delta": 1.0,
        "start_val": None,
        "end_val": None,
        "step_size": 0.05,
        "steps": None,
        "description": "2原子距離を現在距離から +1.0 Å まで伸長します。",
    },
    "bond_compress_minus05": {
        "label": "Bond compression current -> -0.5 Å / 0.05 Å",
        "scan_type": "bond",
        "range_mode": "relative_forward",
        "step_mode": "step_size",
        "start_delta": 0.0,
        "end_delta": -0.5,
        "start_val": None,
        "end_val": None,
        "step_size": 0.05,
        "steps": None,
        "description": "2原子距離を現在距離から -0.5 Å まで圧縮します。",
    },
}

SCAN_RANGE_MODE_OPTIONS = {
    "auto_to_absolute": "current -> absolute end",
    "absolute": "absolute start -> absolute end",
    "relative_forward": "current -> current + delta",
    "relative_window": "current + start delta -> current + end delta",
}

SCAN_STEP_MODE_OPTIONS = {
    "steps": "分割数で指定",
    "step_size": "刻み幅で指定",
}


def scan_type_spec(scan_type: str) -> dict[str, object]:
    return SCAN_TYPE_SPECS.get(scan_type, SCAN_TYPE_SPECS["bond"])


def sync_scan_indices_to_type(prefix: str) -> None:
    scan_type_key = f"{prefix}_scan_type"
    previous_key = f"{prefix}_scan_type_previous"
    indices_key = f"{prefix}_scan_indices_text"
    step_size_key = f"{prefix}_scan_step_size"
    end_delta_key = f"{prefix}_scan_end_delta"
    scan_type = str(st.session_state.get(scan_type_key, "bond"))
    previous = str(st.session_state.get(previous_key, "bond"))
    current_indices = str(st.session_state.get(indices_key, "")).strip()
    previous_default = str(scan_type_spec(previous)["example"])

    if not current_indices or current_indices == previous_default:
        st.session_state[indices_key] = str(scan_type_spec(scan_type)["example"])
    st.session_state[step_size_key] = float(scan_type_spec(scan_type)["default_step_size"])
    st.session_state[end_delta_key] = float(scan_type_spec(scan_type)["default_end_delta"])
    st.session_state[previous_key] = scan_type


def sync_scan_preset(prefix: str) -> None:
    preset_key = f"{prefix}_scan_preset"
    preset_name = str(st.session_state.get(preset_key, "custom"))
    preset = SCAN_PRESET_OPTIONS.get(preset_name, SCAN_PRESET_OPTIONS["custom"])
    if preset_name == "custom":
        return

    scan_type = preset.get("scan_type")
    if scan_type:
        st.session_state[f"{prefix}_scan_type"] = str(scan_type)
        st.session_state[f"{prefix}_scan_type_previous"] = str(scan_type)
        st.session_state[f"{prefix}_scan_indices_text"] = str(scan_type_spec(str(scan_type))["example"])

    for preset_field, state_suffix in [
        ("range_mode", "scan_range_mode"),
        ("step_mode", "scan_step_mode"),
        ("start_delta", "scan_start_delta"),
        ("end_delta", "scan_end_delta"),
        ("start_val", "scan_start_val"),
        ("end_val", "scan_end_val"),
        ("step_size", "scan_step_size"),
        ("steps", "scan_steps"),
    ]:
        value = preset.get(preset_field)
        if value is not None:
            st.session_state[f"{prefix}_{state_suffix}"] = value
    if preset.get("range_mode") == "auto_to_absolute":
        st.session_state[f"{prefix}_scan_start_auto"] = True
    elif preset.get("range_mode"):
        st.session_state[f"{prefix}_scan_start_auto"] = False


def _xyz_coordinates_from_text(text: str) -> list[tuple[float, float, float]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("XYZ file is empty.")
    try:
        atom_count = int(lines[0].split()[0])
        atom_lines = lines[2 : 2 + atom_count]
    except (ValueError, IndexError):
        atom_lines = lines

    coordinates: list[tuple[float, float, float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            coordinates.append((float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError:
            continue
    if not coordinates:
        raise ValueError("XYZ coordinates could not be parsed.")
    return coordinates


def _vector(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2])


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(a, a))


def _scale(a: tuple[float, float, float], value: float) -> tuple[float, float, float]:
    return (a[0] * value, a[1] * value, a[2] * value)


def _normalize(a: tuple[float, float, float]) -> tuple[float, float, float]:
    length = _norm(a)
    if length <= 0:
        raise ValueError("Zero-length vector in selected coordinate.")
    return _scale(a, 1.0 / length)


def scan_coordinate_from_xyz_text(text: str, scan_type: str, indices: list[int]) -> float:
    coordinates = _xyz_coordinates_from_text(text)
    if any(index < 0 or index >= len(coordinates) for index in indices):
        raise ValueError(f"Atom index is out of range for {len(coordinates)} atoms.")

    if scan_type == "bond":
        return _norm(_vector(coordinates[indices[0]], coordinates[indices[1]]))

    if scan_type == "angle":
        center = coordinates[indices[1]]
        v1 = _vector(center, coordinates[indices[0]])
        v2 = _vector(center, coordinates[indices[2]])
        denom = _norm(v1) * _norm(v2)
        if denom <= 0:
            raise ValueError("Zero-length vector in selected angle.")
        cosine = max(-1.0, min(1.0, _dot(v1, v2) / denom))
        return math.degrees(math.acos(cosine))

    if scan_type == "dihedral":
        p0, p1, p2, p3 = [coordinates[index] for index in indices]
        b0 = _scale(_vector(p0, p1), -1.0)
        b1 = _vector(p1, p2)
        b2 = _vector(p2, p3)
        b1_unit = _normalize(b1)
        v = tuple(b0[i] - _dot(b0, b1_unit) * b1_unit[i] for i in range(3))
        w = tuple(b2[i] - _dot(b2, b1_unit) * b1_unit[i] for i in range(3))
        angle = math.degrees(math.atan2(_dot(_cross(b1_unit, v), w), _dot(v, w)))
        return angle + 360.0 if angle < 0 else angle

    raise ValueError(f"Unknown scan type: {scan_type}")


def current_scan_coordinate_from_source(
    *,
    source_mode: str,
    mode: str,
    sample_case: str | None,
    reactant_file: object | None,
    scan_type: str,
    scan_indices: list[int],
) -> tuple[float | None, str | None]:
    if mode != "reactant_product" or not scan_indices:
        return None, None

    try:
        if source_mode == "ビルトインサンプルを使う" and sample_case:
            reactant_path, _ = sample_case_files(sample_case)
            if reactant_path is None:
                return None, "sample reactant XYZ が見つかりません。"
            text = reactant_path.read_text(encoding="utf-8")
        elif source_mode == "新たにアップロードする" and reactant_file is not None:
            text = reactant_file.getvalue().decode("utf-8", errors="replace")
        else:
            return None, None
        return scan_coordinate_from_xyz_text(text, scan_type, scan_indices), None
    except Exception as exc:
        return None, str(exc)


def resolve_scan_settings(module_settings: dict[str, object], current_value: float | None) -> tuple[dict[str, object], list[str], list[str]]:
    scan_type = str(module_settings["scan_type"])
    spec = scan_type_spec(scan_type)
    range_mode = str(module_settings.get("scan_range_mode", "auto_to_absolute"))
    step_mode = str(module_settings.get("scan_step_mode", "steps"))
    errors: list[str] = []
    notes: list[str] = []

    if range_mode == "auto_to_absolute":
        start_auto = True
        start_val = None
        end_val = float(module_settings["scan_end_val"])
    elif range_mode == "absolute":
        start_auto = False
        start_val = float(module_settings["scan_start_val"])
        end_val = float(module_settings["scan_end_val"])
    else:
        if current_value is None:
            errors.append("relative SCAN では reactant XYZ から現在値を読める必要があります。reactant file と atom indices を確認してください。")
            current_value = 0.0
        start_auto = False
        start_val = current_value + float(module_settings.get("scan_start_delta", 0.0))
        end_val = current_value + float(module_settings.get("scan_end_delta", 0.0))

    steps = int(module_settings["scan_steps"])
    if step_mode == "step_size":
        step_size = abs(float(module_settings.get("scan_step_size", 0.0)))
        if step_size <= 0:
            errors.append("SCAN 刻み幅は 0 より大きい値にしてください。")
        else:
            if start_auto and current_value is None:
                errors.append("刻み幅指定を使う場合は、reactant XYZ から現在値を読める必要があります。")
            else:
                effective_start = current_value if start_auto else float(start_val)
                span = abs(float(end_val) - float(effective_start))
                if span <= 0:
                    errors.append("SCAN 範囲が 0 です。start と end を変えてください。")
                else:
                    steps = max(1, int(round(span / step_size)))
                    effective = span / steps
                    if abs(effective - step_size) > max(1e-6, step_size * 0.01):
                        notes.append(f"刻み幅 {step_size:g} {spec['unit_label']} は範囲に合わせて実効 {effective:g} {spec['unit_label']} になります。")

    if scan_type == "bond" and not start_auto and (float(start_val) <= 0 or float(end_val) <= 0):
        errors.append("bond scan の距離は 0 Å より大きい必要があります。")
    if steps < 1:
        errors.append("SCAN steps は 1 以上にしてください。")

    resolved = {
        "scan_start_auto": start_auto,
        "scan_start_val": None if start_auto else float(start_val),
        "scan_end_val": float(end_val),
        "scan_steps": int(steps),
    }
    return resolved, errors, notes


def scan_preview_details(module_settings: dict[str, object], current_value: float | None) -> dict[str, object]:
    scan_type = str(module_settings["scan_type"])
    spec = scan_type_spec(scan_type)
    range_mode = str(module_settings.get("scan_range_mode", "auto_to_absolute"))
    step_mode = str(module_settings.get("scan_step_mode", "steps"))
    unit = str(spec["unit_label"])

    if range_mode == "auto_to_absolute":
        range_text = f"current -> {float(module_settings['scan_end_val']):g} {unit}"
    elif range_mode == "absolute":
        range_text = f"{float(module_settings['scan_start_val']):g} -> {float(module_settings['scan_end_val']):g} {unit}"
    else:
        start_delta = float(module_settings.get("scan_start_delta", 0.0))
        end_delta = float(module_settings.get("scan_end_delta", 0.0))
        if current_value is None:
            range_text = f"current {start_delta:+g} -> current {end_delta:+g} {unit}"
        else:
            range_text = f"{current_value + start_delta:g} -> {current_value + end_delta:g} {unit} (current {current_value:g})"

    if step_mode == "step_size":
        step_size = abs(float(module_settings.get("scan_step_size", 0.0)))
        step_text = f"spacing {step_size:g} {unit}"
        if range_mode == "auto_to_absolute":
            start_val = current_value
            end_val = float(module_settings["scan_end_val"])
        elif range_mode == "absolute":
            start_val = float(module_settings["scan_start_val"])
            end_val = float(module_settings["scan_end_val"])
        elif current_value is not None:
            start_val = current_value + float(module_settings.get("scan_start_delta", 0.0))
            end_val = current_value + float(module_settings.get("scan_end_delta", 0.0))
        else:
            start_val = None
            end_val = None
        if start_val is not None and end_val is not None and step_size > 0:
            steps = max(1, int(round(abs(end_val - start_val) / step_size)))
            image_count = steps + 1
        else:
            steps = None
            image_count = None
    else:
        steps = int(module_settings["scan_steps"])
        image_count = steps + 1
        step_text = f"{steps} SCAN steps"
    return {
        "range_text": range_text,
        "step_text": step_text,
        "steps": steps,
        "image_count": image_count,
    }


def format_scan_range_preview(module_settings: dict[str, object], current_value: float | None) -> str:
    details = scan_preview_details(module_settings, current_value)
    image_text = (
        f"{details['image_count']} images / {details['steps']} SCAN steps"
        if details["image_count"] is not None and details["steps"] is not None
        else "image count pending current value"
    )
    return f"{details['range_text']} / {details['step_text']} / {image_text}"


def sync_initial_path_method_defaults(prefix: str, refine_input_key: str) -> None:
    if st.session_state.get(f"{prefix}_init_path_method") == "SCAN":
        st.session_state[refine_input_key] = False


def render_initial_path_live_controls(prefix: str, *, refine_input_key: str | None = None) -> str:
    key = f"{prefix}_init_path_method"
    st.session_state.setdefault(key, "DMF")
    kwargs = {}
    if refine_input_key is not None:
        kwargs = {
            "on_change": sync_initial_path_method_defaults,
            "args": (prefix, refine_input_key),
        }
    return st.selectbox("Initial path method", ["DMF", "NEB", "SCAN"], key=key, **kwargs)


def widget_default_kwargs(key: str, **defaults) -> dict:
    return {} if key in st.session_state else defaults


def selectbox_default_kwargs(key: str, options: list[str], default: str) -> dict:
    if key in st.session_state or default not in options:
        return {}
    return {"index": options.index(default)}


def render_dmf_settings(prefix: str) -> dict[str, object]:
    with st.expander("DMF settings", expanded=True):
        dmf_cols = st.columns(3)
        nmove_key = f"{prefix}_nmove"
        convergence_key = f"{prefix}_dmf_convergence"
        update_key = f"{prefix}_update_teval"
        convergence_options = ["tight", "normal", "loose"]
        nmove = dmf_cols[0].number_input("NMOVE", min_value=1, step=1, key=nmove_key, **widget_default_kwargs(nmove_key, value=40))
        dmf_convergence = dmf_cols[1].selectbox(
            "DMF convergence",
            convergence_options,
            key=convergence_key,
            **selectbox_default_kwargs(convergence_key, convergence_options, "tight"),
        )
        update_teval = dmf_cols[2].checkbox("UPDATE_TEVAL", key=update_key, **widget_default_kwargs(update_key, value=False))
    return {
        "nmove": nmove,
        "dmf_convergence": dmf_convergence,
        "update_teval": update_teval,
    }


def render_neb_settings(prefix: str) -> dict[str, object]:
    with st.expander("NEB settings", expanded=True):
        neb_cols = st.columns(3)
        images_key = f"{prefix}_neb_images"
        spring_key = f"{prefix}_neb_spring_constant"
        climb_key = f"{prefix}_neb_climb"
        neb_images = neb_cols[0].number_input("NEB images", min_value=2, step=1, key=images_key, **widget_default_kwargs(images_key, value=10))
        neb_spring_constant = neb_cols[1].number_input(
            "NEB spring constant",
            min_value=0.01,
            step=0.01,
            format="%.3f",
            key=spring_key,
            **widget_default_kwargs(spring_key, value=0.1),
        )
        neb_climb = neb_cols[2].checkbox("NEB climb", key=climb_key, **widget_default_kwargs(climb_key, value=True))
    return {
        "neb_images": neb_images,
        "neb_spring_constant": neb_spring_constant,
        "neb_climb": neb_climb,
    }


def render_scan_settings(prefix: str, current_scan_value: float | None = None, current_scan_error: str | None = None) -> dict[str, object]:
    scan_type_key = f"{prefix}_scan_type"
    previous_key = f"{prefix}_scan_type_previous"
    indices_key = f"{prefix}_scan_indices_text"
    steps_key = f"{prefix}_scan_steps"
    step_mode_key = f"{prefix}_scan_step_mode"
    step_size_key = f"{prefix}_scan_step_size"
    range_mode_key = f"{prefix}_scan_range_mode"
    start_auto_key = f"{prefix}_scan_start_auto"
    start_val_key = f"{prefix}_scan_start_val"
    end_val_key = f"{prefix}_scan_end_val"
    start_delta_key = f"{prefix}_scan_start_delta"
    end_delta_key = f"{prefix}_scan_end_delta"
    preset_key = f"{prefix}_scan_preset"

    st.session_state.setdefault(scan_type_key, "bond")
    st.session_state.setdefault(previous_key, st.session_state[scan_type_key])
    st.session_state.setdefault(indices_key, str(scan_type_spec(str(st.session_state[scan_type_key]))["example"]))
    st.session_state.setdefault(steps_key, 10)
    st.session_state.setdefault(step_mode_key, "steps")
    st.session_state.setdefault(step_size_key, float(scan_type_spec(str(st.session_state[scan_type_key]))["default_step_size"]))
    st.session_state.setdefault(range_mode_key, "auto_to_absolute")
    st.session_state.setdefault(start_auto_key, True)
    st.session_state.setdefault(start_val_key, 0.0)
    st.session_state.setdefault(end_val_key, 2.0)
    st.session_state.setdefault(start_delta_key, 0.0)
    st.session_state.setdefault(end_delta_key, float(scan_type_spec(str(st.session_state[scan_type_key]))["default_end_delta"]))
    st.session_state.setdefault(preset_key, "custom")

    with st.expander("SCAN settings", expanded=True):
        preset_options = list(SCAN_PRESET_OPTIONS.keys())
        preset = st.selectbox(
            "SCAN quick preset",
            preset_options,
            key=preset_key,
            format_func=lambda value: SCAN_PRESET_OPTIONS[value]["label"],
            on_change=sync_scan_preset,
            args=(prefix,),
            help="直感入力用のテンプレートです。選択後も atom indices や範囲は調整できます。",
        )
        preset_description = str(SCAN_PRESET_OPTIONS[str(preset)]["description"])
        if preset_description:
            st.caption(preset_description)

        scan_cols = st.columns([1, 1.4, 1])
        scan_type = scan_cols[0].selectbox(
            "SCAN type",
            list(SCAN_TYPE_SPECS.keys()),
            key=scan_type_key,
            on_change=sync_scan_indices_to_type,
            args=(prefix,),
        )
        spec = scan_type_spec(str(scan_type))
        scan_cols[1].text_input(
            "SCAN indices",
            key=indices_key,
            placeholder=str(spec["example"]),
            help=str(spec["help"]),
        )
        scan_cols[2].selectbox(
            "Range mode",
            list(SCAN_RANGE_MODE_OPTIONS.keys()),
            key=range_mode_key,
            format_func=lambda value: SCAN_RANGE_MODE_OPTIONS[value],
        )

        range_mode = str(st.session_state.get(range_mode_key, "auto_to_absolute"))
        step = 0.05 if scan_type == "bond" else 1.0
        if range_mode == "auto_to_absolute":
            st.session_state[start_auto_key] = True
            value_cols = st.columns(2)
            value_cols[0].metric("SCAN start", "current")
            value_cols[1].number_input(f"SCAN end value [{spec['unit']}]", step=step, format="%.3f", key=end_val_key)
        elif range_mode == "absolute":
            st.session_state[start_auto_key] = False
            value_cols = st.columns(2)
            value_cols[0].number_input(f"SCAN start value [{spec['unit']}]", step=step, format="%.3f", key=start_val_key)
            value_cols[1].number_input(f"SCAN end value [{spec['unit']}]", step=step, format="%.3f", key=end_val_key)
        elif range_mode == "relative_forward":
            st.session_state[start_auto_key] = False
            value_cols = st.columns(2)
            value_cols[0].number_input(f"Start delta [{spec['unit']}]", step=step, format="%.3f", key=start_delta_key)
            value_cols[1].number_input(f"End delta [{spec['unit']}]", step=step, format="%.3f", key=end_delta_key)
        else:
            st.session_state[start_auto_key] = False
            value_cols = st.columns(2)
            value_cols[0].number_input(f"Window start delta [{spec['unit']}]", step=step, format="%.3f", key=start_delta_key)
            value_cols[1].number_input(f"Window end delta [{spec['unit']}]", step=step, format="%.3f", key=end_delta_key)

        step_cols = st.columns([1, 1])
        step_cols[0].selectbox(
            "Spacing",
            list(SCAN_STEP_MODE_OPTIONS.keys()),
            key=step_mode_key,
            format_func=lambda value: SCAN_STEP_MODE_OPTIONS[value],
        )
        if st.session_state.get(step_mode_key) == "step_size":
            step_cols[1].number_input(f"Step size [{spec['unit']}]", min_value=0.0001, step=step, format="%.4f", key=step_size_key)
        else:
            step_cols[1].number_input("SCAN steps", min_value=1, step=1, key=steps_key)

        scan_settings = collect_scan_settings(prefix)
        st.caption(f"{spec['count']} atom indices / unit: {spec['unit']} / preview: {format_scan_range_preview(scan_settings, current_scan_value)}")
        if current_scan_error:
            st.caption(f"Current value preview unavailable: {current_scan_error}")
        elif current_scan_value is not None:
            st.caption(f"Current {scan_type}: {current_scan_value:.4f} {spec['unit_label']}")

    return collect_scan_settings(prefix)


def collect_scan_settings(prefix: str) -> dict[str, object]:
    return {
        "scan_preset": st.session_state.get(f"{prefix}_scan_preset", "custom"),
        "scan_type": st.session_state.get(f"{prefix}_scan_type", "bond"),
        "scan_indices_text": st.session_state.get(f"{prefix}_scan_indices_text", "0,1"),
        "scan_steps": st.session_state.get(f"{prefix}_scan_steps", 10),
        "scan_step_mode": st.session_state.get(f"{prefix}_scan_step_mode", "steps"),
        "scan_step_size": st.session_state.get(f"{prefix}_scan_step_size", 0.05),
        "scan_range_mode": st.session_state.get(f"{prefix}_scan_range_mode", "auto_to_absolute"),
        "scan_start_auto": st.session_state.get(f"{prefix}_scan_start_auto", True),
        "scan_start_val": st.session_state.get(f"{prefix}_scan_start_val", 0.0),
        "scan_end_val": st.session_state.get(f"{prefix}_scan_end_val", 2.0),
        "scan_start_delta": st.session_state.get(f"{prefix}_scan_start_delta", 0.0),
        "scan_end_delta": st.session_state.get(f"{prefix}_scan_end_delta", 1.0),
    }


def collect_initial_path_settings(prefix: str) -> dict[str, object]:
    values: dict[str, object] = {
        "init_path_method": st.session_state.get(f"{prefix}_init_path_method", "DMF"),
        "nmove": st.session_state.get(f"{prefix}_nmove", 40),
        "dmf_convergence": st.session_state.get(f"{prefix}_dmf_convergence", "tight"),
        "update_teval": st.session_state.get(f"{prefix}_update_teval", False),
        "neb_images": st.session_state.get(f"{prefix}_neb_images", 10),
        "neb_spring_constant": st.session_state.get(f"{prefix}_neb_spring_constant", 0.1),
        "neb_climb": st.session_state.get(f"{prefix}_neb_climb", True),
    }
    values.update(collect_scan_settings(prefix))
    return values


def render_initial_path_selected_settings(
    prefix: str,
    init_path_method: str,
    current_scan_value: float | None = None,
    current_scan_error: str | None = None,
) -> dict[str, object]:
    if init_path_method == "DMF":
        render_dmf_settings(prefix)
    elif init_path_method == "NEB":
        render_neb_settings(prefix)
    elif init_path_method == "SCAN":
        render_scan_settings(prefix, current_scan_value=current_scan_value, current_scan_error=current_scan_error)
    return collect_initial_path_settings(prefix)


def render_method_live_controls(prefix: str) -> tuple[str, str, str, bool]:
    method_key = f"{prefix}_method"
    custom_key = f"{prefix}_custom"
    orbmol_key = f"{prefix}_orbmol_version"
    alpb_key = f"{prefix}_alpb_solvent"
    ui_method_options = [m for m in METHOD_OPTIONS if m != "orbmol+alpb"]
    alpb_options = ["None", "water", "acetonitrile", "methanol", "ethanol", "dichloromethane"]
    st.session_state.setdefault(custom_key, "orbmol")

    with st.container(border=True):
        st.markdown("**Calculation method**")
        method_cols = st.columns(4)
        method_choice = method_cols[0].selectbox("Method", ui_method_options, key=method_key)
        method = (
            method_cols[1].text_input("custom method", key=custom_key)
            if method_choice == "custom"
            else str(method_choice)
        )
        is_orbmol = method == "orbmol"
        if not is_orbmol:
            st.session_state[alpb_key] = "None"
        if DEFAULT_ORBMOL_VERSION in ORBMOL_VERSION_OPTIONS:
            orbmol_index = ORBMOL_VERSION_OPTIONS.index(DEFAULT_ORBMOL_VERSION)
        else:
            orbmol_index = 0
        orbmol_version = method_cols[2].selectbox(
            "OrbMol version",
            ORBMOL_VERSION_OPTIONS,
            key=orbmol_key,
            disabled=not is_orbmol,
            **({} if orbmol_key in st.session_state else {"index": orbmol_index}),
        )
        alpb_solvent = method_cols[3].selectbox(
            "Add ALPB solvent",
            alpb_options,
            key=alpb_key,
            disabled=not is_orbmol,
            **selectbox_default_kwargs(alpb_key, alpb_options, "None"),
        )
    return method, str(orbmol_version), str(alpb_solvent), is_orbmol and alpb_solvent != "None"


@st.dialog("Workflow structure preview")
def render_workflow_preview_dialog(
    *,
    preset: str,
    mode: str,
    source_mode: str,
    init_path_method: str,
    do_path: bool,
    do_ts: bool,
    do_irc: bool,
    do_vib: bool,
    do_refine: bool,
    refine_input_on: bool,
    pick_optpoints_on: bool,
    save_fig_on: bool,
) -> None:
    summary_cols = st.columns(3)
    with summary_cols[0]:
        render_workflow_summary_item("Preset", WORKFLOW_DISPLAY_LABELS.get(preset, preset))
    with summary_cols[1]:
        render_workflow_summary_item("Input mode", mode.replace("_", " "))
    with summary_cols[2]:
        render_workflow_summary_item("Source", source_mode)
    st.write("")

    if preset == "Figure refresh only":
        render_workflow_preview_step(
            1,
            "Load existing trajectory and result CSV",
            None,
            "既存 trajectory と result CSV を読み込み、計算ステージは実行しません。",
            "existing result.csv",
        )
        render_workflow_preview_step(
            2,
            "Regenerate profile figure",
            save_fig_on,
            "`SAVE_FIG_ON` の設定に従って figure を再生成します。",
            "fig_result.png",
        )
        render_workflow_preview_step(
            3,
            "Finalize run",
            None,
            "timing log と job metadata を保存して終了します。",
            "timing.log / molscout.log",
        )
        return

    input_detail = {
        "reactant_product": "reactant.xyz / product.xyz から初期経路を作成します。",
        "single_input": "既存 trajectory または coordinate file を読み込みます。",
        "single_input_with_result": "既存 trajectory と result CSV を読み込みます。",
    }.get(mode, "選択した入力ファイルを読み込みます。")

    render_workflow_preview_step(1, "Input files", None, input_detail)
    render_workflow_preview_step(
        2,
        "Initial path search",
        do_path,
        (
            f"`INIT_PATH_METHOD = {init_path_method}`。"
            f" 初期構造最適化は {'ON' if refine_input_on else 'OFF'} です。"
        )
        if do_path
        else "既存 trajectory / coordinate input を使うため、初期経路作成はスキップされます。",
        "init_path.traj / init_path.xyz" if do_path else "",
    )
    render_initial_path_method_preview(init_path_method, do_path)
    render_workflow_preview_step(
        3,
        "Evaluate single point energies",
        None,
        "入力された経路または生成された初期経路に対して single point energy を評価します。",
        "result.csv",
    )
    render_workflow_preview_step(
        4,
        "Extract local maxima and endpoints",
        None,
        "TS optimization や後続解析に使う候補点を result.csv から抽出します。",
    )
    render_workflow_preview_step(
        5,
        "TS optimization",
        do_ts,
        "`TSOPT_ON` に対応します。ON の場合は Sella による TS optimization を実行します。",
        "*_tsopt.xyz / *_tsopt.traj" if do_ts else "",
    )
    render_workflow_preview_step(
        6,
        "IRC forward and reverse",
        do_irc,
        "`IRC_ON` に対応します。ON の場合は forward / reverse の AdaptiveIRC を実行します。",
        "*_irc0/1 / irc.traj" if do_irc else "",
    )
    render_workflow_preview_step(
        7,
        "Pick and re-optimize optpoints",
        pick_optpoints_on,
        "`PICK_OPTPOINTS_ON` に対応します。候補点を抽出し、必要に応じて再最適化します。",
        "optpoints.traj / optpoints.xyz" if pick_optpoints_on else "",
    )
    render_workflow_preview_step(
        8,
        "Vibration and thermo",
        do_vib,
        "`VIB_ON` に対応します。振動解析と熱力学補正を計算します。",
        "*_vibsummary.txt / *_vib_*.xyz" if do_vib else "",
    )
    render_workflow_preview_step(
        9,
        "High-level energy refine",
        do_refine,
        "`REFINE_ENERGY_ON` に対応します。PySCF による高精度 single point を実行します。",
        "*_refine_pyscf.json / *.molden" if do_refine else "",
    )
    render_workflow_preview_step(
        10,
        "Figure and final logs",
        save_fig_on,
        "`SAVE_FIG_ON` に対応します。最後に profile figure と log を保存します。",
        "fig_result.png / timing.log / molscout.log" if save_fig_on else "timing.log / molscout.log",
    )


@st.dialog("Concatenation workflow preview")
def render_concat_workflow_preview_dialog(
    *,
    source_mode: str,
    do_opt: bool,
    do_vib: bool,
    do_refine: bool,
    pick_optpoints_on: bool,
    save_fig_on: bool,
) -> None:
    summary_cols = st.columns(3)
    with summary_cols[0]:
        render_workflow_summary_item("Preset", "Concatenation & Batch")
    with summary_cols[1]:
        render_workflow_summary_item("Input mode", "catfiles")
    with summary_cols[2]:
        render_workflow_summary_item("Source", source_mode)
    st.write("")

    render_workflow_preview_step(
        1,
        "Input files",
        None,
        "複数の .xyz / .traj files を指定順に読み込みます。",
        "uploaded files / selected session files",
    )
    render_workflow_preview_step(
        2,
        "Concatenate frames",
        None,
        "`INIT_PATH_METHOD = CAT` として、入力ファイル群を 1 つの trajectory に連結します。",
        "init_path.traj / init_path.xyz",
    )
    render_initial_path_method_preview("CAT", False)
    render_workflow_preview_step(
        3,
        "Evaluate single point energies",
        None,
        "連結した全 frame に対して single point energy を評価します。",
        "result.csv",
    )
    render_workflow_preview_step(
        4,
        "Structure optimization",
        do_opt,
        "`REFINE_INPUT_ON` に対応します。ON の場合は各 frame の structure optimization を実行します。",
    )
    render_workflow_preview_step(
        5,
        "Pick and re-optimize optpoints",
        pick_optpoints_on,
        "`PICK_OPTPOINTS_ON` に対応します。候補点を抽出し、必要に応じて再最適化します。",
        "optpoints.traj / optpoints.xyz" if pick_optpoints_on else "",
    )
    render_workflow_preview_step(
        6,
        "Vibration and thermo",
        do_vib,
        "`VIB_ON` に対応します。batch対象に対して振動解析と熱力学補正を計算します。",
        "*_vibsummary.txt / *_vib_*.xyz" if do_vib else "",
    )
    render_workflow_preview_step(
        7,
        "High-level energy refine",
        do_refine,
        "`REFINE_ENERGY_ON` に対応します。PySCF による高精度 single point を実行します。",
        "*_refine_pyscf.json / *.molden" if do_refine else "",
    )
    render_workflow_preview_step(
        8,
        "Figure and final logs",
        save_fig_on,
        "`SAVE_FIG_ON` に対応します。最後に profile figure と log を保存します。",
        "fig_result.png / timing.log / molscout.log" if save_fig_on else "timing.log / molscout.log",
    )


def render_module_settings(
    prefix: str,
    *,
    include_initial_path_method: bool,
    current_scan_value: float | None = None,
    current_scan_error: str | None = None,
) -> dict:
    values: dict[str, object] = {}
    with st.expander("Detailed Module Settings", expanded=False):
        st.caption("このジョブのみに適用する `core/default_config.py` 相当のモジュール設定です。")

        if include_initial_path_method:
            values.update(collect_initial_path_settings(prefix))
            
        else:
            values["init_path_method"] = "CAT"
            values["nmove"] = 40
            values["dmf_convergence"] = "tight"
            values["update_teval"] = False
            values["neb_images"] = 10
            values["neb_spring_constant"] = 0.1
            values["neb_climb"] = True
            values["scan_preset"] = "custom"
            values["scan_type"] = "bond"
            values["scan_indices_text"] = "0,1"
            values["scan_steps"] = 10
            values["scan_step_mode"] = "steps"
            values["scan_step_size"] = 0.05
            values["scan_range_mode"] = "auto_to_absolute"
            values["scan_start_auto"] = True
            values["scan_start_val"] = 0.0
            values["scan_end_val"] = 2.0
            values["scan_start_delta"] = 0.0
            values["scan_end_delta"] = 1.0

        opt_cols = st.columns(4)
        use_sella_key = f"{prefix}_use_sella_in_opt"
        opt_fmax_key = f"{prefix}_opt_fmax"
        tsopt_fmax_key = f"{prefix}_tsopt_fmax"
        refine_calc_key = f"{prefix}_refine_calc_type"
        refine_calc_options = ["pyscf_high", "pyscf"]
        values["use_sella_in_opt"] = opt_cols[0].checkbox(
            "optimization で Sella を使用",
            key=use_sella_key,
            **widget_default_kwargs(use_sella_key, value=False),
        )
        values["opt_fmax"] = opt_cols[1].number_input(
            "OPT fmax",
            min_value=0.0001,
            step=0.001,
            format="%.4f",
            key=opt_fmax_key,
            **widget_default_kwargs(opt_fmax_key, value=0.01),
        )
        values["tsopt_fmax"] = opt_cols[2].number_input(
            "TSOPT fmax",
            min_value=0.0001,
            step=0.0001,
            format="%.4f",
            key=tsopt_fmax_key,
            **widget_default_kwargs(tsopt_fmax_key, value=0.0004),
        )
        values["refine_calc_type"] = opt_cols[3].selectbox(
            "Refine method",
            refine_calc_options,
            key=refine_calc_key,
            **selectbox_default_kwargs(refine_calc_key, refine_calc_options, "pyscf_high"),
        )

        sella_cols = st.columns(4)
        sella_auto_key = f"{prefix}_sella_internal_auto"
        sella_internal_key = f"{prefix}_sella_internal"
        irc_dx_init_key = f"{prefix}_irc_dx_init"
        irc_dx_max_key = f"{prefix}_irc_dx_max"
        values["sella_internal_auto"] = sella_cols[0].checkbox(
            "SELLA internal auto",
            key=sella_auto_key,
            **widget_default_kwargs(sella_auto_key, value=True),
        )
        values["sella_internal"] = sella_cols[1].checkbox(
            "SELLA internal",
            key=sella_internal_key,
            **widget_default_kwargs(sella_internal_key, value=True),
        )
        values["irc_dx_init"] = sella_cols[2].number_input(
            "IRC dx init",
            min_value=0.001,
            step=0.01,
            format="%.3f",
            key=irc_dx_init_key,
            **widget_default_kwargs(irc_dx_init_key, value=0.06),
        )
        values["irc_dx_max"] = sella_cols[3].number_input(
            "IRC dx max",
            min_value=0.001,
            step=0.01,
            format="%.3f",
            key=irc_dx_max_key,
            **widget_default_kwargs(irc_dx_max_key, value=0.12),
        )

        tail_cols = st.columns(3)
        irc_dx_min_key = f"{prefix}_irc_dx_min"
        optpoints_again_key = f"{prefix}_opt_optpoints_again_on"
        fixed_atoms_key = f"{prefix}_fixed_atoms_text"
        values["irc_dx_min"] = tail_cols[0].number_input(
            "IRC dx min",
            min_value=0.001,
            step=0.01,
            format="%.3f",
            key=irc_dx_min_key,
            **widget_default_kwargs(irc_dx_min_key, value=0.02),
        )
        values["opt_optpoints_again_on"] = tail_cols[1].checkbox(
            "optpoints を再び構造最適化",
            key=optpoints_again_key,
            **widget_default_kwargs(optpoints_again_key, value=False),
        )
        values["fixed_atoms_text"] = st.text_input(
            "Fixed atoms",
            key=fixed_atoms_key,
            help="comma 区切りの atom indices。例: 0,1,2",
            **widget_default_kwargs(fixed_atoms_key, value=""),
        )
        
    return values


def section_switch(label: str, options: list[str], *, key: str, captions: dict[str, str] | None = None) -> str:
    if key not in st.session_state or st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    if hasattr(st, "segmented_control"):
        choice = st.segmented_control(label, options, key=key, label_visibility="collapsed")
    else:
        choice = st.radio(label, options, horizontal=True, key=key, label_visibility="collapsed")
    if captions:
        st.caption(captions.get(choice, ""))
    return choice


def append_monitor_history(name: str, value: float | None, *, max_points: int = 24) -> list[float | None]:
    key = f"monitor_history_{name}"
    history = list(st.session_state.get(key, []))
    history.append(value)
    st.session_state[key] = history[-max_points:]
    return st.session_state[key]


def sparkline_svg(
    history: list[float | None],
    *,
    color: str,
    width: int = 240,
    height: int = 42,
    min_value: float | None = None,
    max_value: float | None = None,
) -> str:
    points = [point for point in history if point is not None and math.isfinite(point)]
    if len(points) < 2:
        return ""

    clean_history: list[float] = []
    last = float(points[0])
    for point in history:
        if point is None or not math.isfinite(point):
            clean_history.append(last)
        else:
            last = float(point)
            clean_history.append(last)

    minimum = min(clean_history) if min_value is None else float(min_value)
    maximum = max(clean_history) if max_value is None else float(max_value)
    span = maximum - minimum
    if span <= 0:
        pad = max(abs(maximum) * 0.1, 1.0)
        minimum -= pad
        maximum += pad
        span = maximum - minimum

    step_x = width / max(len(clean_history) - 1, 1)
    coords: list[tuple[float, float]] = []
    for index, value in enumerate(clean_history):
        x = index * step_x
        normalized = (value - minimum) / span
        y = height - normalized * (height - 4) - 2
        coords.append((x, y))

    line_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    area_d = f"{line_d} L {coords[-1][0]:.1f} {height:.1f} L {coords[0][0]:.1f} {height:.1f} Z"
    return f"""
<div class="status-card-chart" aria-hidden="true">
  <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none">
    <path class="area" d="{area_d}" fill="{color}"></path>
    <path class="line" d="{line_d}" stroke="{color}"></path>
  </svg>
</div>
"""


def render_resource_card(
    label: str,
    main_text: str,
    sub_text: str,
    history: list[float | None] | None = None,
    *,
    color: str = "#0F766E",
    chart_min: float | None = None,
    chart_max: float | None = None,
) -> None:
    chart_html = sparkline_svg(history or [], color=color, min_value=chart_min, max_value=chart_max) if history else ""
    st.markdown(
        f"""
<div class="status-card">
  <div class="status-card-label">{label}</div>
  <div class="status-card-main">{main_text}</div>
  <div class="status-card-sub">{sub_text}</div>
  {chart_html}
</div>
""",
        unsafe_allow_html=True,
    )


def render_queue_status_card(queue_state_label: str, queued_count: int, running_count: int, stopping_count: int) -> None:
    extra_bits = []
    if running_count:
        extra_bits.append(f"{running_count} running")
    if stopping_count:
        extra_bits.append(f"{stopping_count} stopping")
    extra_text = " | ".join(extra_bits) if extra_bits else "No active worker task"
    st.markdown(
        f"""
<div class="status-card">
  <div class="status-card-label">Queue</div>
  <div class="status-card-main">{queue_state_label}</div>
  <div class="status-card-sub">{queued_count} waiting | {extra_text}</div>
</div>
""",
        unsafe_allow_html=True,
    )


@st.fragment(run_every=AUTO_REFRESH_SECONDS)
def sidebar_monitor_fragment() -> None:
    snapshot = system_snapshot()
    queue = queue_snapshot()
    queued_count = sum(item["status"] == "queued" for item in queue["jobs"])
    running_count = sum(item["status"] == "running" for item in queue["jobs"])
    stopping_count = sum(item["status"] == "cancel_requested" for item in queue["jobs"])

    st.markdown("## :material/monitoring: Monitor")
    st.caption("Live server and queue status")
    memory = snapshot["memory"]
    disk = snapshot["disk"]
    gpu_rows = snapshot["gpus"]

    queue_state = "Running" if running_count else "Idle"
    if stopping_count:
        queue_state = "Stopping"
    render_queue_status_card(queue_state, queued_count, running_count, stopping_count)
    
    # --- CPU Util & GPU Util ---
    load_cols = st.columns(2)
    cpu_util = snapshot.get("cpu_util_pct")
    cpu_history = append_monitor_history("cpu_util_pct", cpu_util)
    with load_cols[0]:
        render_resource_card(
            "CPU Util",
            "-" if cpu_util is None else f"{cpu_util:.0f}%",
            "host CPU occupancy (0-100%)",
            cpu_history,
            color="#0F766E",
            chart_min=0,
            chart_max=100,
        )
    
    if gpu_rows:
        gpu = gpu_rows[0]
        gpu_load = gpu.get("util_pct", None)
        if isinstance(gpu_load, (int, float)):
            gpu_text = f"{gpu_load:.0f}%"
        elif gpu_load is not None:
            gpu_text = str(gpu_load)
        else:
            gpu_text = "N/A"
    else:
        gpu_load = None
        gpu_text = "-"
    gpu_history = append_monitor_history("gpu_util_pct", gpu_load)
    with load_cols[1]:
        render_resource_card(
            "GPU Util",
            gpu_text,
            "primary accelerator occupancy (0-100%)",
            gpu_history,
            color="#2563EB",
            chart_min=0,
            chart_max=100,
        )

    # --- Memory ---
    mem_history = append_monitor_history("memory_used_pct", memory["used_pct"])
    render_resource_card(
        "Memory",
        "-" if memory["used_gb"] is None else f"{memory['used_gb']:.1f}/{memory['total_gb']:.1f} GB",
        "-" if memory["used_pct"] is None else f"{memory['used_pct']:.0f}% used (0-100%)",
        mem_history,
        color="#F59E0B",
        chart_min=0,
        chart_max=100,
    )
    
    # --- GPU Memory ---
    if gpu_rows:
        gpu = gpu_rows[0]
        mem_used = gpu.get("mem_used_mb", None)
        mem_total = gpu.get("mem_total_mb", None)
        
        if isinstance(mem_used, (int, float)) and isinstance(mem_total, (int, float)):
            gpu_mem_text = f"{mem_used/1024:.1f}/{mem_total/1024:.1f} GB"
            gpu_mem_sub = f"{(mem_used / mem_total * 100.0):.0f}% used" if mem_total else "-"
        elif mem_used is not None and mem_total is not None:
            gpu_mem_text = f"{mem_used} / {mem_total}"
            gpu_mem_sub = "accelerator memory"
        else:
            gpu_mem_text = "N/A"
            gpu_mem_sub = "accelerator memory"
        gpu_mem_pct = (mem_used / mem_total * 100.0) if isinstance(mem_used, (int, float)) and isinstance(mem_total, (int, float)) and mem_total else None
        gpu_mem_history = append_monitor_history("gpu_mem_pct", gpu_mem_pct)
        render_resource_card(
            "GPU memory",
            gpu_mem_text,
            f"{gpu_mem_sub} (0-100%)" if gpu_mem_sub.endswith("% used") else gpu_mem_sub,
            gpu_mem_history,
            color="#7C3AED",
            chart_min=0,
            chart_max=100,
        )
    else:
        render_resource_card(
            "GPU memory",
            "-",
            "No GPU metrics detected",
            None,
            color="#7C3AED",
        )

    with st.expander("GPU details", expanded=False):
        if gpu_rows:
            st.dataframe(pd.DataFrame(gpu_rows), hide_index=True, height=180)
        else:
            st.caption("この環境では GPU metrics は検出されませんでした。")

    with st.expander("Worker log", expanded=False):
        log_text = tail_text(WORKER_LOG_FILE, max_lines=80) or "No worker log yet."
        log_text = format_worker_log_time(log_text)
        st.text_area("Log", value=log_text, height=200, disabled=True, label_visibility="collapsed")


def render_queue_panel() -> None:
    cols = st.columns([1, 1])
    cols[0].markdown("## :material/lan: Shared queue")
    with cols[1]:
        if st.button(":material/refresh: 表示を更新", width="stretch"):
            st.rerun()

    state = sync_queue_state()
    jobs = []
    for item in state["jobs"]:
        job = reload_job(item["session_id"], item["job_id"])
        if job:
            jobs.append(
                {
                    "session": item["session_id"],
                    "job_id": item["job_id"],
                    "status": job["status"],
                    "workflow": job["workflow"],
                    "owner": job.get("owner_label", "anonymous"),
                    "job note": job.get("notes", ""),
                    "created_at": format_app_time(job.get("created_at", "")),
                }
            )
    if jobs:
        st.dataframe(pd.DataFrame(jobs), hide_index=True, height=240)
    else:
        st.info("現在キューにジョブはありません。")


def refresh_jobs(session_id: str, jobs: list[dict], *, only_job_id: str | None = None) -> dict[str, dict]:
    refreshed: dict[str, dict] = {}
    for job in jobs:
        if only_job_id and job["job_id"] != only_job_id:
            continue
        if job.get("status") not in REFRESHABLE_JOB_STATUSES:
            continue
        updated = reload_job(session_id, job["job_id"])
        if updated:
            refreshed[job["job_id"]] = updated
    return refreshed


@st.cache_data(ttl="10m", max_entries=64, show_spinner=False)
def cached_selected_jobs_archive(
    session_id: str,
    job_ids: tuple[str, ...],
    flat: bool,
    include_merged_csv: bool,
    signature: tuple[tuple[str, str, str], ...],
) -> str:
    del signature
    archive_path = build_selected_jobs_archive(
        session_id,
        list(job_ids),
        flat=flat,
        include_merged_csv=include_merged_csv,
    )
    return str(archive_path)


@st.cache_data(ttl="10m", max_entries=64, show_spinner=False)
def cached_merged_csv_archive(
    session_id: str,
    job_ids: tuple[str, ...],
    flat: bool,
    signature: tuple[tuple[str, str, str], ...],
) -> str | None:
    del signature
    archive_path = build_merged_csv_archive(session_id, list(job_ids), flat=flat)
    return str(archive_path) if archive_path else None


def render_job_detail(
    session_id: str,
    job: dict,
    jobs: list[dict],
    index: int,
    *,
    show_summary: bool = True,
) -> None:
    if show_summary:
        meta = st.columns(5)
        meta[0].metric("Status", job["status"])
        meta[1].metric("Method", job.get("method", "-"))
        meta[2].metric("Charge", job.get("charge", 0))
        meta[3].metric("Created", format_app_time(job.get("created_at")))
        meta[4].metric(
            "Exit code",
            "-" if job.get("exit_code") is None else str(job["exit_code"]),
        )
        if job.get("completion_reason") or job.get("status_message"):
            st.caption(
                f"結果: {job.get('completion_reason') or '-'}"
                + (f" | {job['status_message']}" if job.get("status_message") else "")
            )

        selected_steps = [
            label
            for key, label in [
                ("initial_path", "Initial Path"),
                ("ts_opt", "TS Opt"),
                ("irc", "IRC"),
                ("vib", "Vib & Thermo"),
                ("refine", "Energy Refine"),
            ]
            if job.get("workflow_steps", {}).get(key)
        ]
        if selected_steps:
            st.caption("Steps: " + ", ".join(selected_steps))
        st.caption(
            f"Thermo temperature: {job.get('temperature', 298.15):.2f} K | "
            f"Multiplicity: {job.get('mult', 1)} | "
            f"TBLITE method: {job.get('tblite_method', 'hybrid')}"
        )
        overrides = job.get("config_overrides", {})
        st.caption(
            f"OrbMol version: {overrides.get('ORBMOL_VERSION', DEFAULT_ORBMOL_VERSION)} | "
            f"ALPB solvent: {overrides.get('ALPB_SOLVENT', DEFAULT_ALPB_SOLVENT)} | "
            f"TBLITE accuracy: {overrides.get('TBLITE_ACCURACY', DEFAULT_TBLITE_ACCURACY)}"
        )
        st.caption(
            f"Refine input: {overrides.get('REFINE_INPUT_ON', False)} | "
            f"Pick opt points: {overrides.get('PICK_OPTPOINTS_ON', True)} | "
            f"Save figures: {overrides.get('SAVE_FIG_ON', True)} | "
            f"Initial path method: {overrides.get('INIT_PATH_METHOD', 'DMF')}"
        )

        if job.get("notes"):
            st.caption(job["notes"])

    action_shell = st.columns([1, 4], vertical_alignment="center")
    with action_shell[1]:
        st.caption("ジョブの操作")
        action_cols = st.columns([1, 1, 1, 1, 2])
    if action_cols[0].button(":material/arrow_upward: 上へ", key=f"up_{job['job_id']}", disabled=index == 0 or job["status"] != "queued"):
        order = [item["job_id"] for item in jobs]
        order[index - 1], order[index] = order[index], order[index - 1]
        reorder_queue_for_session(session_id, order)
        st.rerun()
    if action_cols[1].button(":material/arrow_downward: 下へ", key=f"down_{job['job_id']}", disabled=index == len(jobs) - 1 or job["status"] != "queued"):
        order = [item["job_id"] for item in jobs]
        order[index + 1], order[index] = order[index], order[index + 1]
        reorder_queue_for_session(session_id, order)
        st.rerun()
    if action_cols[2].button(":material/stop_circle: 停止", key=f"stop_{job['job_id']}", disabled=job["status"] != "running"):
        stop_result = stop_job(job)
        if stop_result == "signaled":
            st.warning("停止シグナルを送信しました。キューは停止確認後に次のジョブへ進みます。")
        else:
            st.info("このジョブは実行中ではありません。表示を更新します。")
        st.rerun()
    if action_cols[3].button(":material/delete: 削除", key=f"delete_{job['job_id']}", disabled=job["status"] == "running"):
        delete_result = delete_job_from_queue(session_id, job["job_id"])
        if delete_result == "deleted":
            st.warning("ジョブを削除しました。")
        elif delete_result == "deferred":
            st.info("削除を要求しました。ジョブは安全停止後に自動で削除されます。")
        else:
            st.info("このジョブは既に存在しません。")
        st.rerun()

    with action_cols[4]:
        zip_cols = st.columns([0.75, 1.25], vertical_alignment="center")
        flat_job_zip = zip_cols[0].checkbox("flat", key=f"flat_zip_{job['job_id']}")
        archive_path = build_job_archive(session_id, job["job_id"], flat=flat_job_zip)
        zip_cols[1].download_button(
            ":material/folder_zip: ジョブZIP",
            archive_path.read_bytes(),
            file_name=archive_path.name,
            mime="application/zip",
            key=f"zip_{job['job_id']}",
        )

    st.code(" ".join(job.get("command", [])), language="bash")
    if Path(job["stdout_log"]).exists():
        with st.expander("stdout", expanded=job["status"] in {"running", "cancel_requested", "failed"}):
            st.text_area("stdout content", value=tail_text(Path(job["stdout_log"]), max_lines=160), height=300, disabled=True, label_visibility="collapsed", key=f"log_area_{job['job_id']}")
    render_job_results(job)


def render_session_overview(session: dict) -> None:
    jobs = list_jobs(session["session_id"])
    cols = st.columns(4)
    cols[0].metric("Owner", session.get("owner_label", "anonymous"))
    cols[1].metric("Jobs", len(jobs))
    cols[2].metric("Updated", format_app_time(session.get("updated_at")))
    cols[3].metric("Last seen", format_app_time(session.get("last_accessed_at")))
    if session.get("notes"):
        st.caption(session["notes"])

    zip_path = build_session_archive(session["session_id"])
    st.download_button(
        ":material/folder_zip: session ZIP をダウンロード",
        zip_path.read_bytes(),
        file_name=zip_path.name,
        mime="application/zip",
    )


def render_pyscf_profile_editor(label: str, profile: dict, *, prefix: str) -> dict:
    def clean_optional_text(value: str) -> str | None:
        text = value.strip()
        return text or None
    
    def number_default(value, fallback):
        return fallback if value is None else value

    st.markdown(f"### {label}")
    cols = st.columns(3)
    profile["xc"] = cols[0].text_input("XC", value=str(profile.get("xc", "")), key=f"{prefix}_xc")
    profile["basis"] = clean_optional_text(cols[1].text_input("Basis", value="" if profile.get("basis") is None else str(profile.get("basis", "")), key=f"{prefix}_basis"))
    profile["ecp"] = clean_optional_text(cols[2].text_input("ECP", value="" if profile.get("ecp") is None else str(profile.get("ecp", "")), key=f"{prefix}_ecp"))

    cols = st.columns(4)
    profile["with_df"] = cols[0].checkbox("Density fitting", value=bool(profile.get("with_df", False)), key=f"{prefix}_with_df")
    profile["with_solvent"] = cols[1].checkbox("Solvent model", value=bool(profile.get("with_solvent", False)), key=f"{prefix}_with_solvent")
    profile["max_cycle"] = int(cols[2].number_input("Max cycle", min_value=1, value=int(profile.get("max_cycle", 200)), step=1, key=f"{prefix}_max_cycle"))
    profile["verbose"] = int(cols[3].number_input("Verbose", min_value=0, value=int(profile.get("verbose", 4)), step=1, key=f"{prefix}_verbose"))

    cols = st.columns(4)
    profile["auxbasis"] = clean_optional_text(cols[0].text_input("Auxbasis", value="" if profile.get("auxbasis") is None else str(profile.get("auxbasis", "")), key=f"{prefix}_auxbasis", disabled=not profile["with_df"]))
    if not profile["with_df"]:
        profile["auxbasis"] = None
    profile["solvent_model"] = clean_optional_text(cols[1].text_input("Solvent model", value="" if profile.get("solvent_model") is None else str(profile.get("solvent_model", "")), key=f"{prefix}_solvent_model", disabled=not profile["with_solvent"]))
    profile["solvent"] = clean_optional_text(cols[2].text_input("Solvent", value="" if profile.get("solvent") is None else str(profile.get("solvent", "")), key=f"{prefix}_solvent", disabled=not profile["with_solvent"]))
    eps_default = 78.3553 if profile.get("eps") is None else float(profile.get("eps"))
    eps_enabled = cols[3].checkbox("custom eps", value=profile.get("eps") is not None, key=f"{prefix}_eps_enabled", disabled=not profile["with_solvent"])

    cols = st.columns(4)
    conv_tol_default = profile.get("conv_tol")
    conv_enabled = cols[0].checkbox("custom conv_tol", value=conv_tol_default is not None, key=f"{prefix}_conv_tol_enabled")
    profile["conv_tol"] = None
    if conv_enabled:
        profile["conv_tol"] = float(cols[1].number_input("conv_tol", min_value=1e-12, value=float(number_default(conv_tol_default, 1e-8)), step=1e-8, format="%.2e", key=f"{prefix}_conv_tol"))
    else:
        cols[1].caption("conv_tol: default")

    level_shift_default = profile.get("scf_level_shift")
    level_shift_enabled = cols[2].checkbox("レベルシフトを使う", value=level_shift_default is not None, key=f"{prefix}_scf_level_shift_enabled")
    profile["scf_level_shift"] = None
    if level_shift_enabled:
        profile["scf_level_shift"] = float(cols[3].number_input("レベルシフト値", min_value=0.0, value=float(number_default(level_shift_default, 0.1)), step=0.1, format="%.6g", key=f"{prefix}_scf_level_shift"))
    else:
        cols[3].caption("レベルシフト: none")

    cols = st.columns(4)
    disp_default = profile.get("disp")
    disp_enabled = cols[0].checkbox("dispersion keyword", value=disp_default is not None, key=f"{prefix}_disp_enabled")
    profile["disp"] = None
    if disp_enabled:
        profile["disp"] = clean_optional_text(cols[1].text_input("Dispersion", value=str(disp_default or ""), key=f"{prefix}_disp"))
    else:
        cols[1].caption("disp: none")

    cols = st.columns(3)
    grids_enabled = cols[0].checkbox("custom grids", value=profile.get("grids_level") is not None, key=f"{prefix}_grids_enabled")
    if grids_enabled:
        profile["grids_level"] = int(cols[1].number_input("grids level", min_value=0, value=int(profile.get("grids_level", 5)), step=1, key=f"{prefix}_grids_level"))
    else:
        profile["grids_level"] = None
        cols[1].caption("grids: default")
    nlc_enabled = cols[2].checkbox("custom nlc grids", value=profile.get("nlcgrids_level") is not None, key=f"{prefix}_nlcgrids_enabled")

    cols = st.columns(3)
    if nlc_enabled:
        profile["nlcgrids_level"] = int(cols[0].number_input("NLC grids level", min_value=0, value=int(profile.get("nlcgrids_level", 4)), step=1, key=f"{prefix}_nlcgrids_level"))
    else:
        profile["nlcgrids_level"] = None
        cols[0].caption("nlc grids: default")

    if profile["with_solvent"] and eps_enabled:
        profile["eps"] = float(cols[1].number_input("eps", min_value=0.0, value=eps_default, step=0.1, key=f"{prefix}_eps"))
    else:
        profile["eps"] = None
        cols[1].caption("eps: default")

    return {key: value for key, value in profile.items() if value not in ("", None)}


PYSCF_XC_CANDIDATES = [
    "hf",
    "pbe0",
    "b3lyp",
    "m06-x",
    "wb97m-v",
    "wb97x-d",
    "skala-1.1",
    "b973c",
    "r2scan3c",
    "wb97x3c",
    "b97-d3bj/vdzp",
    "r2scan-d4/vdzp",
    "wb97x-d4/vdzp",
    "b3lyp-d4/vdzp",
]


PYSCF_BASIS_CANDIDATES = [
    "sto-3g",
    "6-31g*",
    "6-311++g**",
    "def2-svp",
    "def2-tzvp",
    "def2-tzvpp",
    "def2-tzvpd",
    "ma-def2-svp",
    "ma-def2-tzvp",
    "aug-cc-pvtz",
]


@st.dialog("XC / Basis 候補")
def render_pyscf_candidate_dialog() -> None:
    cols = st.columns(2)
    with cols[0]:
        st.markdown("#### XC")
        st.dataframe(
            pd.DataFrame({"candidate": PYSCF_XC_CANDIDATES}),
            hide_index=True,
            width="stretch",
        )
    with cols[1]:
        st.markdown("#### Basis")
        st.dataframe(
            pd.DataFrame({"candidate": PYSCF_BASIS_CANDIDATES}),
            hide_index=True,
            width="stretch",
        )


PYSCF_PROFILE_WIDGET_FIELDS = [
    "xc",
    "basis",
    "ecp",
    "with_df",
    "auxbasis",
    "with_solvent",
    "solvent_model",
    "solvent",
    "eps",
    "eps_enabled",
    "conv_tol",
    "conv_tol_enabled",
    "scf_level_shift",
    "scf_level_shift_enabled",
    "disp",
    "disp_enabled",
    "grids_level",
    "grids_enabled",
    "nlcgrids_level",
    "nlcgrids_enabled",
    "max_cycle",
    "verbose",
]


def clear_pyscf_profile_state(prefix: str) -> None:
    base_keys = {f"{prefix}_{field}" for field in PYSCF_PROFILE_WIDGET_FIELDS}
    revision_prefix = f"{prefix}_r"
    for key in list(st.session_state):
        if key in base_keys or (
            key.startswith(revision_prefix)
            and any(key.endswith(f"_{field}") for field in PYSCF_PROFILE_WIDGET_FIELDS)
        ):
            st.session_state.pop(key, None)
    st.session_state.pop(f"{prefix}_pending_profile", None)


def queue_pyscf_profile_copy(prefix: str, profile: dict) -> None:
    clear_pyscf_profile_state(prefix)
    st.session_state[f"{prefix}_pending_profile"] = json.loads(json.dumps(profile))
    st.session_state[f"{prefix}_widget_revision"] = int(st.session_state.get(f"{prefix}_widget_revision", 0)) + 1


def profile_with_pending_copy(prefix: str, profile: dict) -> dict:
    pending = st.session_state.pop(f"{prefix}_pending_profile", None)
    if isinstance(pending, dict):
        return pending
    return profile


def pyscf_widget_prefix(prefix: str) -> str:
    revision = int(st.session_state.get(f"{prefix}_widget_revision", 0))
    return f"{prefix}_r{revision}"


def pyscf_profile_from_state(prefix: str, fallback: dict) -> dict:
    def text_value(field: str):
        value = st.session_state.get(f"{prefix}_{field}", fallback.get(field))
        if value in ("", None):
            return None
        return value
    
    def state_number(field: str, fallback_value):
        value = st.session_state.get(f"{prefix}_{field}", fallback.get(field))
        return fallback_value if value is None else value

    profile = {
        "xc": text_value("xc"),
        "basis": text_value("basis"),
        "ecp": text_value("ecp"),
        "with_df": bool(st.session_state.get(f"{prefix}_with_df", fallback.get("with_df", False))),
        "auxbasis": text_value("auxbasis"),
        "max_cycle": int(st.session_state.get(f"{prefix}_max_cycle", fallback.get("max_cycle", 200))),
        "with_solvent": bool(st.session_state.get(f"{prefix}_with_solvent", fallback.get("with_solvent", False))),
        "solvent_model": text_value("solvent_model"),
        "solvent": text_value("solvent"),
        "verbose": int(st.session_state.get(f"{prefix}_verbose", fallback.get("verbose", 4))),
    }
    if not profile["with_df"]:
        profile["auxbasis"] = None
    if st.session_state.get(f"{prefix}_eps_enabled", fallback.get("eps") is not None):
        profile["eps"] = float(state_number("eps", 78.3553))
    if st.session_state.get(f"{prefix}_conv_tol_enabled", fallback.get("conv_tol") is not None):
        profile["conv_tol"] = float(state_number("conv_tol", 1e-8))
    if st.session_state.get(f"{prefix}_scf_level_shift_enabled", fallback.get("scf_level_shift") is not None):
        level_shift = st.session_state.get(f"{prefix}_scf_level_shift", fallback.get("scf_level_shift"))
        profile["scf_level_shift"] = float(0.1 if level_shift is None else level_shift)
    if st.session_state.get(f"{prefix}_disp_enabled", fallback.get("disp") is not None):
        profile["disp"] = text_value("disp")
    if st.session_state.get(f"{prefix}_grids_enabled", fallback.get("grids_level") is not None):
        profile["grids_level"] = int(state_number("grids_level", 5))
    if st.session_state.get(f"{prefix}_nlcgrids_enabled", fallback.get("nlcgrids_level") is not None):
        profile["nlcgrids_level"] = int(state_number("nlcgrids_level", 4))
    return {key: value for key, value in profile.items() if value not in ("", None)}


def render_pyscf_copy_controls(session_id: str, config: dict, presets: dict) -> None:
    labels = {f"preset:{name}": f"preset: {name}" for name in presets}
    labels["profile:pyscf"] = "profile: pyscf"
    labels["profile:pyscf_high"] = "profile: pyscf_high"
    pyscf_prefix = f"{session_id}_pyscf"
    pyscf_high_prefix = f"{session_id}_pyscf_high"
    profiles = {
        "profile:pyscf": pyscf_profile_from_state(pyscf_widget_prefix(pyscf_prefix), dict(config.get("pyscf", {}))),
        "profile:pyscf_high": pyscf_profile_from_state(pyscf_widget_prefix(pyscf_high_prefix), dict(config.get("pyscf_high", {}))),
    }

    cols = st.columns([1.2, 1.2, 1])
    targets = [
        ("pyscf", pyscf_prefix),
        ("pyscf_high", pyscf_high_prefix),
    ]
    for index, (target_name, target_prefix) in enumerate(targets):
        source_options = [key for key in labels if key != f"profile:{target_name}"]
        selected_source = cols[index].selectbox(
            f"{target_name} へ設定をコピー from",
            source_options,
            format_func=lambda value: labels[value],
            key=f"{session_id}_{target_name}_copy_source",
        )
        if cols[index].button(":material/content_copy: copy", key=f"{session_id}_{target_name}_copy_button", width="stretch"):
            source_profile = presets[selected_source.removeprefix("preset:")] if selected_source.startswith("preset:") else profiles[selected_source]
            queue_pyscf_profile_copy(target_prefix, source_profile)
            st.success(f"{labels[selected_source]} を {target_name} に反映しました。保存するには config を保存してください。")
            st.rerun()
    cols[2].caption("copy は画面上の編集値だけを更新します。永続化は保存ボタンで行います。")


def render_session_config(session: dict) -> None:
    st.markdown("## :material/settings: Session config")
    st.caption("PySCFの設定はこのセッションのメタデータに保存されます。これらの値から ジョブローカルのJSONが生成されます。")

    state_version_key = f"{session['session_id']}_pyscf_config_state_version"
    if st.session_state.get(state_version_key) != 3:
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf")
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf_high")
        st.session_state[state_version_key] = 3

    config = json.loads(json.dumps(session.get("pyscf_config", default_pyscf_config())))
    presets = dict(config.get("presets", {}))
    with st.container(border=True):
        if st.button(":material/help: XC / Basis 候補", key=f"{session['session_id']}_pyscf_candidates_help"):
            render_pyscf_candidate_dialog()
        if presets:
            st.divider()
            render_pyscf_copy_controls(session["session_id"], config, presets)
            st.divider()
        pyscf_prefix = f"{session['session_id']}_pyscf"
        pyscf_high_prefix = f"{session['session_id']}_pyscf_high"
        pyscf_profile = profile_with_pending_copy(pyscf_prefix, dict(config.get("pyscf", {})))
        pyscf_high_profile = profile_with_pending_copy(pyscf_high_prefix, dict(config.get("pyscf_high", {})))
        config["pyscf"] = render_pyscf_profile_editor("Profile: pyscf", pyscf_profile, prefix=pyscf_widget_prefix(pyscf_prefix))
        st.divider()
        config["pyscf_high"] = render_pyscf_profile_editor("Profile: pyscf_high", pyscf_high_profile, prefix=pyscf_widget_prefix(pyscf_high_prefix))

        with st.expander("Advanced JSON preview", expanded=False):
            st.code(json.dumps(config, ensure_ascii=False, indent=2), language="json")

        actions = st.columns(3)
        save_pressed = actions[0].button(":material/save: config を保存", type="primary", width="stretch")
        reset_pressed = actions[1].button(":material/restart_alt: デフォルトに戻す", width="stretch")
        reload_pressed = actions[2].button(":material/refresh: session values を再読込", width="stretch")

    if save_pressed:
        updated = dict(session)
        updated["pyscf_config"] = config
        save_session(updated)
        st.success("session PySCF settings を保存しました。")
        st.rerun()
    if reset_pressed:
        updated = dict(session)
        updated["pyscf_config"] = default_pyscf_config()
        save_session(updated)
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf")
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf_high")
        st.success("このセッションの PySCF settings をデフォルトに戻しました。")
        st.rerun()
    if reload_pressed:
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf")
        clear_pyscf_profile_state(f"{session['session_id']}_pyscf_high")
        st.rerun()


def render_job_submission(session: dict) -> None:
    session_id = session["session_id"]
    owner_label = session.get("owner_label", "anonymous")
    sample_cases = list_sample_cases()
    existing_inputs = list_existing_inputs(session_id)
    existing_result_files = list_session_files(session_id, TABLE_EXTENSIONS)
    path_state_keys = path_submit_state_keys(session_id)
    path_was_initialized = path_state_keys["preset"] in st.session_state
    path_reset_applied = bool(st.session_state.pop(submit_reset_pending_key(session_id, PATH_SUBMIT_SECTION), False))
    path_defaults = apply_submit_defaults(
        session,
        PATH_SUBMIT_SECTION,
        PATH_SUBMIT_DEFAULTS,
        path_state_keys,
        force=path_reset_applied,
    )
    if path_reset_applied or not path_was_initialized:
        sync_submit_default_trackers(session_id, str(path_defaults["preset"]))

    st.markdown("## :material/add_circle: New Path Search")
    if path_reset_applied:
        st.success("セッションのデフォルト設定に戻しました。")
    
    st.markdown("#### ワークフローとインプット（ソース）の設定")
    selector_cols = st.columns([1, 1.2])
    workflow_key = f"{session_id}_preset"
    source_key = f"{session_id}_source_mode"
    
    with selector_cols[0]:
        preset = st.selectbox("Workflow preset", list(WORKFLOW_LABELS.keys()), key=workflow_key, format_func=lambda value: WORKFLOW_DISPLAY_LABELS.get(value, value))
        st.caption(WORKFLOW_LABELS[preset]["help"])
        
    mode = WORKFLOW_LABELS[preset]["input_mode"]
    sync_workflow_dependent_state(session_id, preset)
    source_options = ["新たにアップロードする"]
    if mode == "reactant_product" and sample_cases:
        source_options.append("ビルトインサンプルを使う")
    elif mode != "reactant_product" and existing_inputs:
        source_options.append("既存のセッション内ファイルを使う")
        
    current_source = st.session_state.get(source_key)
    if current_source not in source_options:
        st.session_state[source_key] = source_options[0]
        
    with selector_cols[1]:
        if hasattr(st, "segmented_control"):
            source_mode = st.segmented_control("input source", source_options, key=source_key)
        else:
            source_mode = st.radio("input source", source_options, key=source_key, horizontal=True)

    sync_workflow_step_state(session_id, preset, mode)
    step_keys = {name: workflow_step_key(session_id, name) for name in WORKFLOW_STEP_FIELDS}

    reactant_file = product_file = input_file = result_file = None
    sample_case = None
    existing_input = existing_result = None
    path_prefix = f"{session_id}_path"
    refine_input_key = f"{session_id}_refine_input_on"

    if source_mode != "新たにアップロードする":
        st.markdown("**1. Input files (Select existing)**")
        with st.container(border=True):
            if mode == "reactant_product":
                sample_case = st.selectbox("Sample case", sample_cases, key=f"{session_id}_sample")
                if sample_case:
                    ref_path = SAMPLE_INPUT_ROOT / sample_case / "sample_reference.json"
                    if ref_path.exists():
                        ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
                        label = ref_data.get("display_name") or ref_data.get("reaction_label") or sample_case
                        
                        with st.expander(f"Sample info: {label}", expanded=True, icon=":material/info:"):
                            if ref_data.get("description"):
                                st.write(ref_data["description"])

                            status_bits = []
                            if ref_data.get("curation_status"):
                                status_bits.append(f"Curation: `{ref_data['curation_status']}`")
                            if ref_data.get("reference_value_status"):
                                status_bits.append(f"Reference: `{ref_data['reference_value_status']}`")
                            if status_bits:
                                st.caption(" | ".join(status_bits))
                            
                            ref_bits = []
                            if "formula" in ref_data:
                                ref_bits.append(f"Formula: `{ref_data['formula']}`")
                            if "charge" in ref_data:
                                ref_bits.append(f"Charge: `{ref_data['charge']}`")
                            if "multiplicity" in ref_data:
                                ref_bits.append(f"Multiplicity: `{ref_data['multiplicity']}`")
                            if "difficulty" in ref_data:
                                ref_bits.append(f"Difficulty: `{ref_data['difficulty']}`")
                            if "recommended_initial_path_methods" in ref_data:
                                methods = ", ".join(ref_data["recommended_initial_path_methods"])
                                ref_bits.append(f"Recommended path: `{methods}`")
                            
                            if ref_bits:
                                st.markdown(f"**Input references:** {' | '.join(ref_bits)}")

                            if ref_data.get("recommended_use"):
                                st.caption(ref_data["recommended_use"])

                            if ref_data.get("ui_note"):
                                st.info(ref_data["ui_note"])

                            suggested_scan = ref_data.get("suggested_scan") or {}
                            indices_1_based = suggested_scan.get("atom_indices_1_based")
                            if indices_1_based:
                                indices_0_based = [int(index) - 1 for index in indices_1_based]
                                coordinate = suggested_scan.get("coordinate", "scan")
                                st.caption(
                                    "Suggested SCAN: "
                                    f"`{coordinate}` with atom indices "
                                    f"`{','.join(map(str, indices_0_based))}` "
                                    "(0-based; converted from JSON 1-based indices)."
                                )
                            
                            if "notes" in ref_data:
                                for note in ref_data["notes"]:
                                    st.write(f"- {note}")
                            
                            if ref_data.get("reference_values"):
                                df_ref = pd.DataFrame(ref_data["reference_values"])
                                cols_to_show = [c for c in ["quantity", "value", "unit", "method"] if c in df_ref.columns]
                                st.dataframe(df_ref[cols_to_show], hide_index=True)

            elif mode == "single_input":
                labels = [f"{path.relative_to(session_dir(session_id))} ({file_size_label(path)})" for path in existing_inputs]
                if labels:
                    existing_choice = st.selectbox("既存のセッション内ファイルを使う", labels, key=f"{session_id}_existing")
                    existing_input = existing_inputs[labels.index(existing_choice)]
                else:
                    st.caption("このセッションには既存の `.traj` または `.xyz` file がまだありません。")

            else:
                st.caption("figure refresh には trajectory file と再描画対象の CSV の両方が必要です。")
                cols = st.columns(2)
                input_labels = [f"{path.relative_to(session_dir(session_id))} ({file_size_label(path)})" for path in existing_inputs]
                result_labels = [f"{path.relative_to(session_dir(session_id))} ({file_size_label(path)})" for path in existing_result_files]
                if input_labels:
                    existing_input_choice = cols[0].selectbox("既存 trajectory / XYZ", input_labels, key=f"{session_id}_fig_existing_input")
                    existing_input = existing_inputs[input_labels.index(existing_input_choice)]
                else:
                    cols[0].caption("このセッションには既存の `.traj` または `.xyz` file がまだありません。")
                if result_labels:
                    existing_result_choice = cols[1].selectbox("既存 result CSV", result_labels, key=f"{session_id}_fig_existing_csv")
                    existing_result = existing_result_files[result_labels.index(existing_result_choice)]
                else:
                    cols[1].caption("このセッションには既存の `.csv` file がまだありません。")

    if source_mode == "新たにアップロードする":
        st.markdown("**1. Input files (Upload)**")
        with st.container(border=True):
            if mode == "reactant_product":
                is_scan = st.session_state.get(f"{path_prefix}_init_path_method") == "SCAN"
                cols = st.columns(2)
                reactant_file = cols[0].file_uploader("Reactant XYZ (Initial XYZ)", type=["xyz"], key=f"{session_id}_reactant")
                product_file = cols[1].file_uploader("Product XYZ", type=["xyz"], key=f"{session_id}_product", disabled=is_scan)
            elif mode == "single_input":
                input_file = st.file_uploader("Input `.traj` または `.xyz`", type=["traj", "xyz"], key=f"{session_id}_input")
            else:
                st.caption("figure refresh には trajectory file と再描画対象の CSV の両方が必要です。")
                cols = st.columns(2)
                input_file = cols[0].file_uploader("Input `.traj` または `.xyz`", type=["traj", "xyz"], key=f"{session_id}_fig_input")
                result_file = cols[1].file_uploader("既存 result CSV", type=["csv"], key=f"{session_id}_fig_csv")

    st.markdown("**2. Method Settings**")
    with st.container(border=True):
        live_cols = st.columns([1, 2.4])
        with live_cols[0]:
            init_path_method = render_initial_path_live_controls(path_prefix, refine_input_key=refine_input_key)
        with live_cols[1]:
            method, orbmol_version, alpb_solvent, tblite_enabled = render_method_live_controls(session_id)

        preview_scan_indices: list[int] = []
        preview_scan_error: str | None = None
        if mode == "reactant_product":
            try:
                preview_scan_indices = parse_int_list(str(st.session_state.get(f"{path_prefix}_scan_indices_text", "0,1")))
            except ValueError:
                preview_scan_error = "SCAN indices を整数 list として読めません。"
        preview_scan_value, source_scan_error = current_scan_coordinate_from_source(
            source_mode=str(source_mode),
            mode=str(mode),
            sample_case=sample_case,
            reactant_file=reactant_file,
            scan_type=str(st.session_state.get(f"{path_prefix}_scan_type", "bond")),
            scan_indices=preview_scan_indices,
        )
        render_initial_path_selected_settings(
            path_prefix,
            init_path_method,
            current_scan_value=preview_scan_value,
            current_scan_error=preview_scan_error or source_scan_error,
        )
        module_settings = render_module_settings(path_prefix, include_initial_path_method=True)

    with st.form(f"new_job_{session_id}", border=True):
        
        st.markdown("**3. Workflow Steps**")
        if preset == "Figure refresh only":
            do_path = do_ts = do_irc = do_vib = do_refine = False
            st.info("figure refresh は既存 trajectory と CSV からプロットの再描画だけを行います。calculation stage は実行しません。", icon=":material/info:")
        else:
            step_cols = st.columns(5)

            do_path = step_cols[0].checkbox(
                WORKFLOW_STEP_FIELDS["initial_path"],
                key=step_keys["initial_path"],
                disabled=mode != "reactant_product",
            )
            do_ts = step_cols[1].checkbox(
                WORKFLOW_STEP_FIELDS["ts_opt"],
                key=step_keys["ts_opt"],
            )
            do_irc = step_cols[2].checkbox(
                WORKFLOW_STEP_FIELDS["irc"],
                key=step_keys["irc"],
            )
            do_vib = step_cols[3].checkbox(
                WORKFLOW_STEP_FIELDS["vib"],
                key=step_keys["vib"],
            )
            do_refine = step_cols[4].checkbox(
                WORKFLOW_STEP_FIELDS["refine"],
                key=step_keys["refine"],
            )
            if mode != "reactant_product":
                do_path = False

        st.markdown("**4. Calculation Parameters**")
        with st.container(border=True):
            param_cols = st.columns([1, 1.2, 1.2])
            with param_cols[0]:
                charge_key = f"{session_id}_charge"
                mult_key = f"{session_id}_mult"
                temp_key = f"{session_id}_temp"
                charge = st.number_input("Charge", step=1, key=charge_key, **widget_default_kwargs(charge_key, value=0))
                mult = st.number_input("Multiplicity", min_value=1, step=1, key=mult_key, **widget_default_kwargs(mult_key, value=1))
                temp = st.number_input("Temperature [K]", key=temp_key, **widget_default_kwargs(temp_key, value=298.15))
            with param_cols[1]:
                tblite_method = st.selectbox("TBLITE method", ["hybrid", "GFN2-xTB", "GFN1-xTB"], key=f"{session_id}_tblite", disabled=not tblite_enabled)
            with param_cols[2]:
                tblite_accuracy_key = f"{session_id}_tblite_accuracy"
                tblite_accuracy = st.number_input(
                    "TBLITE accuracy",
                    min_value=0.0001,
                    step=0.001,
                    format="%.4f",
                    key=tblite_accuracy_key,
                    disabled=not tblite_enabled,
                    **widget_default_kwargs(tblite_accuracy_key, value=DEFAULT_TBLITE_ACCURACY),
                )
        st.markdown("**5. Output Settings**")
        output_cols = st.columns([1, 2.4])
        with output_cols[0]:
            result_key = f"{session_id}_res"
            result_name = Path(st.text_input("result CSV name", key=result_key, **widget_default_kwargs(result_key, value="result.csv"))).name
        with output_cols[1]:
            note = st.text_input("job note", value="", key=f"{session_id}_note")

        st.markdown("**6. Extra Workflow Options**")
        extra_cols = st.columns(3)
        if refine_input_key not in st.session_state:
            st.session_state[refine_input_key] = mode == "reactant_product" and init_path_method != "SCAN"
        refine_input_applicable = (
            mode == "reactant_product"
            and bool(do_path)
            and str(module_settings["init_path_method"]).upper() in {"DMF", "NEB"}
        )
        if not refine_input_applicable:
            st.session_state[refine_input_key] = False
        refine_input_on = extra_cols[0].checkbox(
            "初期構造の最適化",
            key=refine_input_key,
            disabled=not refine_input_applicable,
        )
        pick_optpoints_key = f"{session_id}_pick_optpoints"
        save_fig_key = f"{session_id}_savefig"
        pick_optpoints_on = extra_cols[1].checkbox(
            "構造最適化点（optpoints）を抽出",
            key=pick_optpoints_key,
            **widget_default_kwargs(pick_optpoints_key, value=True),
        )
        save_fig_on = extra_cols[2].checkbox(
            "図を保存",
            key=save_fig_key,
            **widget_default_kwargs(save_fig_key, value=True),
        )

        st.space("small")
        with st.container(horizontal=True, horizontal_alignment="right"):
            reset_defaults_pressed = st.form_submit_button("リセット", icon=":material/restart_alt:")
            save_defaults_pressed = st.form_submit_button("セッションのデフォルトにする", icon=":material/save:")
            preview_pressed = st.form_submit_button("ワークフロー構造をプレビュー", icon=":material/account_tree:")
            submitted = st.form_submit_button("ジョブをキューに追加", type="primary", icon=":material/queue:")

    if reset_defaults_pressed:
        st.session_state[submit_reset_pending_key(session_id, PATH_SUBMIT_SECTION)] = True
        st.rerun()

    if save_defaults_pressed:
        save_submit_defaults(
            session,
            PATH_SUBMIT_SECTION,
            {
                "preset": preset,
                **module_settings,
                "method": st.session_state.get(f"{session_id}_method", "orbmol"),
                "custom": st.session_state.get(f"{session_id}_custom", method),
                "orbmol_version": orbmol_version,
                "alpb_solvent": alpb_solvent,
                "tblite": tblite_method,
                "tblite_accuracy": tblite_accuracy,
                **{f"workflow_step_{name}": bool(st.session_state.get(key)) for name, key in step_keys.items()},
                "charge": int(charge),
                "mult": int(mult),
                "temp": float(temp),
                "result_name": result_name,
                "refine_input_on": bool(refine_input_on),
                "pick_optpoints_on": bool(pick_optpoints_on),
                "save_fig_on": bool(save_fig_on),
            },
        )
        st.success("この設定をセッションのデフォルトとして保存しました。")
        st.rerun()

    if preview_pressed:
        render_workflow_preview_dialog(
            preset=preset,
            mode=mode,
            source_mode=source_mode,
            init_path_method=str(module_settings["init_path_method"]),
            do_path=bool(do_path),
            do_ts=bool(do_ts),
            do_irc=bool(do_irc),
            do_vib=bool(do_vib),
            do_refine=bool(do_refine),
            refine_input_on=bool(refine_input_on),
            pick_optpoints_on=bool(pick_optpoints_on),
            save_fig_on=bool(save_fig_on),
        )
        return

    if not submitted:
        return

    errors: list[str] = []
    if not method:
        errors.append("Method は必須です。")
    if not result_name.endswith(".csv"):
        errors.append("result CSV name は `.csv` で終わる必要があります。")
    effective_method = "orbmol+alpb" if method == "orbmol" and alpb_solvent != "None" else method
    if alpb_solvent != "None" and method != "orbmol":
        errors.append("Add ALPB solvent is only available when Method is 'orbmol'. Select 'None' or choose 'orbmol'.")
    try:
        scan_indices = parse_int_list(str(module_settings["scan_indices_text"]))
    except ValueError:
        errors.append("SCAN indices は comma 区切りの整数 list で指定してください。")
        scan_indices = []
    if module_settings["init_path_method"] == "SCAN" and scan_indices:
        scan_spec = scan_type_spec(str(module_settings["scan_type"]))
        expected_count = int(scan_spec["count"])
        if len(scan_indices) != expected_count:
            errors.append(f"{module_settings['scan_type']} SCAN では atom indices を {expected_count} 個指定してください。")
    current_scan_value, current_scan_error = current_scan_coordinate_from_source(
        source_mode=str(source_mode),
        mode=str(mode),
        sample_case=sample_case,
        reactant_file=reactant_file,
        scan_type=str(module_settings["scan_type"]),
        scan_indices=scan_indices,
    )
    resolved_scan_settings, scan_resolution_errors, scan_resolution_notes = resolve_scan_settings(module_settings, current_scan_value)
    if module_settings["init_path_method"] == "SCAN":
        if current_scan_error and str(module_settings.get("scan_range_mode")) in {"relative_forward", "relative_window"}:
            errors.append(f"SCAN current value を読めません: {current_scan_error}")
        errors.extend(scan_resolution_errors)
    try:
        fixed_atoms = parse_int_list(str(module_settings["fixed_atoms_text"]))
    except ValueError:
        errors.append("Fixed atoms は comma 区切りの整数 list で指定してください。")
        fixed_atoms = []
    if not any([do_path, do_ts, do_irc, do_vib, do_refine]) and preset != "Figure refresh only":
        errors.append("少なくとも 1 つの workflow step を選択してください。")
    is_scan = module_settings["init_path_method"] == "SCAN"
    if mode == "reactant_product":
        if not do_path:
            errors.append("reactant / product workflow では Initial Path を有効にしてください。")
        if source_mode == "新たにアップロードする":
            if reactant_file is None:
                errors.append("Reactant file (Initial XYZ) は必須です。")
            elif product_file is None and not is_scan:
                errors.append("Product file のアップロードが必要です（SCANモード以外）。")
        else:
            reactant_path, product_path = sample_case_files(sample_case or "")
            if reactant_path is None or (product_path is None and not is_scan):
                errors.append("選択した sample case が不完全です。")
    elif mode == "single_input":
        if source_mode == "新たにアップロードする" and input_file is None:
            errors.append("input trajectory または XYZ file が必要です。")
        if source_mode == "既存のセッション内ファイルを使う" and existing_input is None:
            errors.append("このセッションから既存の `.traj` または `.xyz` file を選択してください。")
    else:
        if source_mode == "新たにアップロードする":
            if input_file is None:
                errors.append("input trajectory または XYZ file が必要です。")
            if result_file is None:
                errors.append("figure refresh には既存 result CSV が必要です。")
        else:
            if existing_input is None:
                errors.append("このセッションから既存の `.traj` または `.xyz` file を選択してください。")
            if existing_result is None:
                errors.append("このセッションから既存の `.csv` file を選択してください。")

    if errors:
        for message in errors:
            st.error(message)
        return
    for message in scan_resolution_notes:
        st.info(message, icon=":material/info:")

    job = create_job(session_id=session_id, owner_label=owner_label, workflow=preset)
    job["charge"] = int(charge)
    job["method"] = effective_method
    job["result_name"] = result_name
    job["notes"] = note
    job["script_name"] = workflow_script_name(preset)
    
    job["workflow_steps"] = {
        "initial_path": do_path,
        "ts_opt": do_ts,
        "irc": do_irc,
        "vib": do_vib,
        "refine": do_refine
    }
    job["temperature"] = float(temp)
    job["mult"] = int(mult)
    job["tblite_method"] = tblite_method
    job["config_overrides"] = {
        "ORBMOL_VERSION": str(orbmol_version),
        "ALPB_SOLVENT": str(alpb_solvent),
        "TBLITE_ACCURACY": float(tblite_accuracy),
        "INIT_PATH_METHOD": module_settings["init_path_method"],
        "REFINE_INPUT_ON": bool(refine_input_on),
        "PICK_OPTPOINTS_ON": bool(pick_optpoints_on),
        "SAVE_FIG_ON": bool(save_fig_on),
        "MULT": int(mult),
        "NMOVE": int(module_settings["nmove"]),
        "UPDATE_TEVAL": bool(module_settings["update_teval"]),
        "DMF_CONVERGENCE": str(module_settings["dmf_convergence"]),
        "NEB_IMAGES": int(module_settings["neb_images"]),
        "NEB_SPRING_CONSTANT": float(module_settings["neb_spring_constant"]),
        "NEB_CLIMB": bool(module_settings["neb_climb"]),
        "SCAN_TYPE": str(module_settings["scan_type"]),
        "SCAN_INDICES": scan_indices,
        "SCAN_START_VAL": resolved_scan_settings["scan_start_val"],
        "SCAN_END_VAL": float(resolved_scan_settings["scan_end_val"]),
        "SCAN_STEPS": int(resolved_scan_settings["scan_steps"]),
        "USE_SELLA_IN_OPT": bool(module_settings["use_sella_in_opt"]),
        "SELLA_INTERNAL_AUTO": bool(module_settings["sella_internal_auto"]),
        "SELLA_INTERNAL": bool(module_settings["sella_internal"]),
        "IRC_DX_INIT": float(module_settings["irc_dx_init"]),
        "IRC_DX_MAX": float(module_settings["irc_dx_max"]),
        "IRC_DX_MIN": float(module_settings["irc_dx_min"]),
        "OPT_FMAX": float(module_settings["opt_fmax"]),
        "TSOPT_FMAX": float(module_settings["tsopt_fmax"]),
        "REFINE_CALC_TYPE": str(module_settings["refine_calc_type"]),
        "OPT_OPTPOINTS_AGAIN_ON": bool(module_settings["opt_optpoints_again_on"]),
        "FIXED_ATOMS": fixed_atoms,
    }

    job_root = job_dir(session_id, job["job_id"])
    input_root = job_root / "inputs"
    output_dir = job_root / "run_output"
    pyscf_config_path = job_root / "pyscf_config.json"
    pyscf_config_path.write_text(json.dumps(session.get("pyscf_config", default_pyscf_config()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    job["config_overrides"]["PYSCF_CONFIG_FILE"] = str(pyscf_config_path)

    reactant_path = product_path = input_path = result_path = None
    manifest_inputs: list[dict[str, str]] = []
    if mode == "reactant_product":
        if source_mode == "新たにアップロードする":
            reactant_path = write_uploaded_file(reactant_file, input_root, reactant_file.name or "reactant.xyz")
            manifest_inputs.append(manifest_input(
                role="reactant",
                original_name=reactant_file.name or "reactant.xyz",
                stored_path=reactant_path,
                job_root=job_root,
            ))
            if product_file is not None:
                product_path = write_uploaded_file(product_file, input_root, product_file.name or "product.xyz")
                manifest_inputs.append(manifest_input(
                    role="product",
                    original_name=product_file.name or "product.xyz",
                    stored_path=product_path,
                    job_root=job_root,
                ))
        else:
            sample_reactant, sample_product = sample_case_files(sample_case or "")
            reactant_path = copy_source_file(sample_reactant, input_root, "reactant.xyz")
            manifest_inputs.append(manifest_input(
                role="reactant",
                original_name=sample_reactant.name,
                stored_path=reactant_path,
                job_root=job_root,
            ))
            if sample_product is not None:
                product_path = copy_source_file(sample_product, input_root, "product.xyz")
                manifest_inputs.append(manifest_input(
                    role="product",
                    original_name=sample_product.name,
                    stored_path=product_path,
                    job_root=job_root,
                ))
        
        job["inputs"] = [str(reactant_path)]
        if product_path:
            job["inputs"].append(str(product_path))
    elif mode == "single_input":
        if source_mode == "新たにアップロードする":
            input_path = write_uploaded_file(input_file, input_root, input_file.name or "input.traj")
            original_input_name = input_file.name or "input.traj"
        else:
            input_path = copy_source_file(existing_input, input_root, existing_input.name)
            original_input_name = existing_input.name
        manifest_inputs.append(manifest_input(
            role="input",
            original_name=original_input_name,
            stored_path=input_path,
            job_root=job_root,
        ))
        job["inputs"] = [str(input_path)]
    else:
        if source_mode == "新たにアップロードする":
            input_path = write_uploaded_file(input_file, input_root, input_file.name or "input.traj")
            result_path = write_uploaded_file(result_file, output_dir, result_name)
            original_input_name = input_file.name or "input.traj"
        else:
            input_path = copy_source_file(existing_input, input_root, existing_input.name)
            result_path = copy_source_file(existing_result, output_dir, result_name)
            original_input_name = existing_input.name
        manifest_inputs.append(manifest_input(
            role="input",
            original_name=original_input_name,
            stored_path=input_path,
            job_root=job_root,
        ))
        job["inputs"] = [str(input_path), str(result_path)]

    stdout_log = job_root / "stdout.log"
    job["output_dir"] = str(output_dir)
    job["stdout_log"] = str(stdout_log)
    job["command"] = build_command(
        workflow=preset,
        script_name=job["script_name"],
        output_dir=output_dir,
        charge=job["charge"],
        method=job["method"],
        result_name=job["result_name"],
        reactant_path=reactant_path,
        product_path=product_path,
        input_path=input_path,
        workflow_steps=job["workflow_steps"],
        temperature=job["temperature"],
        tblite_method=job["tblite_method"],
        config_overrides=job["config_overrides"],
    )
    manifest_path = write_job_manifest(
        job,
        submission_source="gui",
        config_original_name=None,
        config_stored_path=job_root / "submitted_config.json",
        inputs=manifest_inputs,
    )
    job["manifest_path"] = str(manifest_path)
    save_job(job)
    enqueue_job(job)
    ensure_worker_running()
    st.success(f"ジョブ `{job['job_id']}` をキューに追加しました。")


def render_concat_submission(session: dict) -> None:
    session_id = session["session_id"]
    owner_label = session.get("owner_label", "anonymous")
    existing_inputs = list_existing_inputs(session_id)
    concat_state_keys = concat_submit_state_keys(session_id)
    concat_reset_applied = bool(st.session_state.pop(submit_reset_pending_key(session_id, CONCAT_SUBMIT_SECTION), False))
    apply_submit_defaults(
        session,
        CONCAT_SUBMIT_SECTION,
        CONCAT_SUBMIT_DEFAULTS,
        concat_state_keys,
        force=concat_reset_applied,
    )

    st.markdown("## :material/library_add: New Concatenation")
    if concat_reset_applied:
        st.success("セッションのデフォルト設定に戻しました。")
    st.caption("複数の `.xyz` または `.traj` files を 1 つの trajectory に連結し、必要に応じて全 frame に対する batch processing (optimization, vibrational analysis, energy refinement) を順次実行します。")

    st.markdown("#### インプット（ソース）の設定")
    source_key = f"{session_id}_cat_source_mode"
    source_options = ["ファイルをアップロード"]
    if existing_inputs:
        source_options.append("既存のセッション内ファイルを選択")

    current_source = st.session_state.get(source_key)
    if current_source not in source_options:
        st.session_state[source_key] = source_options[0]

    if hasattr(st, "segmented_control"):
        source_mode = st.segmented_control("input source", source_options, key=source_key)
    else:
        source_mode = st.radio("input source", source_options, key=source_key, horizontal=True)

    method, orbmol_version, alpb_solvent, tblite_enabled = render_method_live_controls(f"{session_id}_cat")

    with st.form(f"new_concat_job_{session_id}", border=True):
        st.markdown("**1. Input files**")

        uploaded_files = None
        selected_existing = []

        if source_mode == "ファイルをアップロード":
            uploaded_files = st.file_uploader("`.traj` または `.xyz` files をアップロード", type=["traj", "xyz"], accept_multiple_files=True, key=f"{session_id}_cat_upload")
        else:
            labels = [f"{path.relative_to(session_dir(session_id))} ({file_size_label(path)})" for path in existing_inputs]
            if labels:
                selected_labels = st.multiselect("既存のファイルを選択 (順序を反映します)", labels, key=f"{session_id}_cat_existing")
                for label in selected_labels:
                    selected_existing.append(existing_inputs[labels.index(label)])
            else:
                st.caption("このセッションには既存の `.traj` または `.xyz` file がまだありません。")
                
        st.markdown("**2. Batch processing steps (optional)**")
        step_cols = st.columns(3)
        do_opt_key = f"{session_id}_cat_do_opt"
        do_vib_key = f"{session_id}_cat_do_vib"
        do_refine_key = f"{session_id}_cat_do_refine"
        do_opt = step_cols[0].checkbox("structure optimization", key=do_opt_key, **widget_default_kwargs(do_opt_key, value=False))
        do_vib = step_cols[1].checkbox("Vib & Thermo", key=do_vib_key, **widget_default_kwargs(do_vib_key, value=False))
        do_refine = step_cols[2].checkbox("Energy Refine", key=do_refine_key, **widget_default_kwargs(do_refine_key, value=False))
        
        st.markdown("**3. Parameters**")
        with st.container(border=True):
            param_cols = st.columns([1, 1.2, 1.2])
            with param_cols[0]:
                charge_key = f"{session_id}_cat_charge"
                mult_key = f"{session_id}_cat_mult"
                temp_key = f"{session_id}_cat_temp"
                charge = st.number_input("Charge", step=1, key=charge_key, **widget_default_kwargs(charge_key, value=0))
                mult = st.number_input("Multiplicity", min_value=1, step=1, key=mult_key, **widget_default_kwargs(mult_key, value=1))
                temp = st.number_input("Temperature [K]", key=temp_key, **widget_default_kwargs(temp_key, value=298.15))
            with param_cols[1]:
                tblite_method = st.selectbox("TBLITE method", ["hybrid", "GFN2-xTB", "GFN1-xTB"], key=f"{session_id}_cat_tblite", disabled=not tblite_enabled)
            with param_cols[2]:
                tblite_accuracy_key = f"{session_id}_cat_tblite_accuracy"
                tblite_accuracy = st.number_input(
                    "TBLITE accuracy",
                    min_value=0.0001,
                    step=0.001,
                    format="%.4f",
                    key=tblite_accuracy_key,
                    disabled=not tblite_enabled,
                    **widget_default_kwargs(tblite_accuracy_key, value=DEFAULT_TBLITE_ACCURACY),
                )
        output_cols = st.columns([1, 2.4])
        with output_cols[0]:
            result_key = f"{session_id}_cat_res"
            result_name = Path(st.text_input("result CSV name", key=result_key, **widget_default_kwargs(result_key, value="result.csv"))).name
        with output_cols[1]:
            note = st.text_input("job note", value="", key=f"{session_id}_cat_note")

        st.markdown("**4. Extra workflow flags**")
        extra_cols = st.columns(3)
        save_fig_key = f"{session_id}_cat_savefig"
        pick_optpoints_key = f"{session_id}_cat_pick_optpoints"
        save_fig_on = extra_cols[0].checkbox(
            "図を保存",
            key=save_fig_key,
            **widget_default_kwargs(save_fig_key, value=True),
        )
        pick_optpoints_on = extra_cols[1].checkbox(
            "構造最適化点（optpoints）を抽出",
            key=pick_optpoints_key,
            **widget_default_kwargs(pick_optpoints_key, value=False),
        )
        module_settings = render_module_settings(f"{session_id}_cat", include_initial_path_method=False)

        st.space("small")
        with st.container(horizontal=True, horizontal_alignment="right"):
            reset_defaults_pressed = st.form_submit_button("リセット", icon=":material/restart_alt:")
            save_defaults_pressed = st.form_submit_button("セッションのデフォルトにする", icon=":material/save:")
            preview_pressed = st.form_submit_button("ワークフロー構造をプレビュー", icon=":material/account_tree:")
            submitted = st.form_submit_button("ジョブをキューに追加", type="primary", icon=":material/queue:")

    if reset_defaults_pressed:
        st.session_state[submit_reset_pending_key(session_id, CONCAT_SUBMIT_SECTION)] = True
        st.rerun()

    if save_defaults_pressed:
        save_submit_defaults(
            session,
            CONCAT_SUBMIT_SECTION,
            {
                **module_settings,
                "method": st.session_state.get(f"{session_id}_cat_method", "orbmol"),
                "custom": st.session_state.get(f"{session_id}_cat_custom", method),
                "orbmol_version": orbmol_version,
                "alpb_solvent": alpb_solvent,
                "tblite": tblite_method,
                "tblite_accuracy": tblite_accuracy,
                "do_opt": bool(do_opt),
                "do_vib": bool(do_vib),
                "do_refine": bool(do_refine),
                "charge": int(charge),
                "mult": int(mult),
                "temp": float(temp),
                "result_name": result_name,
                "save_fig_on": bool(save_fig_on),
                "pick_optpoints_on": bool(pick_optpoints_on),
            },
        )
        st.success("この設定をセッションのデフォルトとして保存しました。")
        st.rerun()

    if preview_pressed:
        render_concat_workflow_preview_dialog(
            source_mode=source_mode,
            do_opt=bool(do_opt),
            do_vib=bool(do_vib),
            do_refine=bool(do_refine),
            pick_optpoints_on=bool(pick_optpoints_on),
            save_fig_on=bool(save_fig_on),
        )
        return

    if not submitted:
        return

    errors: list[str] = []
    if not method:
        errors.append("Method は必須です。")
    if not result_name.endswith(".csv"):
        errors.append("result CSV name は `.csv` で終わる必要があります。")
    effective_method = "orbmol+alpb" if method == "orbmol" and alpb_solvent != "None" else method
    if alpb_solvent != "None" and method != "orbmol":
        errors.append("Add ALPB solvent is only available when Method is 'orbmol'. Select 'None' or choose 'orbmol'.")
    try:
        fixed_atoms = parse_int_list(str(module_settings["fixed_atoms_text"]))
    except ValueError:
        errors.append("Fixed atoms は comma 区切りの整数リストで指定してください。")
        fixed_atoms = []
    
    if source_mode == "ファイルをアップロード":
        if not uploaded_files:
            errors.append("少なくとも 1 つのファイルをアップロードしてください。")
    else:
        if not selected_existing:
            errors.append("少なくとも 1 つの既存ファイルを選択してください。")

    if errors:
        for message in errors:
            st.error(message)
        return

    if errors:
        for message in errors:
            st.error(message)
        return

    job = create_job(session_id=session_id, owner_label=owner_label, workflow="Concatenation & Batch")
    job["charge"] = int(charge)
    job["method"] = effective_method
    job["result_name"] = result_name
    job["notes"] = note
    job["script_name"] = "molscout.py"
    
    job["workflow_steps"] = {
        "initial_path": False,
        "ts_opt": False,
        "irc": False,
        "vib": do_vib,
        "refine": do_refine
    }
    job["temperature"] = float(temp)
    job["mult"] = int(mult)
    job["tblite_method"] = tblite_method
    job_root = job_dir(session_id, job["job_id"])
    input_root = job_root / "inputs"
    output_dir = job_root / "run_output"
    pyscf_config_path = job_root / "pyscf_config.json"
    pyscf_config_path.write_text(json.dumps(session.get("pyscf_config", default_pyscf_config()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cat_paths = []
    manifest_inputs: list[dict[str, str]] = []
    if source_mode == "ファイルをアップロード":
        for index, uf in enumerate(uploaded_files, start=1):
            saved_path = write_uploaded_file(uf, input_root, uf.name)
            cat_paths.append(saved_path)
            manifest_inputs.append(manifest_input(
                role=f"input_{index:03d}",
                original_name=uf.name,
                stored_path=saved_path,
                job_root=job_root,
            ))
    else:
        for index, ef in enumerate(selected_existing, start=1):
            saved_path = copy_source_file(ef, input_root, ef.name)
            cat_paths.append(saved_path)
            manifest_inputs.append(manifest_input(
                role=f"input_{index:03d}",
                original_name=ef.name,
                stored_path=saved_path,
                job_root=job_root,
            ))
            
    job["inputs"] = [str(p) for p in cat_paths]

    job["config_overrides"] = {
        "ORBMOL_VERSION": str(orbmol_version),
        "ALPB_SOLVENT": str(alpb_solvent),
        "TBLITE_ACCURACY": float(tblite_accuracy),
        "INIT_PATH_METHOD": "CAT",
        "CONCAT_FILES": [path.name for path in cat_paths],
        "REFINE_INPUT_ON": bool(do_opt),
        "PICK_OPTPOINTS_ON": bool(pick_optpoints_on),
        "SAVE_FIG_ON": bool(save_fig_on),
        "MULT": int(mult),
        "USE_SELLA_IN_OPT": bool(module_settings["use_sella_in_opt"]),
        "SELLA_INTERNAL_AUTO": bool(module_settings["sella_internal_auto"]),
        "SELLA_INTERNAL": bool(module_settings["sella_internal"]),
        "IRC_DX_INIT": float(module_settings["irc_dx_init"]),
        "IRC_DX_MAX": float(module_settings["irc_dx_max"]),
        "IRC_DX_MIN": float(module_settings["irc_dx_min"]),
        "OPT_FMAX": float(module_settings["opt_fmax"]),
        "TSOPT_FMAX": float(module_settings["tsopt_fmax"]),
        "REFINE_CALC_TYPE": str(module_settings["refine_calc_type"]),
        "OPT_OPTPOINTS_AGAIN_ON": bool(module_settings["opt_optpoints_again_on"]),
        "FIXED_ATOMS": fixed_atoms,
        "PYSCF_CONFIG_FILE": str(pyscf_config_path),
    }

    stdout_log = job_root / "stdout.log"
    job["output_dir"] = str(output_dir)
    job["stdout_log"] = str(stdout_log)

    # For CAT mode, we bypass adding --input or --reactant since CONCAT_FILES controls the inputs
    command = build_command(
        workflow="Full workflow", # Use standard flags setup without reactant/product requirements
        script_name=job["script_name"],
        output_dir=output_dir,
        charge=job["charge"],
        method=job["method"],
        result_name=job["result_name"],
        reactant_path=None,
        product_path=None,
        input_path=None,
        cat_paths=cat_paths,
        workflow_steps=job["workflow_steps"],
        temperature=job["temperature"],
        tblite_method=job["tblite_method"],
        config_overrides=job["config_overrides"],
    )

    job["command"] = command
    manifest_path = write_job_manifest(
        job,
        submission_source="gui",
        config_original_name=None,
        config_stored_path=job_root / "submitted_config.json",
        inputs=manifest_inputs,
    )
    job["manifest_path"] = str(manifest_path)

    save_job(job)
    enqueue_job(job)
    ensure_worker_running()
    st.success(f"ジョブ `{job['job_id']}` をキューに追加しました。")


def render_session_jobs(session: dict) -> None:
    header_cols = st.columns([4, 1])
    with header_cols[0]:
        st.markdown("## :material/view_list: Session jobs")
        
    jobs = list_jobs(session["session_id"])
    refreshed_jobs: dict[str, dict] = {}
    
    with header_cols[1]:
        if st.button(":material/refresh: jobs を更新", width="stretch"):
            refreshed_jobs = refresh_jobs(session["session_id"], jobs)

    if not jobs:
        st.info("このセッションにはまだジョブがありません。")
        return

    jobs = [refreshed_jobs.get(job["job_id"], job) for job in jobs]

    df = pd.DataFrame(jobs)
    for col in ["job_id", "workflow", "status", "created_at", "notes"]:
        if col not in df.columns:
            df[col] = ""

    display_df = df[["job_id", "workflow", "status", "created_at", "notes"]].copy()
    display_df["created_at"] = display_df["created_at"].map(format_app_time)

    st.markdown("### 詳細を見るジョブを選択")

    job_id_signature = hashlib.sha1(
        "|".join(display_df["job_id"].astype(str).tolist()).encode("utf-8")
    ).hexdigest()[:12]
    selection_key = f"{session['session_id']}_job_selection_{job_id_signature}"
    selection_event = st.dataframe(
        display_df,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key=selection_key,
        height=250,
        column_config={
            "job_id": "Job ID",
            "workflow": "Workflow",
            "status": "Status",
            "created_at": "Created",
            "notes": "Job Note",
        }
    )

    raw_selected_indices = selection_event.selection.rows
    selected_indices = [
        index
        for index in raw_selected_indices
        if isinstance(index, int) and 0 <= index < len(display_df)
    ]

    if not selected_indices:
        st.info("👆 詳細を見るには、上のテーブルで 1 件以上のジョブを選択してください。")
        return

    selected_job_ids = display_df.iloc[selected_indices]["job_id"].tolist()

    st.markdown("### Download")
    download_cols = st.columns([1, 1.2, 1.8, 1.8], vertical_alignment="center")
    flat_selected_zip = download_cols[0].checkbox("flat ZIP", key=f"{session['session_id']}_flat_selected_zip", value=True)
    include_merged_csv = download_cols[1].checkbox("集計CSVを追加", key=f"{session['session_id']}_include_merged_csv", value=True)
    selected_job_ids_tuple = tuple(selected_job_ids)
    selected_job_signature = tuple(
        (
            job["job_id"],
            str(job.get("status", "")),
            str(job.get("updated_at") or job.get("finished_at") or job.get("created_at", "")),
        )
        for job in jobs
        if job["job_id"] in selected_job_ids_tuple
    )
    archive_state_key = (
        f"{session['session_id']}_selected_archive_"
        f"{'-'.join(selected_job_ids_tuple)}_{int(flat_selected_zip)}_{int(include_merged_csv)}"
    )
    merged_state_key = (
        f"{session['session_id']}_merged_archive_"
        f"{'-'.join(selected_job_ids_tuple)}_{int(flat_selected_zip)}"
    )

    if download_cols[2].button(
        ":material/folder_zip: 選択ジョブZIPを生成",
        key=f"{session['session_id']}_generate_selected_jobs_zip",
        width="stretch",
    ):
        with st.spinner("選択したジョブのZIPを作成中..."):
            archive_path = cached_selected_jobs_archive(
                session["session_id"],
                selected_job_ids_tuple,
                flat_selected_zip,
                include_merged_csv,
                selected_job_signature,
            )
        st.session_state[archive_state_key] = archive_path
        st.success("選択したジョブのZIPを作成しました。")

    if download_cols[3].button(
        ":material/table: 集計CSV ZIPを生成",
        key=f"{session['session_id']}_generate_merged_csv_zip",
        width="stretch",
    ):
        with st.spinner("集計CSV ZIPを作成中..."):
            merged_archive_path = cached_merged_csv_archive(
                session["session_id"],
                selected_job_ids_tuple,
                flat_selected_zip,
                selected_job_signature,
            )
        if merged_archive_path:
            st.session_state[merged_state_key] = merged_archive_path
            st.success("集計CSV ZIPを作成しました。")
        else:
            st.session_state.pop(merged_state_key, None)
            st.info("一致する CSV file name がありません。")

    selected_archive_raw = st.session_state.get(archive_state_key)
    selected_archive_path = Path(selected_archive_raw) if selected_archive_raw else None
    if selected_archive_path and selected_archive_path.exists():
        st.download_button(
            ":material/download: 選択したジョブのZIPをダウンロード",
            selected_archive_path.read_bytes(),
            file_name=selected_archive_path.name,
            mime="application/zip",
            key=f"{session['session_id']}_selected_jobs_zip_{archive_state_key}",
        )

    merged_archive_raw = st.session_state.get(merged_state_key)
    merged_archive_path = Path(merged_archive_raw) if merged_archive_raw else None
    if merged_archive_path and merged_archive_path.exists():
        st.download_button(
            ":material/download: 集計CSV ZIPをダウンロード",
            merged_archive_path.read_bytes(),
            file_name=merged_archive_path.name,
            mime="application/zip",
            key=f"{session['session_id']}_merged_csv_zip_{merged_state_key}",
        )

    st.divider()
    st.markdown("### Job details")

    if len(selected_job_ids) > 1:
        target_job_id = st.selectbox(
            "確認するジョブを選択:",
            options=selected_job_ids,
            key=f"{session['session_id']}_target_job_dropdown"
        )
    else:
        target_job_id = selected_job_ids[0]
        st.caption(f"表示中: **{target_job_id}**")

    target_job = next(job for job in jobs if job["job_id"] == target_job_id)
    target_index = jobs.index(target_job)

    previous_key = f"{session['session_id']}_previous_job_id"
    previous_job_id = st.session_state.get(previous_key)
    st.session_state[previous_key] = target_job_id

    if target_job_id != previous_job_id and target_job.get("status") in REFRESHABLE_JOB_STATUSES:
        refreshed = reload_job(session["session_id"], target_job_id)
        if refreshed:
            target_job = refreshed
            jobs[target_index] = refreshed

    st.markdown(f"#### :material/visibility: {target_job['job_id']} | {target_job['workflow']} | {target_job['status']}")
    render_job_detail(session["session_id"], target_job, jobs, target_index)


def render_job_results(job: dict) -> None:
    run_dir = Path(job["output_dir"])
    files = all_result_files(run_dir)
    job_root, job_json_files = all_job_json_files(job)
    if not files and not job_json_files:
        st.info("出力はまだありません。")
        return

    st.caption(f"result folder: `{run_dir}`")
    result_view = section_switch(
        "result view",
        ["概要", "Logs", "Json", "XYZ", "Tables", "Images"],
        key=f"result_view_{job['job_id']}",
        captions={
            "概要": "出力フォルダー概要とメインログ",
            "Logs": ".log files",
            "Json": ".json files",
            "XYZ": ".xyz files",
            "Tables": "反応プロファイルと導出データのCSVプレビュー",
            "Images": "保存済みフィギュアとプロット",
        },
    )

    if result_view == "概要":
        rows = [{"path": str(path.relative_to(run_dir)), "size": file_size_label(path)} for path in files]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, height=220)
            render_molscout_log_expander(files, run_dir)
        else:
            st.info("Calculation outputs are not available yet. Job JSON files can be viewed in the Json tab.")
    elif result_view == "Logs":
        log_files = [path for path in files if path.suffix.lower() in LOG_EXTENSIONS]
        if log_files:
            labels = [str(path.relative_to(run_dir)) for path in log_files]
            selected = log_files[labels.index(st.selectbox("Log file", labels, key=f"log_{job['job_id']}"))]
            st.code(tail_text(selected, max_lines=280) or "(empty file)", language="text")
        else:
            st.info(".log file はありません。")
    elif result_view == "Json":
        if job_json_files:
            labels = [str(path.relative_to(job_root)) for path in job_json_files]
            selected = job_json_files[
                labels.index(st.selectbox("JSON file", labels, key=f"json_{job['job_id']}"))
            ]
            st.code(tail_text(selected, max_lines=280) or "(empty file)", language="json")
        else:
            st.info("No JSON files are available for this job.")
    elif result_view == "XYZ":
        xyz_files = [path for path in files if path.suffix.lower() in XYZ_EXTENSIONS]
        if xyz_files:
            labels = [str(path.relative_to(run_dir)) for path in xyz_files]
            selected = xyz_files[labels.index(st.selectbox("XYZ file", labels, key=f"xyz_{job['job_id']}"))]
            st.code(tail_text(selected, max_lines=280) or "(empty file)", language="text")
        else:
            st.info("XYZ file はありません。")
    elif result_view == "Tables":
        csv_files = [path for path in files if path.suffix.lower() in TABLE_EXTENSIONS]
        if csv_files:
            labels = [str(path.relative_to(run_dir)) for path in csv_files]
            selected = csv_files[labels.index(st.selectbox("CSV file", labels, key=f"csv_{job['job_id']}"))]
            df = pd.read_csv(selected)
            st.dataframe(df, hide_index=True, height=320)
        else:
            st.info("CSVファイルはありません。")
    elif result_view == "Images":
        image_files = [path for path in files if path.suffix.lower() in IMAGE_EXTENSIONS]
        if image_files:
            labels = [str(path.relative_to(run_dir)) for path in image_files]
            selected = image_files[labels.index(st.selectbox("Image file", labels, key=f"img_{job['job_id']}"))]
            st.image(str(selected))
        else:
            st.info("imageファイルはありません。")
