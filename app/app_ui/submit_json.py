"""Minimal JSON-based job submission page."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app_core.job_manifest import manifest_input, write_job_manifest
from app_core.job_runner import build_command
from app_core.json_submission import (
    JsonSubmissionError,
    input_mode,
    parse_json_config,
    product_is_required,
    result_name,
    workflow_steps,
)
from app_core.queue_manager import enqueue_job
from app_core.session_manager import (
    create_job,
    default_pyscf_config,
    job_dir,
    save_job,
)
from app_ui.sidebar import get_selected_session
from app_ui.views import ensure_worker_running, write_uploaded_file


st.markdown("## :material/data_object: Submit (JSON)")
st.caption("Upload a MolScout JSON configuration and the required input structure files.")

session = get_selected_session()
if not session:
    st.info("Create or select a session from the sidebar first.")
    st.stop()

session_id = session["session_id"]
owner_label = session.get("owner_label", "anonymous")
config_file = st.file_uploader(
    "JSON configuration",
    type=["json"],
    accept_multiple_files=False,
    key=f"{session_id}_json_submit_config",
)

config: dict | None = None
mode: str | None = None
if config_file is not None:
    try:
        config = parse_json_config(config_file.getvalue())
    except JsonSubmissionError as exc:
        st.error(str(exc))
    else:
        mode = input_mode(config)
        st.success(f"Configuration loaded: `{config_file.name}`")

reactant_file = product_file = input_file = None
cat_files = None
if config is not None and mode == "reactant_product":
    cols = st.columns(2)
    reactant_file = cols[0].file_uploader(
        "Reactant structure",
        type=["xyz"],
        key=f"{session_id}_json_submit_reactant",
    )
    product_file = cols[1].file_uploader(
        "Product structure",
        type=["xyz"],
        key=f"{session_id}_json_submit_product",
        help="Optional for SCAN configurations.",
    )
elif config is not None and mode == "single_input":
    input_file = st.file_uploader(
        "Input structure or trajectory",
        type=["xyz", "traj"],
        key=f"{session_id}_json_submit_input",
    )
elif config is not None and mode == "cat":
    cat_files = st.file_uploader(
        "Input structures or trajectories",
        type=["xyz", "traj"],
        accept_multiple_files=True,
        key=f"{session_id}_json_submit_cat",
    )

submitted = st.button(
    "Submit",
    type="primary",
    icon=":material/queue:",
    disabled=config is None,
    key=f"{session_id}_json_submit_button",
)

if submitted and config is not None and mode is not None:
    errors: list[str] = []
    if mode == "reactant_product":
        if reactant_file is None:
            errors.append("A reactant XYZ file is required.")
        if product_is_required(config) and product_file is None:
            errors.append("A product XYZ file is required for this path method.")
    elif mode == "single_input" and input_file is None:
        errors.append("An input XYZ or trajectory file is required.")
    elif mode == "cat" and not cat_files:
        errors.append("At least one input XYZ or trajectory file is required.")

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
    pyscf_config_path = job_root / "pyscf_config.json"

    runtime_config = dict(config)
    runtime_config["PYSCF_CONFIG_FILE"] = str(pyscf_config_path)
    submitted_config_path.write_text(
        json.dumps(runtime_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pyscf_config_path.write_text(
        json.dumps(session.get("pyscf_config", default_pyscf_config()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    reactant_path = product_path = input_path = None
    cat_paths: list[Path] | None = None
    manifest_inputs: list[dict[str, str]] = []

    if mode == "reactant_product":
        reactant_path = write_uploaded_file(reactant_file, input_root, "reactant.xyz")
        manifest_inputs.append(
            manifest_input(
                role="reactant",
                original_name=reactant_file.name,
                stored_path=reactant_path,
                job_root=job_root,
            )
        )
        if product_file is not None:
            product_path = write_uploaded_file(product_file, input_root, "product.xyz")
            manifest_inputs.append(
                manifest_input(
                    role="product",
                    original_name=product_file.name,
                    stored_path=product_path,
                    job_root=job_root,
                )
            )
    elif mode == "single_input":
        input_suffix = Path(input_file.name).suffix.lower() or ".xyz"
        input_path = write_uploaded_file(input_file, input_root, f"input{input_suffix}")
        manifest_inputs.append(
            manifest_input(
                role="input",
                original_name=input_file.name,
                stored_path=input_path,
                job_root=job_root,
            )
        )
    else:
        cat_paths = []
        for index, uploaded in enumerate(cat_files or [], start=1):
            suffix = Path(uploaded.name).suffix.lower() or ".xyz"
            stored_path = write_uploaded_file(uploaded, input_root, f"input_{index:03d}{suffix}")
            cat_paths.append(stored_path)
            manifest_inputs.append(
                manifest_input(
                    role=f"input_{index:03d}",
                    original_name=uploaded.name,
                    stored_path=stored_path,
                    job_root=job_root,
                )
            )

    steps = workflow_steps(runtime_config)
    job["name"] = Path(config_file.name).stem or "JSON submission"
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
        config_path=submitted_config_path,
    )
    manifest_path = write_job_manifest(
        job,
        submission_source="json_upload",
        config_original_name=config_file.name,
        config_stored_path=submitted_config_path,
        inputs=manifest_inputs,
    )
    job["manifest_path"] = str(manifest_path)

    save_job(job)
    enqueue_job(job)
    ensure_worker_running()
    st.success(f"Job `{job['job_id']}` was added to the queue.")
