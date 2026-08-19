"""Cross-session artifact catalog and storage diagnostics."""

from __future__ import annotations

import csv
import io

import streamlit as st

from app_ui.i18n import t, tf

from app_core.artifact_manager import (
    artifact_path,
    diagnose_artifact_catalog,
    scan_all_artifacts,
)
from app_core.database import (
    artifact_filter_values,
    artifact_summary,
    search_artifact_records,
)
from app_core.session_manager import list_sessions
from app_ui.sidebar import (
    database_page_url,
    set_database_multi_selection,
)
from app_ui.views import format_app_time

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DIAGNOSTIC_STATE_KEY = "artifact_catalog_diagnostic"


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def issues_csv(issues: list[dict]) -> bytes:
    output = io.StringIO()
    fieldnames = ["issue", "session_id", "job_id", "relative_path", "detail"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(issues)
    return output.getvalue().encode("utf-8-sig")


def render_summary() -> None:
    summary = artifact_summary()
    cols = st.columns(5)
    cols[0].metric("Indexed Files", summary["artifact_count"])
    cols[1].metric("Indexed File Size", format_bytes(summary["available_bytes"]))
    cols[2].metric("Missing Files", summary["missing_count"])
    cols[3].metric("Sessions", summary["session_count"])
    cols[4].metric("Jobs", summary["job_count"])



def render_catalog() -> None:
    render_summary()
    filter_values = artifact_filter_values()
    sessions = list_sessions()
    session_ids = [str(item["session_id"]) for item in sessions]

    st.markdown(t('### :material/search: Cross-session search'))
    row1 = st.columns([2.5, 1.5, 1.3])
    search_text = row1[0].text_input(
        t('Search'),
        placeholder=t('Filename, path, job name, job note, workflow, method, owner'),
    )
    selected_session = row1[1].selectbox(
        t('Session'),
        [""] + session_ids,
        format_func=lambda value: t('All') if not value else value,
    )
    selected_type = row1[2].selectbox(
        t('File type'),
        [""] + filter_values["artifact_types"],
        format_func=lambda value: t('All') if not value else value,
    )

    row2 = st.columns([1.3, 1.3, 1])
    availability = row2[0].selectbox(
        t('File status'),
        ["", "available", "missing"],
        index=1,
        format_func=lambda value: {"": t('All'), "available": t('Available'), "missing": t('Missing')}[value],
    )
    job_status = row2[1].selectbox(
        t('Job status'),
        [""] + filter_values["job_statuses"],
        format_func=lambda value: t('All') if not value else value,
    )
    limit = row2[2].selectbox(t('Result limit'), [100, 300, 500, 1000], index=2)

    records = search_artifact_records(
        text=search_text,
        session_id=selected_session,
        artifact_type=selected_type,
        availability_status=availability,
        job_status=job_status,
        limit=limit,
    )
    st.caption(tf(
        "Showing {count} records. File contents are not stored in the database; entries reference files under the data directory.",
        count=len(records),
    ))

    table_rows = [
        {
            "modified": format_app_time(item.get("modified_at")),
            "session": item["session_id"],
            "job": item.get("job_id") or "-",
            "status": item.get("job_status") or "-",
            "Job Note": item.get("job_notes") or "",
            "workflow": item.get("workflow") or "-",
            "method": item.get("method") or "-",
            "type": item["artifact_type"],
            "role": item["artifact_role"],
            "size": format_bytes(item["size_bytes"]),
            "file": item["filename"],
            "path": item["relative_path"],
            "availability": item["availability_status"],
        }
        for item in records
    ]
    selection = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        height=460,
        on_select="rerun",
        selection_mode="multi-row",
        key="artifact_catalog_search_results",
    )

    if not records:
        return

    path_options = [item["relative_path"] for item in records]
    selected_rows = [
        index
        for index in selection.selection.rows
        if isinstance(index, int) and 0 <= index < len(records)
    ]
    if selected_rows:
        st.session_state["artifact_catalog_selected_path"] = records[selected_rows[0]]["relative_path"]
    if st.session_state.get("artifact_catalog_selected_path") not in path_options:
        st.session_state["artifact_catalog_selected_path"] = path_options[0]

    selected_jobs: list[dict] = []
    seen_jobs: set[tuple[str, str]] = set()
    for index in selected_rows:
        item = records[index]
        session_id = str(item.get("session_id") or "")
        job_id = str(item.get("job_id") or "")
        key = (session_id, job_id)
        if not session_id or not job_id or key in seen_jobs:
            continue
        seen_jobs.add(key)
        selected_jobs.append(item)

    if selected_rows:
        st.caption(
            f"Selected artifacts: {len(selected_rows)} | Selected jobs: {len(selected_jobs)}"
        )

    if selected_jobs:
        st.markdown("### :material/open_in_new: Selected Jobs")
        st.caption("Each link opens in a new browser tab with an independent Database context.")
        for index, item in enumerate(selected_jobs):
            session_id = str(item.get("session_id") or "")
            job_id = str(item.get("job_id") or "")
            info_col, results_col, chemiscope_col = st.columns([2.4, 1, 1])
            note = str(item.get("job_notes") or "")
            info_col.markdown(
                f"`{job_id}`  \n{session_id} | {item.get('workflow') or '-'} | {item.get('job_status') or '-'}"
                + (f"  \nJob Note: {note}" if note else "")
            )
            results_url = database_page_url("results", session_id, job_id)
            chemiscope_url = database_page_url("chemiscope", session_id, job_id)
            if results_url:
                results_col.link_button(
                    "Results",
                    results_url,
                    icon=":material/open_in_new:",
                    width="stretch",
                )
            else:
                results_col.button(
                    "Results",
                    disabled=True,
                    width="stretch",
                    key=f"artifact_results_link_unavailable_{index}",
                )
            if chemiscope_url:
                chemiscope_col.link_button(
                    "Chemiscope",
                    chemiscope_url,
                    icon=":material/open_in_new:",
                    width="stretch",
                )
            else:
                chemiscope_col.button(
                    "Chemiscope",
                    disabled=True,
                    width="stretch",
                    key=f"artifact_chemiscope_link_unavailable_{index}",
                )

        selected_sessions = {str(item.get("session_id") or "") for item in selected_jobs}
        if len(selected_sessions) == 1:
            archive_session_id = next(iter(selected_sessions))
            archive_job_ids = [str(item.get("job_id") or "") for item in selected_jobs]
            if st.button(
                ":material/checklist: Select jobs in sidebar",
                width="stretch",
                key="artifact_use_selected_jobs_for_archive",
            ):
                set_database_multi_selection(archive_session_id, archive_job_ids)
                st.rerun()
        else:
            st.caption("Sidebar job selection is session-scoped; selected jobs span multiple sessions.")

    st.markdown(t('### :material/draft: File details'))
    selected_path = st.selectbox(
        t('Selected file'),
        path_options,
        key="artifact_catalog_selected_path",
        format_func=lambda value: value,
    )
    selected = next(item for item in records if item["relative_path"] == selected_path)
    cols = st.columns(4)
    cols[0].metric("Artifact Type", selected["artifact_type"])
    cols[1].metric("Role", selected["artifact_role"])
    cols[2].metric("File Size", format_bytes(selected["size_bytes"]))
    cols[3].metric("Status", selected["availability_status"])
    st.code(selected["relative_path"], language="text")

    target_job_id = str(selected.get("job_id") or "")
    if target_job_id:
        target_session_id = str(selected.get("session_id") or "")
        results_url = database_page_url("results", target_session_id, target_job_id)
        chemiscope_url = database_page_url("chemiscope", target_session_id, target_job_id)
        action_cols = st.columns(2)
        if results_url:
            action_cols[0].link_button(
                "Open Job in Results",
                results_url,
                icon=":material/open_in_new:",
                width="stretch",
            )
        if chemiscope_url:
            action_cols[1].link_button(
                "Open Job in Chemiscope",
                chemiscope_url,
                icon=":material/open_in_new:",
                width="stretch",
            )
        if not results_url or not chemiscope_url:
            st.caption("New-tab links are unavailable because the current app URL could not be resolved.")
    else:
        st.caption(t('This artifact has no Job ID, so Results / Chemiscope links are not available.'))

    metadata = selected.get("metadata") or {}
    if metadata:
        with st.expander(t('Catalog metadata'), expanded=False):
            st.json(metadata)

    try:
        path = artifact_path(selected["relative_path"])
    except ValueError as exc:
        st.error(str(exc))
        return
    if not path.exists() or not path.is_file():
        st.warning(t('The file is missing. Run diagnostics or rescan from the maintenance tab.'))
        return
    current_size = path.stat().st_size
    if current_size > MAX_DOWNLOAD_BYTES:
        st.info(
            tf(
                "{size} exceeds the download limit for this page. Use the file directly from the data directory.",
                size=format_bytes(current_size),
            )
        )
        return
    st.download_button(
        t(':material/download: Download this file'),
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/octet-stream",
        width="stretch",
    )


def render_maintenance() -> None:
    st.info(
        t('This page only updates the artifact catalog and runs diagnostics. It does not delete files or database records, or automatically repair orphaned data.')
    )
    cols = st.columns(2)
    if cols[0].button(t(':material/sync: Rescan data'), type="primary", width="stretch"):
        try:
            with st.spinner(t('Scanning the data directory...')):
                result = scan_all_artifacts(source="admin_page", dry_run=False)
        except Exception as exc:
            st.error(tf(
                "Rescan failed: {error_type}: {error}",
                error_type=type(exc).__name__,
                error=exc,
            ))
        else:
            st.session_state.pop(DIAGNOSTIC_STATE_KEY, None)
            if result["errors"]:
                st.warning(
                    tf(
                        "Found {artifact_count} artifacts with {error_count} errors.",
                        artifact_count=result["artifacts_found"],
                        error_count=len(result["errors"]),
                    )
                )
                st.code("\n".join(result["errors"]), language="text")
            else:
                st.success(tf(
                    "Scanned {session_count} sessions, {job_count} jobs, and {artifact_count} files.",
                    session_count=result["sessions_scanned"],
                    job_count=result["jobs_scanned"],
                    artifact_count=result["artifacts_found"],
                ))

    if cols[1].button(t(':material/fact_check: Run consistency diagnostics'), width="stretch"):
        try:
            with st.spinner(t('Comparing database records with files...')):
                st.session_state[DIAGNOSTIC_STATE_KEY] = diagnose_artifact_catalog()
        except Exception as exc:
            st.error(tf(
                "Diagnostics failed: {error_type}: {error}",
                error_type=type(exc).__name__,
                error=exc,
            ))

    report = st.session_state.get(DIAGNOSTIC_STATE_KEY)
    if not report:
        st.caption(t('Diagnostics run only when explicitly requested. Scanning may take time for a large data directory.'))
        return

    st.markdown(t('### :material/health_and_safety: Diagnostic results'))
    cols = st.columns(3)
    cols[0].metric(t('Catalog entries'), report["registered_count"])
    cols[1].metric(t('Files in scope'), report["eligible_files_found"])
    cols[2].metric(t('Potential issues'), report["issue_count"])

    issues = report["issues"]
    if not issues:
        st.success(t('No inconsistencies were found for cataloged artifacts.'))
        return

    st.dataframe(issues, hide_index=True, width="stretch", height=430)
    st.download_button(
        t(':material/download: Download diagnostics CSV'),
        data=issues_csv(issues),
        file_name="molscout_artifact_diagnostics.csv",
        mime="text/csv",
        width="stretch",
    )
    with st.expander(t('Issue counts by type'), expanded=False):
        st.json(report["issue_counts"])


st.set_page_config(page_title="MolScout [Data]")
st.markdown("## :material/database: Data Catalog")
st.caption(t('Search artifacts across all sessions and check consistency between PostgreSQL catalog entries and the data directory.'))

catalog_tab, maintenance_tab = st.tabs([t('Browse'), t('Maintenance / diagnostics')])
with catalog_tab:
    render_catalog()
with maintenance_tab:
    render_maintenance()
