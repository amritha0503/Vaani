"""Working set in memory, audit trail on disk.

Forty calls do not need a database server. What DOES need to be durable is the
audit log -- it is the accountability claim, and a judge may ask to see it.
sqlite3 ships with Python, so this costs nothing.
"""
import json
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).with_name("vaani.db")

CALLS: dict[str, dict] = {}          # the working set, id -> call


def init() -> None:
    with sqlite3.connect(DB) as c:
        c.execute("""create table if not exists audit_log (
            id      integer primary key autoincrement,
            ts      real    not null,
            call_id text,
            action  text    not null,   -- ranked | overridden | dispatched | degraded
            actor   text    not null,   -- system | operator:<id>
            payload text    not null    -- the feature values behind the decision
        )""")


def log(action: str, actor: str = "system", call_id: str | None = None, **payload) -> None:
    with sqlite3.connect(DB) as c:
        c.execute("insert into audit_log (ts, call_id, action, actor, payload) values (?,?,?,?,?)",
                  (time.time(), call_id, action, actor, json.dumps(payload, default=str)))


def audit_rows(limit: int = 500) -> list[dict]:
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("select * from audit_log order by id desc limit ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def reset() -> None:
    CALLS.clear()
    with sqlite3.connect(DB) as c:
        c.execute("delete from audit_log")
