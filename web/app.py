import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
import os
import logging
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import Filters, build_where as cohort_where
from datetime import datetime, timedelta, timezone

# Tags that describe the dataset itself rather than individual repos — excluded from all charts/filters
BLOCKLIST_TAGS = frozenset([
    "government", "open-source", "public-sector", "open-government",
    "government-software", "government-tool", "government-project",
    "government-repository", "government-platform", "government-code",
])

# Tags that duplicate the language field already in the schema
LANGUAGE_TAGS = frozenset([
    "javascript", "python", "java", "typescript", "html", "css", "php",
    "ruby", "shell", "r", "scala", "c#", "kotlin", "go", "rust", "c",
    "c++", "perl", "swift", "matlab", "bash", "json", "xml", "yaml",
    "sql", "makefile", "scss",
])

# Combined filter — tags to hide from dashboard display
EXCLUDED_TAGS = BLOCKLIST_TAGS | LANGUAGE_TAGS

# Minimum repos a tag must appear in to show in charts/filters
MIN_TAG_REPOS = 2

def _tag_filter_sql(tag_col: str = "tag") -> str:
    """Return a SQL fragment excluding noise tags. Use with AND."""
    excluded = EXCLUDED_TAGS
    ph = ",".join(["?"] * len(excluded))
    return f"{tag_col} NOT IN ({ph}) AND {tag_col} IS NOT NULL"

def _tag_filter_params() -> list:
    return list(EXCLUDED_TAGS)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("govtech-dashboard")
logger.info("Starting GovTech Dashboard...")

st.set_page_config(
    page_title="GovTech GitHub Explorer",
    page_icon="🏛️",
    layout="wide",
)


@st.cache_data(ttl=300)
def get_db_path(override=None):
    if override:
        return override
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "govtech.db"),
        os.path.join(os.path.dirname(__file__), "govtech.db"),
        "govtech.db",
        "../govtech.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            logger.info(f"Found local DB: {os.path.abspath(p)}")
            return os.path.abspath(p)
    logger.info("No local DB found, downloading from HuggingFace Hub...")
    try:
        from huggingface_hub import hf_hub_download, HfApi
        revision = HfApi().dataset_info("AndreasThinks/government-github-repos").sha
        from huggingface_hub.errors import EntryNotFoundError
        try:
            hf_hub_download(repo_id="AndreasThinks/government-github-repos", filename="data/manifest.json", repo_type="dataset", revision=revision)
        except EntryNotFoundError:
            # Legacy publications have no manifest. Keep them readable during the
            # first migration/refresh, but never present them as verified or fresh.
            st.warning("Legacy publication has no verification manifest. Refresh coverage is unknown.")
        path = hf_hub_download(
            repo_id="AndreasThinks/government-github-repos",
            filename="data/govtech.db",
            repo_type="dataset",
            revision=revision,
        )
        logger.info(f"Downloaded DB to: {path}")
        return path
    except Exception as e:
        logger.error("Failed to download dataset (%s)", type(e).__name__)
        st.error("Could not load a verified dataset revision.")
        st.stop()


DB_PATH = get_db_path(os.getenv('GOVTECH_DB_PATH'))


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data(ttl=300)
def _query_df(db_path, sql, params=None):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    df = pd.read_sql_query(sql, conn, params=params or [])
    conn.close()
    return df


@st.cache_data(ttl=300)
def _query_one(db_path, sql, params=None):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    result = cur.execute(sql, params or []).fetchone()[0]
    conn.close()
    return result


def query_df(sql, params=None):
    return _query_df(DB_PATH, sql, params)


def query_one(sql, params=None):
    return _query_one(DB_PATH, sql, params)


@st.cache_data(ttl=300)
def get_last_updated_display(db_path):
    iso = query_one("SELECT MAX(last_scraped) FROM repositories")
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return None


@st.cache_data(ttl=600)
def load_filter_options(db_path):
    conn = get_conn()
    countries = pd.read_sql_query(
        "SELECT DISTINCT country FROM repositories WHERE country IS NOT NULL AND country != '' ORDER BY country", conn
    )["country"].tolist()
    languages = pd.read_sql_query(
        "SELECT DISTINCT language FROM repositories WHERE language IS NOT NULL AND language != '' ORDER BY language", conn
    )["language"].tolist()
    tf_sql = _tag_filter_sql()
    tf_params = _tag_filter_params()
    tags = pd.read_sql_query(
        f"SELECT tag, COUNT(DISTINCT html_url) as c FROM repository_tags WHERE {tf_sql} GROUP BY tag HAVING c >= {MIN_TAG_REPOS} ORDER BY c DESC",
        conn, params=tf_params
    )["tag"].tolist()
    orgs = pd.read_sql_query(
        "SELECT owner, COUNT(*) as c FROM repositories GROUP BY owner ORDER BY c DESC ", conn
    )["owner"].tolist()
    conn.close()
    return countries, languages, tags, orgs


# ==================== SIDEBAR FILTERS ====================
st.sidebar.title("🔭 Filters")
st.sidebar.caption("Applied across all tabs")

countries, languages, tags, orgs = load_filter_options(DB_PATH)

sel_countries = st.sidebar.multiselect("🌍 Country", countries, key="g_country")
sel_orgs = st.sidebar.multiselect("🏢 Organisation", orgs, key="g_org")
sel_languages = st.sidebar.multiselect("💻 Language", languages, key="g_lang")
sel_tags = st.sidebar.multiselect("🏷️ Tag", tags, key="g_tag")

st.sidebar.divider()
st.sidebar.subheader("📅 Activity")
activity_options = {
    "All time": None,
    "Active last 3 months": 90,
    "Active last 6 months": 180,
    "Active last 12 months": 365,
    "Active last 2 years": 730,
}
activity_label = st.sidebar.selectbox("Last pushed", list(activity_options.keys()), index=0, key="g_activity")
activity_days = activity_options[activity_label]

st.sidebar.divider()
show_archived = st.sidebar.checkbox("Include archived", value=False, key="g_arch")
show_forks = st.sidebar.checkbox("Include forks", value=True, key="g_forks")
min_stars = st.sidebar.slider("Min stars", 0, 500, 0, key="g_stars")

st.sidebar.divider()
st.sidebar.markdown(
    "🔗 [GitHub](https://github.com/AndreasThinks/open-govtech-report) &nbsp;|&nbsp; "
    "[Dataset](https://huggingface.co/datasets/AndreasThinks/government-github-repos) &nbsp;|&nbsp; "
    "[➕ Submit a missing org](https://github.com/AndreasThinks/open-govtech-report/blob/main/CONTRIBUTING.md)",
    unsafe_allow_html=True,
)


def build_where(extra_conditions=None, base_table="r", tag_table="rt"):
    filters = Filters(tuple(sel_countries), tuple(sel_languages), tuple(sel_orgs),
                      tuple(sel_tags), show_archived, show_forks, min_stars, activity_days)
    where, params = cohort_where(filters)
    conditions = ([where[6:]] if where else []) + list(extra_conditions or [])
    return conditions, params


def build_tag_join_where(extra_conditions=None):
    conditions, params = build_where(extra_conditions)
    return ('WHERE ' + ' AND '.join(conditions) if conditions else ''), params, ''


# ==================== HEADER ====================
st.title("🏛️ GovTech GitHub Explorer")
st.caption("Public repositories from government registry accounts; sidebar filters apply across tabs")

# ==================== TABS ====================
tab_overview, tab_explorer, tab_tags, tab_insights, tab_trends, tab_about = st.tabs(
    ["📊 Overview", "🔍 Explorer", "🏷️ Tags", "💡 Insights", "📈 Trends", "ℹ️ About"]
)


# ==================== OVERVIEW ====================
with tab_overview:
    where, params, tag_join = build_tag_join_where()

    total_filtered = query_one(f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join} {where}", params)
    account_count = query_one(f"SELECT COUNT(DISTINCT r.owner) FROM repositories r {where}", params)
    country_count_val = query_one(
        f"SELECT COUNT(DISTINCT r.country) FROM repositories r {tag_join} {where}", params
    )

    # Active in last 12m within filtered set
    active_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active_conditions, active_params = build_where([f"r.pushed_at >= ?"])
    active_params_full = active_params.copy()
    active_params_full.insert(
        len(active_params) - 1 if active_params else 0, active_cutoff
    )
    # Simpler: just count directly
    conn = get_conn()
    conds_12m, p_12m = build_where()
    conds_12m.append("r.pushed_at >= ?")
    p_12m.append(active_cutoff)
    tj2 = ""
    w12 = ("WHERE " + " AND ".join(conds_12m)) if conds_12m else ""
    active_12m = conn.execute(
        f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tj2} {w12}", p_12m
    ).fetchone()[0]
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repositories", f"{total_filtered:,}")
    c2.metric("Accounts", f"{account_count:,}")
    c3.metric("Countries", country_count_val)
    c4.metric("Active last 12m", f"{active_12m:,}", help="Repos with a push in the last 12 months")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Top Countries by Repositories")
        df_countries = query_df(
            f"SELECT r.country, COUNT(DISTINCT r.html_url) as count FROM repositories r {tag_join} {where} GROUP BY r.country ORDER BY count DESC LIMIT 20",
            params,
        )
        if not df_countries.empty:
            fig = px.bar(df_countries, x="country", y="count", color="count", color_continuous_scale="Blues")
            fig.update_layout(showlegend=False, xaxis_title="Country", yaxis_title="Repositories", coloraxis_showscale=False)
            st.plotly_chart(fig, width="stretch")

    with col_right:
        st.subheader("Top Languages")
        df_langs = query_df(
            f"""SELECT r.language, COUNT(DISTINCT r.html_url) as count
                FROM repositories r {tag_join} {where}
                {"AND" if where else "WHERE"} r.language IS NOT NULL AND r.language != ''
                GROUP BY r.language ORDER BY count DESC LIMIT 15""",
            params,
        )
        if not df_langs.empty:
            fig = px.bar(df_langs, x="count", y="language", orientation="h", color="count", color_continuous_scale="Greens")
            fig.update_layout(showlegend=False, yaxis=dict(autorange="reversed"), xaxis_title="Repositories", yaxis_title="", coloraxis_showscale=False)
            st.plotly_chart(fig, width="stretch")

    st.subheader("Repository Creation Timeline")
    df_timeline = query_df(
        f"""SELECT SUBSTR(r.created_at, 1, 4) as year, COUNT(DISTINCT r.html_url) as count
            FROM repositories r {tag_join} {where}
            {"AND" if where else "WHERE"} r.created_at IS NOT NULL
            GROUP BY year ORDER BY year""",
        params,
    )
    df_timeline = df_timeline[df_timeline["year"].str.match(r"^\d{4}$", na=False)]

    # Also pull active repos per year (pushed_at within 12m of each year-end — proxy: pushed in that year or later)
    df_pushed = query_df(
        f"""SELECT SUBSTR(r.pushed_at, 1, 4) as year, COUNT(DISTINCT r.html_url) as active
            FROM repositories r {tag_join} {where}
            {"AND" if where else "WHERE"} r.pushed_at IS NOT NULL
            GROUP BY year ORDER BY year""",
        params,
    )
    df_pushed = df_pushed[df_pushed["year"].str.match(r"^\d{4}$", na=False)]

    if not df_timeline.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_timeline["year"], y=df_timeline["count"],
            name="Created", fill="tozeroy", mode="lines",
            line=dict(color="#3b82f6"), fillcolor="rgba(59,130,246,0.2)"
        ))
        if not df_pushed.empty:
            fig.add_trace(go.Scatter(
                x=df_pushed["year"], y=df_pushed["active"],
                name="Last pushed", fill="tozeroy", mode="lines",
                line=dict(color="#10b981"), fillcolor="rgba(16,185,129,0.15)"
            ))
        fig.update_layout(xaxis_title="Year", yaxis_title="Repositories", legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.caption("'Last pushed' shows when repositories last received a commit — a proxy for active maintenance.")


# ==================== EXPLORER ====================
with tab_explorer:
    where_e, params_e, tag_join_e = build_tag_join_where()

    search_text = st.text_input("Search name / description", key="exp_search")
    if search_text:
        where_e += (" AND " if where_e else "WHERE ") + "(r.name LIKE ? OR r.description LIKE ?)"
        params_e += [f"%{search_text}%"] * 2

    sort_col = st.selectbox("Sort by", ["stars", "forks", "pushed_at", "created_at"], key="exp_sort")

    conn = get_conn()
    count_sql = f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_e} {where_e}"
    total_results = conn.execute(count_sql, params_e).fetchone()[0]
    conn.close()

    st.write(f"**{total_results:,}** repositories match current filters")

    page_size = 50
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    filter_signature = (where_e, tuple(params_e), sort_col)
    if st.session_state.get('exp_filters') != filter_signature:
        st.session_state['exp_page'] = 1
        st.session_state['exp_filters'] = filter_signature
    st.session_state['exp_page'] = min(st.session_state.get('exp_page', 1), total_pages)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, key="exp_page")
    offset = (page - 1) * page_size

    search_clause = ""
    search_params = []

    data_sql = f"""
        SELECT r.html_url, r.name, r.owner, r.country, r.language, r.stars, r.forks,
               r.license, r.created_at, r.pushed_at, r.archived, r.fork
        FROM repositories r {tag_join_e} {where_e} {search_clause}
        GROUP BY r.html_url
        ORDER BY r.{sort_col} DESC
        LIMIT ? OFFSET ?
    """
    df_results = query_df(data_sql, params_e + search_params + [page_size, offset])

    if not df_results.empty:
        st.dataframe(
            df_results,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "name": st.column_config.TextColumn("Name"),
                "owner": st.column_config.TextColumn("Owner"),
                "country": st.column_config.TextColumn("Country"),
                "language": st.column_config.TextColumn("Language"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "forks": st.column_config.NumberColumn("🍴 Forks"),
                "license": st.column_config.TextColumn("License"),
                "created_at": st.column_config.TextColumn("Created"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
                "archived": st.column_config.CheckboxColumn("Archived"),
                "fork": st.column_config.CheckboxColumn("Fork"),
            },
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Page {page} of {total_pages}")
    else:
        st.info("No repositories match the current filters.")


# ==================== TAGS ====================
with tab_tags:
    where_t, params_t, tag_join_t = build_tag_join_where()

    tagged_count_t = query_one(f"SELECT COUNT(*) FROM repositories r {where_t} {'AND' if where_t else 'WHERE'} EXISTS (SELECT 1 FROM repository_tags x WHERE x.html_url=r.html_url AND x.source='llm')", params_t)
    empty_count_t = query_one(f"SELECT COUNT(*) FROM repositories r {where_t} {'AND' if where_t else 'WHERE'} COALESCE(TRIM(r.description),'')='' AND r.language IS NULL AND r.stars=0 AND NOT EXISTS (SELECT 1 FROM repository_tags x WHERE x.html_url=r.html_url AND x.source='llm')", params_t)
    st.caption(f"Selected cohort: {tagged_count_t:,} LLM tagged; {empty_count_t:,} intentionally empty; {max(0, total_filtered-tagged_count_t-empty_count_t):,} awaiting tags.")

    col_tl, col_tr = st.columns(2)

    with col_tl:
        st.subheader("Top Tags")
        tf_sql_t = _tag_filter_sql("rt2.tag")
        tf_params_t = _tag_filter_params()
        df_top_tags = query_df(
            f"""SELECT rt2.tag, COUNT(DISTINCT r.html_url) as count
                FROM repositories r
                JOIN repository_tags rt2 ON r.html_url = rt2.html_url
                {tag_join_t.replace("rt", "rt_f") if sel_tags else ""}
                {where_t}
                {"AND" if where_t else "WHERE"} {tf_sql_t}
                GROUP BY rt2.tag HAVING count >= {MIN_TAG_REPOS} ORDER BY count DESC LIMIT 30""",
            params_t + tf_params_t,
        )
        if not df_top_tags.empty:
            fig = px.bar(
                df_top_tags, x="count", y="tag", orientation="h",
                color="count", color_continuous_scale="Purples",
            )
            fig.update_layout(yaxis=dict(autorange="reversed"), showlegend=False, height=600,
                              xaxis_title="Repositories", yaxis_title="", coloraxis_showscale=False)
            st.plotly_chart(fig, width="stretch")

    with col_tr:
        st.subheader("Tags by Year Created")
        st.caption("Repos tagged with each technology, by creation year — shows technology adoption over time.")

        # Pick top 10 tags for the chart
        if not df_top_tags.empty:
            top10_tags = df_top_tags.head(10)["tag"].tolist()
            ph = ",".join(["?"] * len(top10_tags))
            conds_ty, params_ty = build_where(base_table="r")
            conds_ty.append(f"rt3.tag IN ({ph})")
            params_ty.extend(top10_tags)
            conds_ty.append("r.created_at IS NOT NULL")
            w_ty = ("WHERE " + " AND ".join(conds_ty)) if conds_ty else ""
            df_tag_time = query_df(
                f"""SELECT SUBSTR(r.created_at,1,4) as year, rt3.tag, COUNT(DISTINCT r.html_url) as count
                    FROM repositories r JOIN repository_tags rt3 ON r.html_url = rt3.html_url
                    {w_ty}
                    GROUP BY year, rt3.tag ORDER BY year""",
                params_ty,
            )
            df_tag_time = df_tag_time[df_tag_time["year"].str.match(r"^\d{4}$", na=False)]
            if not df_tag_time.empty:
                fig = px.line(df_tag_time, x="year", y="count", color="tag",
                              labels={"year": "Year", "count": "Repos created", "tag": "Tag"})
                fig.update_layout(legend=dict(orientation="h", y=-0.3))
                st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader("Browse Repos by Tag")
    browse_tags = df_top_tags["tag"].tolist() if not df_top_tags.empty else tags
    if browse_tags:
        sel_tag = st.selectbox("Select a tag", browse_tags, key="tag_browse")
        sort_tag = st.selectbox("Sort by", ["stars", "pushed_at", "created_at"], key="tag_sort")
        conds_br, params_br = build_where(base_table="r")
        conds_br.append("rt_b.tag = ?")
        params_br.append(sel_tag)
        w_br = ("WHERE " + " AND ".join(conds_br)) if conds_br else ""
        df_tag_repos = query_df(
            f"""SELECT r.name, r.owner, r.country, r.language, r.stars, r.pushed_at, rt_b.confidence, r.html_url
               FROM repository_tags rt_b JOIN repositories r ON rt_b.html_url = r.html_url
               {w_br} ORDER BY r.{sort_tag} DESC LIMIT 200""",
            params_br,
        )
        tag_total = query_one(f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r JOIN repository_tags rt_b ON r.html_url=rt_b.html_url {w_br}", params_br)
        st.write(f"Displaying {len(df_tag_repos):,} of {tag_total:,} repos tagged with **{sel_tag}**")
        if not df_tag_repos.empty:
            st.dataframe(
                df_tag_repos,
                column_config={
                    "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                    "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1),
                    "stars": st.column_config.NumberColumn("⭐ Stars"),
                    "pushed_at": st.column_config.TextColumn("Last pushed"),
                },
                width="stretch",
                hide_index=True,
            )

    st.divider()
    st.subheader("Tag Groups")
    df_groups = query_df(f"SELECT DISTINCT g.id, g.name, g.description FROM tag_groups g JOIN tag_group_members gm ON gm.group_id=g.id JOIN repository_tags rt ON rt.tag=gm.tag JOIN repositories r ON r.html_url=rt.html_url {where_t} ORDER BY g.name", params_t)
    if not df_groups.empty:
        group_id = st.selectbox("Browse a tag group", df_groups['id'].tolist(),
                               format_func=lambda value: df_groups.set_index('id').loc[value, 'name'])
        members = query_df(f"SELECT DISTINCT gm.tag FROM tag_group_members gm JOIN repository_tags rt ON rt.tag=gm.tag JOIN repositories r ON r.html_url=rt.html_url {where_t} {'AND' if where_t else 'WHERE'} gm.group_id=? ORDER BY gm.tag", params_t + [int(group_id)])
        st.write(', '.join(members['tag'].tolist()))
    else:
        st.info("No tag groups in the selected cohort.")


# ==================== INSIGHTS ====================
with tab_insights:
    where_i, params_i, tag_join_i = build_tag_join_where()

    col_ia, col_ib = st.columns(2)

    with col_ia:
        st.subheader("⭐ Most Starred")
        df_top = query_df(
            f"""SELECT r.name, r.owner, r.country, r.stars, r.language, r.pushed_at, r.html_url
                FROM repositories r {tag_join_i} {where_i}
                GROUP BY r.html_url ORDER BY r.stars DESC LIMIT 25""",
            params_i,
        )
        st.dataframe(
            df_top,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
            },
            width="stretch",
            hide_index=True,
        )

    with col_ib:
        st.subheader("🚀 Rising Stars (active last 12m, sorted by stars)")
        rising_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conds_r, params_r = build_where(base_table="r")
        conds_r.append("r.pushed_at >= ?")
        params_r.append(rising_cutoff)
        tj_r = ""
        w_r = ("WHERE " + " AND ".join(conds_r)) if conds_r else ""
        df_rising = query_df(
            f"""SELECT r.name, r.owner, r.country, r.stars, r.language, r.pushed_at, r.html_url
                FROM repositories r {tj_r} {w_r}
                GROUP BY r.html_url ORDER BY r.stars DESC LIMIT 25""",
            params_r,
        )
        st.dataframe(
            df_rising,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
            },
            width="stretch",
            hide_index=True,
        )

    st.divider()

    st.subheader("📈 Most Active Organisations")
    st.caption("Organisations ranked by number of repos with a push in the last 12 months.")
    conds_ao, params_ao = build_where(base_table="r")
    conds_ao.append("r.pushed_at >= ?")
    params_ao.append((datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    tj_ao = ""
    w_ao = ("WHERE " + " AND ".join(conds_ao)) if conds_ao else ""
    df_active_orgs = query_df(
        f"""SELECT r.owner, r.country, COUNT(DISTINCT r.html_url) as active_repos,
                   SUM(r.stars) as total_stars
            FROM repositories r {tj_ao} {w_ao}
            GROUP BY r.owner ORDER BY active_repos DESC LIMIT 20""",
        params_ao,
    )
    col_org1, col_org2 = st.columns(2)
    with col_org1:
        if not df_active_orgs.empty:
            fig = px.bar(df_active_orgs, x="active_repos", y="owner", orientation="h",
                         color="active_repos", color_continuous_scale="Oranges",
                         labels={"active_repos": "Active repos (12m)", "owner": ""})
            fig.update_layout(yaxis=dict(autorange="reversed"), showlegend=False,
                              height=500, coloraxis_showscale=False)
            st.plotly_chart(fig, width="stretch")
    with col_org2:
        if not df_active_orgs.empty:
            st.dataframe(df_active_orgs, width="stretch", hide_index=True,
                         column_config={"total_stars": st.column_config.NumberColumn("⭐ Total stars"),
                                        "active_repos": st.column_config.NumberColumn("Active repos (12m)")})

    st.divider()

    col_ic, col_id = st.columns(2)

    with col_ic:
        st.subheader("📜 License Breakdown")
        df_lic = query_df(
            f"""SELECT r.license, COUNT(DISTINCT r.html_url) as count
                FROM repositories r {tag_join_i} {where_i}
                {"AND" if where_i else "WHERE"} r.license IS NOT NULL AND r.license != ''
                GROUP BY r.license ORDER BY count DESC""",
            params_i,
        )
        if not df_lic.empty:
            top_n = 10
            if len(df_lic) > top_n:
                top = df_lic.head(top_n)
                other = pd.DataFrame([{"license": "Other", "count": df_lic.iloc[top_n:]["count"].sum()}])
                df_lic_plot = pd.concat([top, other], ignore_index=True)
            else:
                df_lic_plot = df_lic
            fig = px.pie(df_lic_plot, names="license", values="count", hole=0.3)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, width="stretch")

    with col_id:
        st.subheader("🍴 Fork vs Original")
        fork_count = query_one(
            f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_i} {where_i} {'AND' if where_i else 'WHERE'} r.fork = 1",
            params_i,
        )
        original_count = query_one(
            f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_i} {where_i} {'AND' if where_i else 'WHERE'} (r.fork = 0 OR r.fork IS NULL)",
            params_i,
        )
        m1, m2 = st.columns(2)
        m1.metric("Original", f"{original_count:,}")
        m2.metric("Forked", f"{fork_count:,}")
        fig = px.pie(
            pd.DataFrame({"type": ["Original", "Fork"], "count": [original_count, fork_count]}),
            names="type", values="count", hole=0.4,
            color_discrete_sequence=["#2ecc71", "#e74c3c"],
        )
        st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader("🌍 Language × Country Heatmap")
    df_heat = query_df(
        f"""SELECT r.country, r.language, COUNT(DISTINCT r.html_url) as count
            FROM repositories r {tag_join_i} {where_i}
            {"AND" if where_i else "WHERE"} r.language IS NOT NULL AND r.language != ''

            GROUP BY r.country, r.language""",
        params_i,
    )
    if not df_heat.empty:
        pivot = df_heat.pivot_table(index="country", columns="language", values="count", fill_value=0)
        top_countries = pivot.sum(axis=1).nlargest(15).index
        top_languages = pivot.sum(axis=0).nlargest(12).index
        pivot = pivot.loc[top_countries, top_languages]
        fig = px.imshow(pivot, text_auto=True, color_continuous_scale="YlOrRd",
                        labels=dict(x="Language", y="Country", color="Repos"), aspect="auto")
        fig.update_layout(height=500)
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Not enough data for heatmap.")


# ==================== TRENDS ====================
with tab_trends:
    st.subheader("📈 Trends Over Time")
    st.caption("How government open source has evolved — new activity, rising tags, and shifting languages.")

    # ---- Period selector ----
    PERIOD_OPTIONS = {
        "This week":     7,
        "This month":    30,
        "Last 3 months": 90,
        "Last 6 months": 180,
        "Last 12 months": 365,
        "Last 2 years":  730,
    }
    period_label = st.radio(
        "Period", list(PERIOD_OPTIONS.keys()), index=4,
        horizontal=True, key="trends_period",
    )
    period_days = PERIOD_OPTIONS[period_label]
    period_prior_days = period_days * 2  # prior window = same length, shifted back

    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_recent = (now_utc - timedelta(days=period_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_prior  = (now_utc - timedelta(days=period_prior_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Granularity: week/month buckets depending on period
    if period_days <= 30:
        bucket_fmt = "%Y-%W"   # ISO week
        bucket_label = "Week"
    elif period_days <= 365:
        bucket_fmt = "%Y-%m"   # Month
        bucket_label = "Month"
    else:
        bucket_fmt = "%Y-%m"
        bucket_label = "Month"

    # For the repos chart, show the bucket pattern for the selected period
    # SQLite STRFTIME format
    if period_days <= 30:
        sql_bucket = "STRFTIME('%Y-%W', r.created_at)"
        bucket_re = r"^\d{4}-\d{2}$"
    else:
        sql_bucket = "SUBSTR(r.created_at, 1, 7)"
        bucket_re = r"^\d{4}-\d{2}$"

    st.divider()

    conds_base, params_base = build_where(base_table="r")
    w_base = (" AND ".join(conds_base)) if conds_base else ""
    and_base = ("AND " + w_base) if w_base else ""
    tf_sql_mom = _tag_filter_sql("rt.tag")
    tf_params_mom = _tag_filter_params()

    # ---- 1. New repos per period ----
    st.markdown(f"### 🗓️ New Repositories — {period_label}")

    conds_m, params_m = build_where(base_table="r")
    conds_m.append("r.created_at >= ?")
    params_m.append(cutoff_recent)
    w_m = ("WHERE " + " AND ".join(conds_m)) if conds_m else ""
    tj_m = ""

    df_activity = query_df(
        f"""SELECT {sql_bucket} as bucket, COUNT(DISTINCT r.html_url) as new_repos
            FROM repositories r {tj_m} {w_m}
            GROUP BY bucket ORDER BY bucket""",
        params_m,
    )
    df_activity = df_activity[df_activity["bucket"].str.match(bucket_re, na=False)]
    if not df_activity.empty:
        fig = px.bar(
            df_activity, x="bucket", y="new_repos",
            labels={"bucket": bucket_label, "new_repos": "New repositories"},
            color="new_repos", color_continuous_scale="Blues",
        )
        fig.update_layout(coloraxis_showscale=False, xaxis_title=bucket_label, yaxis_title="New repos")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Not enough data for this period.")

    # Fastest growing repos this period (by stars delta proxy: recently created + high stars)
    st.markdown(f"#### 🌟 Top New Repos — {period_label}")
    st.caption("Highest-starred repositories created in the selected period.")
    conds_nr, params_nr = build_where(base_table="r")
    conds_nr.append("r.created_at >= ?")
    params_nr.append(cutoff_recent)
    w_nr = ("WHERE " + " AND ".join(conds_nr)) if conds_nr else ""
    tj_nr = ""
    df_new_repos = query_df(
        f"""SELECT r.name, r.owner, r.country, r.language, r.stars, r.created_at, r.html_url
            FROM repositories r {tj_nr} {w_nr}
            GROUP BY r.html_url ORDER BY r.stars DESC LIMIT 20""",
        params_nr,
    )
    if not df_new_repos.empty:
        st.dataframe(
            df_new_repos,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "created_at": st.column_config.TextColumn("Created"),
            },
            width="stretch", hide_index=True,
        )
    else:
        st.info("No new repos in this period.")

    st.divider()

    # ---- 2. Tag momentum ----
    st.markdown(f"### 🚀 Tag Momentum — {period_label} vs prior {period_label.lower()}")
    st.caption(
        f"Tags ranked by growth — repos created in the selected period vs the equivalent period before it. "
        "Higher ratio = faster-growing category."
    )

    # Minimum repo count scales with period to avoid noise on short windows
    min_repos = max(2, period_days // 60)

    df_momentum = query_df(
        f"""
        SELECT
            rt.tag,
            COUNT(DISTINCT CASE WHEN r.created_at >= ? THEN r.html_url END) as recent,
            COUNT(DISTINCT CASE WHEN r.created_at >= ? AND r.created_at < ? THEN r.html_url END) as prior
        FROM repository_tags rt
        JOIN repositories r ON rt.html_url = r.html_url
        WHERE r.created_at IS NOT NULL AND {tf_sql_mom} {and_base}
        GROUP BY rt.tag
        HAVING recent >= {min_repos} AND prior >= {min_repos}
        ORDER BY (CAST(recent AS FLOAT) / prior) DESC
        LIMIT 30
        """,
        [cutoff_recent, cutoff_prior, cutoff_recent] + tf_params_mom + params_base,
    )

    if not df_momentum.empty:
        df_momentum["growth_ratio"] = (df_momentum["recent"] / df_momentum["prior"]).round(2)
        df_momentum["growth_pct"] = ((df_momentum["growth_ratio"] - 1) * 100).round(1)

        col_m1, col_m2 = st.columns(2)

        with col_m1:
            st.markdown(f"**Fastest growing tags**")
            fig = px.bar(
                df_momentum.head(20), x="growth_ratio", y="tag", orientation="h",
                color="growth_ratio", color_continuous_scale="Greens",
                labels={"growth_ratio": "Growth ratio (recent / prior)", "tag": "Tag"},
                hover_data={"recent": True, "prior": True, "growth_pct": True},
            )
            fig.update_layout(
                yaxis=dict(autorange="reversed"), height=550,
                coloraxis_showscale=False, xaxis_title="Growth ratio", yaxis_title="",
            )
            fig.add_vline(x=1.0, line_dash="dash", line_color="grey", annotation_text="no change")
            st.plotly_chart(fig, width="stretch")

        with col_m2:
            st.markdown(f"**Top tags by volume**")
            df_recent_top = query_df(
                f"""
                SELECT rt.tag, COUNT(DISTINCT r.html_url) as recent_count
                FROM repository_tags rt JOIN repositories r ON rt.html_url = r.html_url
                WHERE r.created_at >= ? AND {tf_sql_mom} {and_base}
                GROUP BY rt.tag ORDER BY recent_count DESC LIMIT 20
                """,
                [cutoff_recent] + tf_params_mom + params_base,
            )
            if not df_recent_top.empty:
                fig2 = px.bar(
                    df_recent_top, x="recent_count", y="tag", orientation="h",
                    color="recent_count", color_continuous_scale="Purples",
                    labels={"recent_count": f"Repos ({period_label.lower()})", "tag": "Tag"},
                )
                fig2.update_layout(
                    yaxis=dict(autorange="reversed"), height=550,
                    coloraxis_showscale=False,
                    xaxis_title=f"Repos ({period_label.lower()})", yaxis_title="",
                )
                st.plotly_chart(fig2, width="stretch")

        # Emerging tags table
        st.markdown("**Emerging tags** — ratio > 1.5")
        df_emerging = df_momentum[df_momentum["growth_ratio"] >= 1.5][
            ["tag", "recent", "prior", "growth_ratio", "growth_pct"]
        ].rename(columns={
            "tag": "Tag", "recent": period_label, "prior": f"Prior {period_label.lower()}",
            "growth_ratio": "Ratio", "growth_pct": "Growth %"
        })
        if not df_emerging.empty:
            st.dataframe(df_emerging, width="stretch", hide_index=True)
        else:
            st.info("No tags with >50% growth in this period.")
    else:
        st.info("Not enough tagged data for this period — try a longer window.")

    st.divider()

    # ---- 3. Language trends ----
    st.markdown("### 💻 Language Trends")
    st.caption("Year-over-year share of new repositories by primary language — top 10 languages.")

    df_lang_year = query_df(
        f"""
        SELECT SUBSTR(r.created_at, 1, 4) as year, r.language,
               COUNT(DISTINCT r.html_url) as count
        FROM repositories r
        WHERE r.language IS NOT NULL AND r.language != ''
          AND r.created_at IS NOT NULL

          AND SUBSTR(r.created_at, 1, 4) BETWEEN '2015' AND SUBSTR(?, 1, 4) {and_base}
        GROUP BY year, r.language ORDER BY year
        """,
        [now_str] + params_base,
    )
    df_lang_year = df_lang_year[df_lang_year["year"].str.match(r"^\d{4}$", na=False)]

    if not df_lang_year.empty:
        top_languages = df_lang_year.groupby('language')['count'].sum().nlargest(10).index
        df_lang_year = df_lang_year[df_lang_year['language'].isin(top_languages)]
        fig_lang = px.line(
            df_lang_year, x="year", y="count", color="language",
            labels={"year": "Year", "count": "New repositories", "language": "Language"},
            markers=True,
        )
        fig_lang.update_layout(legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig_lang, width="stretch")

        st.caption("As a share of all new repos that year (top 10 languages).")
        df_totals = df_lang_year.groupby("year")["count"].sum().reset_index().rename(columns={"count": "total"})
        df_share = df_lang_year.merge(df_totals, on="year")
        df_share["share"] = (df_share["count"] / df_share["total"] * 100).round(1)

        fig_share = px.area(
            df_share, x="year", y="share", color="language",
            labels={"year": "Year", "share": "Share of new repos (%)", "language": "Language"},
            groupnorm="",
        )
        fig_share.update_layout(legend=dict(orientation="h", y=-0.25), yaxis_title="Share (%)")
        st.plotly_chart(fig_share, width="stretch")
    else:
        st.info("Not enough data for language trends.")


# ==================== ABOUT ====================
with tab_about:
    st.subheader("About GovTech GitHub Explorer")
    st.write(
        """
        **GovTech GitHub Explorer** maps the global landscape of government open source software.
        It discovers, scrapes, and automatically categorises public GitHub repositories
        belonging to accounts in the government.github.com registry. Refreshes are scheduled weekly; actual freshness is shown below.
        """
    )

    st.subheader("How it works")
    col_a1, col_a2, col_a3, col_a4 = st.columns(4)
    with col_a1:
        st.markdown("### 🔍 Discover")
        st.write("Government GitHub accounts are sourced from the [government.github.com](https://github.com/github/government.github.com) registry, supplemented by accepted community submissions.")
    with col_a2:
        st.markdown("### 🕷️ Scrape")
        st.write("Repository metadata is collected via the GitHub API using a GitHub App installation, giving high-throughput authenticated access.")
    with col_a3:
        st.markdown("### 🏷️ Tag")
        st.write("An LLM pipeline (Qwen3-32B via OpenRouter) reads each repository's metadata (README and code are not fetched by batch tagging), then assigns structured tags and categories.")
    with col_a4:
        st.markdown("### 📊 Explore")
        st.write("Tags are clustered into groups using embedding similarity, and the full dataset is published to HuggingFace for anyone to use.")

    st.divider()

    st.subheader("Data")
    col_d1, col_d2, col_d3 = st.columns(3)
    total_a = total_filtered
    tagged_a = tagged_count_t
    tag_count_a = query_one(f"SELECT COUNT(DISTINCT rt.tag) FROM repository_tags rt JOIN repositories r ON r.html_url=rt.html_url {where}", params)
    col_d1.metric("Repositories", f"{total_a:,}")
    col_d2.metric("Tagged", f"{tagged_a:,}")
    col_d3.metric("Unique tags", f"{tag_count_a:,}")

    st.write(
        "The full dataset — including repo metadata, tags, and tag groups — is available on "
        "[HuggingFace](https://huggingface.co/datasets/AndreasThinks/government-github-repos) "
        "in CSV, Parquet, and SQLite formats. Sunday updates depend on successful scheduled runs."
    )

    st.divider()

    st.subheader("Contribute")
    st.write(
        "Know a government GitHub organisation that's missing from the dataset? "
        "Submit it via a pull request — it'll be included in the next weekly scrape."
    )
    st.markdown(
        "📋 **[How to submit a missing organisation](https://github.com/AndreasThinks/open-govtech-report/blob/main/CONTRIBUTING.md)**"
    )
    st.write(
        "The scraper, tagger, and dashboard are all open source. "
        "Issues and pull requests welcome."
    )
    st.markdown("[github.com/AndreasThinks/open-govtech-report](https://github.com/AndreasThinks/open-govtech-report)")

    st.divider()
    st.markdown(
        "✨ A project by [AndreasThinks](https://andreasthinks.me), built with ❤️ using Streamlit, "
        "and some ✨vibes✨",
        unsafe_allow_html=True,
    )


st.divider()
last_updated = get_last_updated_display(DB_PATH)
updated_str = f"Maximum repository scrape: {last_updated}" if last_updated else "Scrape time unavailable"
latest_iso = query_one("SELECT MAX(last_scraped) FROM repositories")
if not latest_iso or datetime.now(timezone.utc) - datetime.fromisoformat(latest_iso).replace(tzinfo=timezone.utc) > timedelta(days=14):
    st.warning("Repository data is stale or freshness is unknown (over 14 days).")
manifest_path = Path(DB_PATH).with_name('manifest.json')
if manifest_path.exists():
    try:
        manifest = json.loads(manifest_path.read_text())
        published = manifest.get('publication_timestamp')
        coverage = manifest.get('coverage', {})
        outcomes = coverage.get('outcomes', {})
        st.caption(f"Publication: {published or 'unknown'}. Refresh: {coverage.get('attempted', '?')} of {coverage.get('expected', '?')} accounts checked; "
                   f"{outcomes.get('succeeded', 0)} updated, {outcomes.get('unchanged', 0)} unchanged, "
                   f"{outcomes.get('not-found', 0)} not found, {outcomes.get('failed', 0)} failed.")
        if not published or datetime.now(timezone.utc) - datetime.fromisoformat(published).replace(tzinfo=timezone.utc) > timedelta(days=14):
            st.warning('Publication is stale or its date is unknown (over 14 days).')
        if not manifest.get('coverage', {}).get('complete'):
            st.warning('Last publication lacks complete refresh coverage.')
    except (ValueError, OSError):
        st.warning('Publication metadata unavailable.')
else:
    st.caption('Publication time unknown; a repository scrape timestamp is not a publication timestamp.')
    tables = query_df("SELECT name FROM sqlite_master WHERE type='table'")['name'].tolist()
    if 'scrape_runs' in tables:
        coverage = query_df('SELECT expected, attempted, complete, finished_at FROM scrape_runs ORDER BY started_at DESC LIMIT 1')
        st.dataframe(coverage, hide_index=True)
        if coverage.empty or not coverage.iloc[0]['complete']:
            st.warning('Latest refresh coverage is incomplete or unknown.')
    else:
        st.warning('Refresh coverage is unknown for this legacy database.')
st.caption(
    f"Data sourced from government GitHub accounts worldwide. {updated_str}. "
    "| [GitHub](https://github.com/AndreasThinks/open-govtech-report) "
    "| [Dataset](https://huggingface.co/datasets/AndreasThinks/government-github-repos) "
    "| [➕ Submit a missing org](https://github.com/AndreasThinks/open-govtech-report/blob/main/CONTRIBUTING.md) "
    "| ✨ A project by [AndreasThinks](https://andreasthinks.me)"
)
