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
            Td(str(repo['commit_count'] or 0)),
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
                    'commit_count': 'sum'
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
            Td(str(stats['total_commits'] or 0)),
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

    stats_cards = [
        stats_card("Total Repositories", stats['total_repos']),
        stats_card("Total Stars", stats['total_stars']),
        stats_card("Total Forks", stats['total_forks']),
        stats_card("Average Stars", stats['avg_stars']),
        stats_card("Average Forks", stats['avg_forks'])
    ]

    # Create country filter options
    country_options = [
        Option(
            country,
            value=country,
            selected=(selected_countries is None or country in selected_countries)
        ) for country in sorted(set(stats['countries']))
    ]

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
                        Select(
                            *country_options,
                            name="countries",
                            id="countries",
                            multiple=True,
                            hx_get="/",
                            hx_target="main",
                            style="height: 100px"  # Make the multi-select box taller
                        )
                    )
                ),
                Div(
                    Img(src="https://htmx.org/img/bars.svg", cls="htmx-indicator"),
                    style="text-align: center"
                )
            )
        ),
        Section(*stats_cards, cls="grid"),
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
def index(request):
    # Get query parameters with defaults
    params = dict(request.query_params)
    try:
        days = int(params.get('days', ['30'])[0])
        min_stars = int(params.get('min_stars', ['0'])[0])
        min_days = int(params.get('min_days', ['0'])[0])
        selected_countries = params.get('countries', [])  # List of selected countries
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

@rt('/update-repos')
def update_repos(request):
    """HTMX endpoint for updating just the repository table"""
    params = dict(request.query_params)
    try:
        # Handle empty or missing parameters gracefully
        days_param = params.get('days', ['30'])
        min_stars_param = params.get('min_stars', ['0'])
        min_days_param = params.get('min_days', ['0'])
        sort_metric_param = params.get('sort_metric', ['stars'])
        
        days = int(days_param[0] if days_param and days_param[0] else 30)
        min_stars = int(min_stars_param[0] if min_stars_param and min_stars_param[0] else 0)
        min_days = int(min_days_param[0] if min_days_param and min_days_param[0] else 0)
        sort_by = sort_metric_param[0] if sort_metric_param else 'stars'
        selected_countries = params.get('countries', [])
        
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
                    
        return repo_table(top_repos, sort_by=sort_by, min_stars=min_stars, min_days=min_days)
    except Exception as e:
        debug_log(f"Error in update_repos route: {str(e)}")
        import traceback
        debug_log(traceback.format_exc())
        return error_handler(request, e)

serve()
