"""Optional read-only stdio MCP interface. Never exposes browser credentials."""
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from reminder.store import Store, timestamp

store = Store(Path(__file__).resolve().parent / 'data' / 'reminder.db')
mcp = FastMCP('Canvas DDL Cache')


@mcp.tool()
def list_deadlines(days: int = 14, include_completed: bool = False, course: str = '') -> dict:
    """Read locally cached assignments due in the next N days, plus overdue and undated tasks.

    Does not log into Canvas or refresh data. Always report last_sync to the user.
    """
    if not 1 <= days <= 366:
        raise ValueError('days must be between 1 and 366')
    now = time.time()
    tasks = []
    for task in store.tasks():
        if task['done'] and not include_completed:
            continue
        if course.casefold() not in task['course'].casefold():
            continue
        due = timestamp(task['due'])
        if due is not None and due > now + days * 86400:
            continue
        tasks.append({k: task[k] for k in ('id', 'course', 'title', 'url', 'due', 'done', 'changed')}
                     | {'remaining_seconds': round(due - now) if due is not None else None})
    last = store.get('last_sync')
    return {'last_sync': last, 'stale': not last or now - timestamp(last) > 86400, 'tasks': tasks}


@mcp.tool()
def cache_status() -> dict:
    """Read cache freshness and task count; does not access Canvas or authentication data."""
    last = store.get('last_sync')
    return {'last_sync': last, 'stale': not last or time.time() - timestamp(last) > 86400,
            'task_count': len(store.tasks()), 'dashboard': 'http://127.0.0.1:8765'}


if __name__ == '__main__':
    mcp.run(transport='stdio')
