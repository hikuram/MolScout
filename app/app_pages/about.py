"""About page."""

from __future__ import annotations

import streamlit as st

from app_core.paths import AUTO_REFRESH_SECONDS


st.markdown("# :material/science: MolScout Remote Queue")
st.caption(
    "長時間を要するワークフローを共有実行するための laboratory queue です。"
    "ユーザーセッションとサーバーモニタリング機能を備えています。"
)
st.markdown(
    '<span class="app-badge">1 worker</span>'
    f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
    '<span class="app-badge">PostgreSQL metadata</span>',
    unsafe_allow_html=True,
)

st.divider()

st.markdown("### Page guide")
st.markdown(
    """
- **Queue**: 共有キューと選択中セッションの概要を確認します。
- **Submit**: 反応経路探索とファイル連結処理の job を投入します。
- **Results**: セッション内 job、ログ、結果ファイル、ZIP download を確認します。
- **Chemiscope**: trajectory / structure を可視化します。
- **Data**: 全セッションの成果物検索と DB/filesystem 整合性診断を行います。
- **PySCF**: 選択中セッションの PySCF 設定を編集します。
"""
)

st.markdown("### Sidebar")
st.markdown(
    """
セッション作成・選択、monitoring、環境チェック、サンプル一覧、worker log は全 page 共通の sidebar にあります。
期限ベースの Cleanup は PostgreSQL 移行中のため凍結しています。
"""
)
