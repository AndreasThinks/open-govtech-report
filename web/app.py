from fasthtml.common import *
from datetime import datetime, timedelta
import pandas as pd
import sys
import os

# Add parent directory to path to import db_operations
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)
from db_operations import DatabaseManager

def error_handler(req, exc):
    error_msg = str(exc)
    if "must be non-negative" in error_msg:
        error_msg = "All filter values must be non-negative numbers"
    elif "No data available" in error_msg:
        error_msg = f"No data available for the selected time period. Try increasing the date range."
    
    return Titled("Data Not Available",
        Card(
            P(error_msg, cls="error"),
            footer=A("Return to Dashboard", href="/", role="button")
        )
    )

app, rt = fast_app(
    exception_handlers={404: error_handler, 500: error_handler},
    htmx=True,
    hdrs=(
        Style("""
            .htmx-indicator { opacity: 0; transition: opacity 500ms ease-in; }
            .htmx-request .htmx-indicator { opacity: 1; }
            .htmx-request.htmx-indicator { opacity: 1; }
            .filter-control { margin-bottom: 1rem; }
            .filter-control label { display: block; margin-bottom: 0.5rem; }
            .grid { 
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1rem;
                margin-bottom: 1rem;
            }
            .grid > div {
                min-width: 0;
            }
            /* Table styles */
            table {
                width: 100%;
                border-collapse: collapse;
            }
            th, td {
                padding: 0.75rem;
                text-align: left;
                border-bottom: 1px solid var(--pico-form-element-border-color);
            }
            th {
                background: var(--pico-background-color);
                font-weight: bold;
            }
            td {
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            /* Column widths */
            table th:nth-child(1), table td:nth-child(1) { width: 25%; } /* Repository */
            table th:nth-child(2), table td:nth-child(2) { width: 20%; } /* Owner */
            table th:nth-child(3), table td:nth-child(3) { width: 10%; } /* Stars */
            table th:nth-child(4), table td:nth-child(4) { width: 10%; } /* Forks */
            table th:nth-child(5), table td:nth-child(5) { width: 10%; } /* Watchers */
            table th:nth-child(6), table td:nth-child(6) { width: 10%; } /* Commits */
            table th:nth-child(7), table td:nth-child(7) { width: 10%; } /* Language */
            table th:nth-child(8), table td:nth-child(8) { width: 5%; }  /* Size */
        """),
    )
)

# Initialize DatabaseManager with root directory path
os.chdir(root_dir)  # Change working directory to root to ensure correct DB path
print(f"Current working directory: {os.getcwd()}")
print(f"Database path: {os.path.join(os.getcwd(), 'government_repos.db')}")
print(f"Database exists: {os.path.exists('government_repos.db')}")

try:
    db = DatabaseManager()
    print("DatabaseManager initialized successfully")
    test_df = db.get_latest_snapshot()
    print(f"Successfully retrieved {len(test_df)} rows from database")
except Exception as e:
    print(f"Error initializing DatabaseManager: {str(e)}")
    import traceback
    print(traceback.format_exc())
    raise

# Add debug logging
def debug_log(msg):
    print(f"[DEBUG] {msg}")

def stats_card(title, value):
    return Card(
        H3(title),
        P(str(value), cls="stat-value")
    )

def top_list(title, items):
    rows = [Tr(Td(name), Td(str(count))) for name, count in items.items()]
    return Card(
        H2(title),
        Table(
            Thead(Tr(Th("Name"), Th("Count"))),
            Tbody(*rows)
        )
    )

def repo_table(repos, sort_by='stars', min_stars=0, min_days=0):
    # Sort and filter repos
    filtered_repos = [r for r in repos if r['stars'] >= min_stars]
    if min_days > 0:
        cutoff = (datetime.utcnow() - timedelta(days=min_days)).isoformat()
        filtered_repos = [r for r in filtered_repos if r['created_at'] >= cutoff]
    
    sorted_repos = sorted(filtered_repos, key=lambda x: x[sort_by], reverse=True)
    
    # Create table rows
    rows = [
        Tr(
            Td(A(repo['name'], href=repo['html_url'], target="_blank")),
            Td(repo['username']),
            Td(str(repo['stars'])),
            Td(str(repo['forks'])),
            Td(str(repo['watchers'])),
            Td(str(int(repo['commit_count'] if pd.notna(repo['commit_count']) else 0))),
            Td(repo['language'] or 'N/A'),
            Td(str(repo['size_kb']))
        ) for repo in sorted_repos
    ]

    # Create sort controls
    metrics = [('stars', 'Stars'), ('forks', 'Forks'), ('watchers', 'Watchers'), ('commit_count', 'Commits'), ('size_kb', 'Size (KB)')]
    sort_controls = [
        Div(
            Input(
                type="radio",
                name="sort_metric",
                value=value,
                checked=(value == sort_by),
                hx_get="/update-repos",
                hx_trigger="change",
                hx_target="#repos-table",
                hx_include="[name='days'],[name='min_stars'],[name='min_days'],[name='countries']"
            ),
            Label(label),
            cls="metric-option"
        ) for value, label in metrics
    ]

    # Create filter controls
    filter_controls = [
        Div(
            Label("Minimum Stars"),
            Input(
                type="number",
                name="min_stars",
                value=min_stars,
                min="0",
                title="Show only repositories with at least this many stars",
                hx_get="/update-repos",
                hx_trigger="change",
                hx_target="#repos-table",
                hx_include="[name='sort_metric'],[name='days'],[name='min_days'],[name='countries']"
            ),
            cls="filter-control"
        ),
        Div(
            Label("Created within last N days"),
            Input(
                type="number",
                name="min_days",
                value=min_days,
                min="0",
                title="Show only repositories created within this many days (0 for all time)",
                hx_get="/update-repos",
                hx_trigger="change",
                hx_target="#repos-table",
                hx_include="[name='sort_metric'],[name='days'],[name='min_stars'],[name='countries']"
            ),
            cls="filter-control"
        )
    ]

    return Card(
        H2("Top Repositories"),
        Div(*sort_controls, cls="metrics-selector"),
        Div(*filter_controls, cls="filter-controls"),
        Div(Img(src="https://htmx.org/img/bars.svg", cls="htmx-indicator"), style="text-align: center"),
        Table(
            Thead(
                Tr(
                    Th("Repository"),
                    Th("Owner"),
                    Th("Stars"),
                    Th("Forks"),
                    Th("Watchers"),
                    Th("Commits"),
                    Th("Language"),
                    Th("Size (KB)")
                )
            ),
            Tbody(*rows),
            id="repos-table"
        )
    )

def create_orgs_table(filtered_df):
    """Create a table showing most active GitHub organizations."""
    org_stats = (filtered_df.groupby('username')
                .agg({
                    'name': 'count',
                    'stars': 'sum',
                    'forks': 'sum',
                    'size_kb': 'sum',
                    'commit_count': lambda x: x.fillna(0).astype(int).sum()
                })
                .rename(columns={'name': 'repos', 'size_kb': 'total_size_kb', 'commit_count': 'total_commits'})
                .sort_values('repos', ascending=False)
                .head(10))
    
    rows = [
        Tr(
            Td(A(username, href=f"https://github.com/{username}", target="_blank")),
            Td(str(stats['repos'])),
            Td(str(stats['stars'])),
            Td(str(stats['forks'])),
            Td(str(int(stats['total_commits'] if pd.notna(stats['total_commits']) else 0))),
            Td(str(stats['total_size_kb']))
        ) for username, stats in org_stats.iterrows()
    ]
    
    return Card(
        H2("Most Active Organizations"),
        Table(
            Thead(
                Tr(
                    Th("Organization"),
                    Th("Repositories"),
                    Th("Total Stars"),
                    Th("Total Forks"),
                    Th("Total Commits"),
                    Th("Total Size (KB)")
                )
            ),
            Tbody(*rows)
        )
    )

def create_dashboard(stats, top_languages, top_repos, top_countries, top_topics, current_days, filtered_df, min_stars=0, min_days=0, selected_countries=None):
    days_options = [
        Option(
            f"{d} days" if d < 365 else "1 year",
            value=str(d),
            selected=(d == current_days)
        ) for d in [7, 30, 90, 180, 365]
    ]

    # Wrap each stats card in a div to maintain grid structure
    stats_cards = [
        Div(stats_card("Total Repositories", stats['total_repos'])),
        Div(stats_card("Total Stars", stats['total_stars'])),
        Div(stats_card("Total Forks", stats['total_forks'])),
        Div(stats_card("Average Stars", stats['avg_stars'])),
        Div(stats_card("Average Forks", stats['avg_forks']))
    ]

    # Create country filter options - all selected by default
    country_options = []
    all_countries = sorted(set(stats['countries']))
    for country in all_countries:
        # If no countries are selected yet, select all. Otherwise check if country is selected
        is_selected = selected_countries is None or not selected_countries or country in selected_countries
        country_options.append(Option(country, value=country, selected=is_selected))

    return Main(
        Header(
            Form(
                Grid(
                    Div(
                        Label("Show data for last:"),
                        Select(
                            *days_options,
                            name="days",
                            id="days",
                            hx_get="/",
                            hx_target="main"
                        )
                    ),
                    Div(
                        Label("Filter by countries:"),
                        Details(
                            Summary("Select countries"),
                            Group(
                                Div(
                                    Button("Select all", 
                                        hx_post="/update-filter",
                                        hx_target="#filter-results, #repos-table",
                                        hx_include="[name='days']",
                                        hx_vals='{"select_all": true}',
                                        cls="secondary outline",
                                        style="margin-right: 0.5rem;"
                                    ),
                                    Button("Deselect all", 
                                        hx_post="/update-filter",
                                        hx_target="#filter-results, #repos-table",
                                        hx_include="[name='days']",
                                        hx_vals='{"deselect_all": true}',
                                        cls="secondary outline"
                                    ),
                                    style="margin: 0.5rem 0 1rem; text-align: center; padding: 0.5rem; border-bottom: 1px solid var(--pico-form-element-border-color);"
                                ),
                                Div(
                                    *[Div(
                                        CheckboxX(
                                            checked=is_selected,
                                            label=country,
                                            value=country,
                                            name="countries",
                                            hx_post="/update-filter",
                                            hx_target="#filter-results, #repos-table",
                                            hx_trigger="change"
                                        ),
                                        style="padding: 0.4rem 0.2rem;"
                                    ) for country, is_selected in [(c, selected_countries is None or not selected_countries or c in selected_countries) for c in sorted(set(stats['countries']))]],
                                    style="max-height: 300px; overflow-y: auto; padding: 0.5rem;"
                                )
                            ),
                            role="list",
                            style="position: relative; margin: 0;"
                        ),
                        style="position: relative;"
                    ),
                    Style("""
                        details[role="list"] summary + * { 
                            position: absolute;
                            width: 100%;
                            z-index: 1000;
                            background: var(--pico-background-color);
                            border: 1px solid var(--pico-form-element-border-color);
                            border-radius: var(--pico-border-radius);
                            box-shadow: var(--pico-card-box-shadow);
                        }
                        details[role="list"] summary {
                            padding: 0.75rem;
                            border-radius: var(--pico-border-radius);
                            background: var(--pico-background-color);
                            border: 1px solid var(--pico-form-element-border-color);
                            cursor: pointer;
                            transition: border-color 0.2s ease;
                        }
                        details[role="list"] summary:hover {
                            border-color: var(--pico-form-element-active-border-color);
                        }
                        details[role="list"] [type="checkbox"] {
                            margin: 0 0.75rem 0 0;
                            cursor: pointer;
                        }
                        details[role="list"] [type="checkbox"] + label {
                            display: inline-block;
                            margin: 0;
                            cursor: pointer;
                            user-select: none;
                        }
                        details[role="list"] [type="checkbox"]:hover + label {
                            color: var(--pico-form-element-active-border-color);
                        }
                        details[role="list"] button {
                            padding: 0.4rem 0.8rem;
                            font-size: 0.9rem;
                        }
                    """)
                ),
                Div(
                    Img(src="https://htmx.org/img/bars.svg", cls="htmx-indicator"),
                    style="text-align: center"
                )
            )
        ),
        Section(Grid(*stats_cards), id="filter-results"),
        repo_table(top_repos, min_stars=min_stars, min_days=min_days),
        Grid(
            top_list("Top Languages", top_languages),
            top_list("Most Active Countries", top_countries),
            top_list("Top Topics", top_topics)
        ),
        create_orgs_table(filtered_df),
        Footer(
            Small(f"Data updated as of {stats['latest_update']}")
        ),
        cls="container"
    )

@rt('/')
async def index(request):
    # Get query parameters with defaults
    params = request.query_params
    try:
        days = int(params.get('days', '30'))
        min_stars = int(params.get('min_stars') or '0')  # Handle empty string
        min_days = int(params.get('min_days') or '0')    # Handle empty string
        # Handle countries parameter - ensure it's always a list
        selected_countries = []
        if request.method == "POST":
            form = await request.form()
            selected_countries = form.getlist('countries')
        if days <= 0 or min_stars < 0 or min_days < 0:
            raise ValueError("Parameters must be non-negative")
    except ValueError as e:
        return error_handler(request, e)
    
    try:
        debug_log("Getting latest snapshot...")
        try:
            df = db.get_latest_snapshot()
            debug_log(f"Got snapshot with {len(df)} rows")
            debug_log(f"Columns in df: {df.columns.tolist()}")
            debug_log(f"First row: {df.iloc[0].to_dict() if not df.empty else 'No data'}")
        except Exception as e:
            debug_log(f"Error getting snapshot: {str(e)}")
            raise
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        debug_log(f"Converting timestamp column...")
        df['scrape_timestamp'] = pd.to_datetime(df['scrape_timestamp'])
        debug_log(f"Filtering data after {cutoff_date}")
        filtered_df = df[df['scrape_timestamp'] >= cutoff_date]
        if selected_countries:
            filtered_df = filtered_df[filtered_df['country'].isin(selected_countries)]
        debug_log(f"Filtered to {len(filtered_df)} rows")
        
        if filtered_df.empty:
            debug_log("No data after filtering")
            raise ValueError(f"No data available for the last {days} days")
        else:
            debug_log(f"Data available: {len(filtered_df)} rows")
    
        stats = {
            'total_repos': len(filtered_df),
            'total_stars': filtered_df['stars'].sum(),
            'total_forks': filtered_df['forks'].sum(),
            'avg_stars': round(filtered_df['stars'].mean(), 2),
            'avg_forks': round(filtered_df['forks'].mean(), 2),
            'countries': df['country'].unique()  # Add list of all countries for filter
        }
    
        top_languages = (filtered_df['language']
                        .value_counts()
                        .head(10)
                        .to_dict())
        
        top_repos = (filtered_df.nlargest(10, 'stars')
                    [['name', 'username', 'stars', 'forks', 'watchers', 'commit_count', 'language', 'html_url', 'created_at', 'size_kb']]
                    .to_dict('records'))
        
        # Get top topics
        def extract_topics(topics_str):
            try:
                if pd.isna(topics_str) or topics_str in ('[]', 'NaN', '"NaN"'):
                    return []
                return eval(topics_str)  # Safe since we know it's a JSON array from our database
            except:
                return []
        
        all_topics = []
        for topics in filtered_df['topics']:
            all_topics.extend(extract_topics(topics))
        
        top_topics = (pd.Series(all_topics)
                     .value_counts()
                     .head(10)
                     .to_dict())
        
        top_countries = (filtered_df['country']
                        .value_counts()
                        .head(10)
                        .to_dict())
        
        latest_timestamp = filtered_df['scrape_timestamp'].max()
        stats['latest_update'] = latest_timestamp.strftime('%Y-%m-%d %H:%M UTC')

        return Titled(
            "Open GovTech Report",
            create_dashboard(stats, top_languages, top_repos, top_countries, top_topics, days, filtered_df, min_stars, min_days, selected_countries)
        )
    except Exception as e:
        debug_log(f"Error in index route: {str(e)}")
        import traceback
        debug_log(traceback.format_exc())
        return error_handler(request, e)

@rt('/update-filter')
async def post(request):
    """HTMX endpoint for updating the filtered results"""
    try:
        days = int(request.query_params.get('days', '30'))
        form = await request.form()
        
        # Handle select/deselect all
        if 'select_all' in form:
            df = db.get_latest_snapshot()
            selected_countries = sorted(df['country'].unique().tolist())
        elif 'deselect_all' in form:
            selected_countries = []
        else:
            selected_countries = form.getlist('countries')  # Get from form data
        
        df = db.get_latest_snapshot()
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        df['scrape_timestamp'] = pd.to_datetime(df['scrape_timestamp'])
        filtered_df = df[df['scrape_timestamp'] >= cutoff_date]
        if selected_countries:
            filtered_df = filtered_df[filtered_df['country'].isin(selected_countries)]
        
        if filtered_df.empty:
            raise ValueError(f"No data available for the last {days} days")
        
        stats = {
            'total_repos': len(filtered_df),
            'total_stars': filtered_df['stars'].sum(),
            'total_forks': filtered_df['forks'].sum(),
            'avg_stars': round(filtered_df['stars'].mean(), 2),
            'avg_forks': round(filtered_df['forks'].mean(), 2),
            'countries': df['country'].unique()
        }
        
        # Wrap each stats card in a div to maintain grid structure
        stats_cards = [
            Div(stats_card("Total Repositories", stats['total_repos'])),
            Div(stats_card("Total Stars", stats['total_stars'])),
            Div(stats_card("Total Forks", stats['total_forks'])),
            Div(stats_card("Average Stars", stats['avg_stars'])),
            Div(stats_card("Average Forks", stats['avg_forks']))
        ]
        
        # Also get the filtered repos for the table update
        top_repos = (filtered_df.nlargest(10, 'stars')
                    [['name', 'username', 'stars', 'forks', 'watchers', 'commit_count', 'language', 'html_url', 'created_at', 'size_kb']]
                    .to_dict('records'))
        
        return (
            Section(Grid(*stats_cards), id="filter-results"),
            repo_table(top_repos, sort_by='stars', min_stars=0, min_days=0)
        )
    except Exception as e:
        debug_log(f"Error in update_filter route: {str(e)}")
        import traceback
        debug_log(traceback.format_exc())
        return error_handler(request, e)

@rt('/update-repos')
async def update_repos(request):
    """HTMX endpoint for updating just the repository table"""
    params = request.query_params
    try:
        # Handle empty or missing parameters gracefully
        days = int(params.get('days', '30'))
        min_stars = int(params.get('min_stars') or '0')  # Handle empty string
        min_days = int(params.get('min_days') or '0')    # Handle empty string
        sort_by = params.get('sort_metric', 'stars')
        # Handle countries parameter - ensure it's always a list
        form = await request.form()
        selected_countries = form.getlist('countries')  # Get from form data
        
        df = db.get_latest_snapshot()
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        df['scrape_timestamp'] = pd.to_datetime(df['scrape_timestamp'])
        filtered_df = df[df['scrape_timestamp'] >= cutoff_date]
        if selected_countries:
            filtered_df = filtered_df[filtered_df['country'].isin(selected_countries)]
        
        if filtered_df.empty:
            raise ValueError(f"No data available for the last {days} days")
            
        top_repos = (filtered_df.nlargest(10, sort_by)
                    [['name', 'username', 'stars', 'forks', 'watchers', 'commit_count', 'language', 'html_url', 'created_at', 'size_kb']]
                    .to_dict('records'))
        # Only return the table itself, not the whole card with heading and controls
        rows = [
            Tr(
                Td(A(repo['name'], href=repo['html_url'], target="_blank")),
                Td(repo['username']),
                Td(str(repo['stars'])),
                Td(str(repo['forks'])),
                Td(str(repo['watchers'])),
                Td(str(int(repo['commit_count'] if pd.notna(repo['commit_count']) else 0))),
                Td(repo['language'] or 'N/A'),
                Td(str(repo['size_kb']))
            ) for repo in top_repos
        ]
                    
        return Table(
            Thead(
                Tr(
                    Th("Repository"),
                    Th("Owner"),
                    Th("Stars"),
                    Th("Forks"),
                    Th("Watchers"),
                    Th("Commits"),
                    Th("Language"),
                    Th("Size (KB)")
                )
            ),
            Tbody(*rows),
            id="repos-table"
        )
    except Exception as e:
        debug_log(f"Error in update_repos route: {str(e)}")
        import traceback
        debug_log(traceback.format_exc())
        return error_handler(request, e)

serve()
