from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Session counters
sessions_created = Counter(
    "agent_sessions_created_total",
    "Total number of agent sessions created"
)

sessions_completed = Counter(
    "agent_sessions_completed_total",
    "Total number of agent sessions completed",
    ["status"]  # completed | failed
)

# Tool usage
tool_calls_total = Counter(
    "agent_tool_calls_total",
    "Total number of tool calls made by agents",
    ["tool_name"]
)

# Latency
session_duration_seconds = Histogram(
    "agent_session_duration_seconds",
    "Time taken to complete an agent session",
    buckets=[1, 5, 10, 30, 60, 120, 300]
)

# Active sessions
active_sessions = Gauge(
    "agent_active_sessions",
    "Number of currently running agent sessions"
)


def get_metrics():
    return generate_latest(), CONTENT_TYPE_LATEST
