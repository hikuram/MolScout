"""JSON-based job submission page."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import streamlit as st

from app_core.job_manifest import manifest_input, write_job_manifest
from app_core.job_runner import build_command
from app_core.json_submission import (
    JsonSubmissionError,
    configuration_summary,
    gui_defaults_from_config,
    input_mode,
    parse_json_config,
    product_is_required,
    result_name,
    uses_pyscf,
    workflow_steps,
)
from app_core.queue_manager import enqueue_job
from app_core.session_manager import (
    create_job,
    default_pyscf_config,
    get_session,
    job_dir,
    list_jobs,
    save_job,
)
from app_ui.sidebar import get_selected_session
from app_ui.views import (
    copy_source_file,
    ensure_worker_running,
    save_submit_defaults,
    session_submit_defaults,
    submit_reset_pending_key,
    write_uploaded_file,
)


def source_config_path(session_id: str, job: dict[str, Any]) -> Path | None:
    """Return the preferred reusable JSON configuration for a job."""
    root = job_dir(session_id, str(job["job_id"]))
    candidates = [
        root / "run_output" / "resolved_config.json",
        root / "submitted_config.json",
        root / "runtime_config.json",
    ]
    return next((path for path in candidates if path.exists()), None)


def available_source_jobs(session_id: str) -> list[dict[str, Any]]:
    """Return jobs that contain a reusable JSON configuration."""
    return [job for job in list_jobs(session_id) if source_config_path(session_id, job)]


def read_json_object(data: bytes, label: str) -> dict[str, Any]:
    """Read a generic UTF-8 JSON object."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise JsonSubmissionError(f"{label} must use UTF-8 encoding.") from exc
    except json.JSONDecodeError as exc:
        raise JsonSubmissionError(f"Invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise JsonSubmissionError(f"{label} must contain a JSON object.")
    return payload


def existing_input_records(session_id: str, job: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """Return reusable structure inputs from an existing job."""
    root = job_dir(session_id, str(job["job_id"]))
    manifest_path = root / "job_manifest.json"
    records: list[dict[str, Any]] = []

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        for item in manifest.get("inputs", []):
            if not isinstance(item, dict):
                continue
            stored_path = item.get("stored_path")
            if not stored_path:
                continue
            candidate = Path(str(stored_path))
            path = candidate if candidate.is_absolute() else root / candidate
            if path.exists() and path.suffix.lower() in {".xyz", ".traj"}:
                records.append({
                    "role": str(item.get("role") or "input"),
                    "original_name": str(item.get("original_name") or path.name),
                    "path": path,
                })

    if not records:
        for path_text in job.get("inputs", []):
            path = Path(str(path_text))
            if path.exists() and path.suffix.lower() in {".xyz", ".traj"}:
                records.append({
                    "role": "input",
                    "original_name": path.name,
                    "path": path,
                })

    if mode == "reactant_product":
        reactant = next((item for item in records if item["role"] == "reactant"), None)
        product = next((item for item in records if item["role"] == "product"), None)
        selected_ids = {id(item) for item in (reactant, product) if item is not None}
        fallback = [item for item in records if id(item) not in selected_ids]
        reactant = reactant or (fallback.pop(0) if fallback else None)
        product = product or (fallback.pop(0) if fallback else None)
        normalized = []
        if reactant is not None:
            normalized.append({**reactant, "role": "reactant"})
        if product is not None:
            normalized.append({**product, "role": "product"})
        return normalized
    if mode == "single_input":
        preferred = next((item for item in records if item["role"] == "input"), None)
        return [{**(preferred or records[0]), "role": "input"}] if records else []
    return records


def input_target_name(role: str, original_name: str, index: int = 1) -> str:
    """Return the normalized job-local filename for one input."""
    suffix = Path(original_name).suffix.lower() or ".xyz"
    if role == "reactant":
        return "reactant.xyz"
    if role == "product":
        return "product.xyz"
    if role == "input":
        return f"input{suffix}"
    return f"input_{index:03d}{suffix}"


def input_preview_rows(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build rows showing how source files will be stored in the new job."""
    rows = []
    for index, item in enumerate(records, start=1):
        role = str(item["role"])
        original_name = str(item["original_name"])
        rows.append({
            "Role": role,
            "Source file": original_name,
            "Stored as": f"inputs/{input_target_name(role, original_name, index)}",
        })
    return rows


def load_pyscf_source(
    *,
    selection: str,
    session: dict[str, Any],
    uploaded_file,
    source_job_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the PySCF configuration and its manifest metadata."""
    if selection == "Use source job settings":
        source_path = source_job_root / "pyscf_config.json" if source_job_root else None
        if source_path is None or not source_path.exists():
            raise JsonSubmissionError("The selected source job does not contain pyscf_config.json.")
        payload = read_json_object(source_path.read_bytes(), "source job PySCF configuration")
        return payload, {
            "source": "source_job",
            "original_name": source_path.name,
            "stored_path": "pyscf_config.json",
        }
    if selection == "Upload pyscf_config.json":
        if uploaded_file is None:
            raise JsonSubmissionError("Upload a PySCF configuration JSON file.")
        payload = read_json_object(uploaded_file.getvalue(), "PySCF configuration")
        return payload, {
            "source": "upload",
            "original_name": uploaded_file.name,
            "stored_path": "pyscf_config.json",
        }
    return dict(session.get("pyscf_config", default_pyscf_config())), {
        "source": "session",
        "original_name": None,
        "stored_path": "pyscf_config.json",
    }


st.markdown("## :material/data_object: Submit (JSON)")
st.caption("Load a MolScout JSON configuration, attach its input structures, and submit a new job.")

session = get_selected_session()
if not session:
    st.info("Create or select a session from the sidebar first.")
    st.stop()

session_id = session["session_id"]
owner_label = session.get("owner_label", "anonymous")
source_mode = st.radio(
    "Configuration source",
    ["Upload JSON", "Existing job"],
    horizontal=True,
    key=f"{session_id}_json_submit_source_mode",
)

config_bytes: bytes | None = None
config_original_name: str | None = None
source_job: dict[str, Any] | None = None
source_job_root: Path | None = None

if source_mode == "Upload JSON":
    config_file = st.file_uploader(
        "JSON configuration",
        type=["json"],
        accept_multiple_files=False,
        key=f"{session_id}_json_submit_config",
    )
    if config_file is not None:
        config_bytes = config_file.getvalue()
        config_original_name = config_file.name
else:
    source_jobs = available_source_jobs(session_id)
    if not source_jobs:
        st.info("No existing job contains a reusable JSON configuration.")
        st.stop()
    labels = {
        str(job["job_id"]): f"{job['job_id']} | {job.get('name', '')} | {job.get('status', '')}"
        for job in source_jobs
    }
    selected_job_id = st.selectbox(
        "Existing job",
        list(labels),
        format_func=lambda value: labels[value],
        key=f"{session_id}_json_submit_existing_job",
    )
    source_job = next(job for job in source_jobs if str(job["job_id"]) == selected_job_id)
    source_job_root = job_dir(session_id, selected_job_id)
    config_path = source_config_path(session_id, source_job)
    if config_path is not None:
        config_bytes = config_path.read_bytes()
        config_original_name = config_path.name
        st.caption(f"Configuration: `{config_path.relative_to(source_job_root)}`")

if config_bytes is None:
    st.info("Load a JSON configuration to continue.")
    st.stop()

try:
    config = parse_json_config(config_bytes)
except JsonSubmissionError as exc:
    st.error(str(exc))
    st.stop()

mode = input_mode(config)
source_identity = (
    f"{source_mode}|{source_job.get('job_id') if source_job else config_original_name}|".encode("utf-8")
    + config_bytes
)
source_token = hashlib.sha256(source_identity).hexdigest()[:12]
st.success(f"Configuration loaded: `{config_original_name}`")
st.dataframe(configuration_summary(config), hide_index=True, use_container_width=True)

with st.expander("Parsed configuration", expanded=False):
    st.json(config)

st.caption("Parsed values will override the current defaults for new jobs in this session.")
if st.button(
    "Set as session defaults",
    key=f"{session_id}_{source_token}_set_json_defaults",
):
    section, parsed_defaults = gui_defaults_from_config(config)
    current_session = get_session(session_id) or session
    merged_defaults = {
        **session_submit_defaults(current_session, section),
        **parsed_defaults,
    }
    save_submit_defaults(current_session, section, merged_defaults)
    st.session_state[submit_reset_pending_key(session_id, section)] = True
    st.success("Session defaults updated.")

st.markdown("### Job settings")
default_job_name = (
    f"{source_job.get('name', source_job['job_id'])} rerun"
    if source_job is not None
    else Path(config_original_name or "JSON submission").stem
)
job_name = st.text_input(
    "Job name",
    value=default_job_name,
    key=f"{session_id}_{source_token}_json_job_name",
)
job_note = st.text_area(
    "Job note",
    value="",
    key=f"{session_id}_{source_token}_json_job_note",
)

pyscf_selection = "Use this session's PySCF settings"
pyscf_upload = None
if uses_pyscf(config):
    pyscf_options = ["Use this session's PySCF settings"]
    if source_job_root is not None and (source_job_root / "pyscf_config.json").exists():
        pyscf_options.append("Use source job settings")
    pyscf_options.append("Upload pyscf_config.json")
    pyscf_selection = st.selectbox(
        "PySCF configuration",
        pyscf_options,
        key=f"{session_id}_{source_token}_json_pyscf_source",
    )
    if pyscf_selection == "Upload pyscf_config.json":
        pyscf_upload = st.file_uploader(
            "PySCF configuration JSON",
            type=["json"],
            accept_multiple_files=False,
            key=f"{session_id}_{source_token}_json_pyscf_upload",
        )

reactant_file = product_file = input_file = None
cat_files = None
source_inputs: list[dict[str, Any]] = []

st.markdown("### Input structures")
if source_mode == "Existing job" and source_job is not None:
    source_inputs = existing_input_records(session_id, source_job, mode)
    if source_inputs:
        st.dataframe(input_preview_rows(source_inputs), hide_index=True, use_container_width=True)
    else:
        st.warning("No reusable input structure was found in the selected job.")
elif mode == "reactant_product":
    cols = st.columns(2)
    reactant_file = cols[0].file_uploader(
        "Reactant structure",
        type=["xyz"],
        key=f"{session_id}_{source_token}_json_submit_reactant",
    )
    product_file = cols[1].file_uploader(
        "Product structure",
        type=["xyz"],
        key=f"{session_id}_{source_token}_json_submit_product",
        help="Optional for SCAN configurations.",
    )
    preview_records = []
    if reactant_file is not None:
        preview_records.append({"role": "reactant", "original_name": reactant_file.name})
    if product_file is not None:
        preview_records.append({"role": "product", "original_name": product_file.name})
    if preview_records:
        st.dataframe(input_preview_rows(preview_records), hide_index=True, use_container_width=True)
elif mode == "single_input":
    input_file = st.file_uploader(
        "Input structure or trajectory",
        type=["xyz", "traj"],
        key=f"{session_id}_{source_token}_json_submit_input",
    )
    if input_file is not None:
        st.dataframe(
            input_preview_rows([{"role": "input", "original_name": input_file.name}]),
            hide_index=True,
            use_container_width=True,
        )
else:
    cat_files = st.file_uploader(
        "Input structures or trajectories",
        type=["xyz", "traj"],
        accept_multiple_files=True,
        key=f"{session_id}_{source_token}_json_submit_cat",
    )
    if cat_files:
        preview_records = [
            {"role": f"input_{index:03d}", "original_name": uploaded.name}
            for index, uploaded in enumerate(cat_files, start=1)
        ]
        st.dataframe(input_preview_rows(preview_records), hide_index=True, use_container_width=True)

submitted = st.button(
    "Submit",
    type="primary",
    icon=":material/queue:",
    key=f"{session_id}_{source_token}_json_submit_button",
)

if not submitted:
    st.stop()

errors: list[str] = []
if not job_name.strip():
    errors.append("Job name is required.")
if source_mode == "Existing job":
    if not source_inputs:
        errors.append("The selected job does not contain reusable input structures.")
    elif mode == "reactant_product":
        if product_is_required(config) and len(source_inputs) < 2:
            errors.append("A product XYZ file is required for this path method.")
else:
    if mode == "reactant_product":
        if reactant_file is None:
            errors.append("A reactant XYZ file is required.")
        if product_is_required(config) and product_file is None:
            errors.append("A product XYZ file is required for this path method.")
    elif mode == "single_input" and input_file is None:
        errors.append("An input XYZ or trajectory file is required.")
    elif mode == "cat" and not cat_files:
        errors.append("At least one input XYZ or trajectory file is required.")

if uses_pyscf(config):
    try:
        pyscf_payload, pyscf_manifest = load_pyscf_source(
            selection=pyscf_selection,
            session=session,
            uploaded_file=pyscf_upload,
            source_job_root=source_job_root,
        )
    except JsonSubmissionError as exc:
        errors.append(str(exc))
        pyscf_payload = {}
        pyscf_manifest = {}
else:
    pyscf_payload = dict(session.get("pyscf_config", default_pyscf_config()))
    pyscf_manifest = {}

if errors:
    for message in errors:
        st.error(message)
    st.stop()

job = create_job(session_id=session_id, owner_label=owner_label, workflow="JSON submission")
job_root = job_dir(session_id, job["job_id"])
input_root = job_root / "inputs"
output_dir = job_root / "run_output"
stdout_log = job_root / "stdout.log"
submitted_config_path = job_root / "submitted_config.json"
runtime_config_path = job_root / "runtime_config.json"
pyscf_config_path = job_root / "pyscf_config.json"

submitted_config_path.write_bytes(config_bytes)
pyscf_config_path.write_text(
    json.dumps(pyscf_payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
runtime_config = dict(config)
runtime_config["PYSCF_CONFIG_FILE"] = str(pyscf_config_path)
runtime_config_path.write_text(
    json.dumps(runtime_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

reactant_path = product_path = input_path = None
cat_paths: list[Path] | None = None
manifest_inputs: list[dict[str, str]] = []

if source_mode == "Existing job":
    if mode == "reactant_product":
        reactant_source = source_inputs[0]
        reactant_path = copy_source_file(reactant_source["path"], input_root, "reactant.xyz")
        manifest_inputs.append(manifest_input(
            role="reactant",
            original_name=reactant_source["original_name"],
            stored_path=reactant_path,
            job_root=job_root,
        ))
        if len(source_inputs) > 1:
            product_source = source_inputs[1]
            product_path = copy_source_file(product_source["path"], input_root, "product.xyz")
            manifest_inputs.append(manifest_input(
                role="product",
                original_name=product_source["original_name"],
                stored_path=product_path,
                job_root=job_root,
            ))
    elif mode == "single_input":
        source = source_inputs[0]
        input_path = copy_source_file(
            source["path"],
            input_root,
            input_target_name("input", source["original_name"]),
        )
        manifest_inputs.append(manifest_input(
            role="input",
            original_name=source["original_name"],
            stored_path=input_path,
            job_root=job_root,
        ))
    else:
        cat_paths = []
        for index, source in enumerate(source_inputs, start=1):
            target_name = input_target_name(f"input_{index:03d}", source["original_name"], index)
            stored_path = copy_source_file(source["path"], input_root, target_name)
            cat_paths.append(stored_path)
            manifest_inputs.append(manifest_input(
                role=f"input_{index:03d}",
                original_name=source["original_name"],
                stored_path=stored_path,
                job_root=job_root,
            ))
elif mode == "reactant_product":
    reactant_path = write_uploaded_file(reactant_file, input_root, "reactant.xyz")
    manifest_inputs.append(manifest_input(
        role="reactant",
        original_name=reactant_file.name,
        stored_path=reactant_path,
        job_root=job_root,
    ))
    if product_file is not None:
        product_path = write_uploaded_file(product_file, input_root, "product.xyz")
        manifest_inputs.append(manifest_input(
            role="product",
            original_name=product_file.name,
            stored_path=product_path,
            job_root=job_root,
        ))
elif mode == "single_input":
    target_name = input_target_name("input", input_file.name)
    input_path = write_uploaded_file(input_file, input_root, target_name)
    manifest_inputs.append(manifest_input(
        role="input",
        original_name=input_file.name,
        stored_path=input_path,
        job_root=job_root,
    ))
else:
    cat_paths = []
    for index, uploaded in enumerate(cat_files or [], start=1):
        target_name = input_target_name(f"input_{index:03d}", uploaded.name, index)
        stored_path = write_uploaded_file(uploaded, input_root, target_name)
        cat_paths.append(stored_path)
        manifest_inputs.append(manifest_input(
            role=f"input_{index:03d}",
            original_name=uploaded.name,
            stored_path=stored_path,
            job_root=job_root,
        ))

steps = workflow_steps(runtime_config)
job["name"] = job_name.strip()
job["notes"] = job_note.strip()
job["charge"] = int(runtime_config.get("CHARGE", 0))
job["method"] = str(runtime_config.get("CALC_TYPE", "orbmol"))
job["result_name"] = result_name(runtime_config)
job["script_name"] = "molscout.py"
job["workflow_steps"] = steps
job["temperature"] = float(runtime_config.get("THERMO_TEMPERATURE", 298.15))
job["mult"] = int(runtime_config.get("MULT", 1))
job["tblite_method"] = str(runtime_config.get("TBLITE_METHOD", "hybrid"))
job["config_overrides"] = runtime_config
job["inputs"] = [str(path) for path in [reactant_path, product_path, input_path] if path is not None]
if cat_paths:
    job["inputs"] = [str(path) for path in cat_paths]
job["output_dir"] = str(output_dir)
job["stdout_log"] = str(stdout_log)
job["command"] = build_command(
    workflow="Full workflow",
    script_name=job["script_name"],
    output_dir=output_dir,
    charge=job["charge"],
    method=job["method"],
    result_name=job["result_name"],
    reactant_path=reactant_path,
    product_path=product_path,
    input_path=input_path,
    cat_paths=cat_paths,
    workflow_steps=steps,
    temperature=job["temperature"],
    tblite_method=job["tblite_method"],
    config_overrides=None,
    config_path=runtime_config_path,
)
manifest_path = write_job_manifest(
    job,
    submission_source="json_upload" if source_mode == "Upload JSON" else "existing_job",
    config_original_name=config_original_name,
    config_stored_path=submitted_config_path,
    runtime_config_path=runtime_config_path,
    source_job=(
        {
            "session_id": session_id,
            "job_id": source_job["job_id"],
            "config_path": str(source_config_path(session_id, source_job).relative_to(source_job_root)),
        }
        if source_job is not None and source_job_root is not None
        else None
    ),
    pyscf_config=pyscf_manifest,
    inputs=manifest_inputs,
)
job["manifest_path"] = str(manifest_path)

save_job(job)
enqueue_job(job)
ensure_worker_running()
st.success(f"Job `{job['job_id']}` was added to the queue.")
