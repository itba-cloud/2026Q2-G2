"""Pooled database access for the API.

A per-request `common.connect()` would call `database_port()` -- which hits
the RDS `describe_db_instances` API -- on every single request unless
`DB_PORT` is set. Opening one pool at startup means that lookup happens once,
and every request just checks a connection out.

Two pools, not one: reads go to the replica and everything else to the
primary. With `DB_HOST_RO` unset -- Floci, docker compose, any local run --
the read pool dials the primary host instead, so nothing has to change to
develop without a replica. It stays a separate pool either way; see
open_pools() for why that matters.

    from db import get_connection
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from common import SETTINGS, database_password, database_port

log = logging.getLogger("api")

_pool: ConnectionPool | None = None      # primary: writes, and the admin work
_ro_pool: ConnectionPool | None = None   # replica: the read endpoints


def _conninfo(host: str, port: int) -> str:
    # No `options='-c work_mem=...'` here: RDS Proxy refuses startup options.
    # The comparison's work_mem lives in the instance's parameter group.
    return (f"host={host} port={port} dbname={SETTINGS.db_name} "
            f"user={SETTINGS.db_user} password={database_password(SETTINGS)}")


def open_pools() -> None:
    """Resolve the RDS port and open the pools. Called by the first
    get_connection(), not at import time: a cold start must never dial out,
    and GET /health -- which never touches the pool -- has to be able to
    answer before RDS is even reachable.
    """
    global _pool, _ro_pool
    if _pool is not None:
        return
    port = database_port(SETTINGS)
    _pool = ConnectionPool(
        _conninfo(SETTINGS.db_host, port),
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1, max_size=10)

    # Always its own pool, even with no replica to point it at -- in that case
    # it just dials the primary host.
    #
    # Making it `_pool` itself when DB_HOST_RO is unset would have been less
    # code and a latent deadlock: a request that holds a read connection and
    # then asks for a primary one (which the tenancy-guarded endpoints do, see
    # main.py) would be taking two connections out of one ten-connection pool.
    # Ten such requests at once and every one of them waits for a connection
    # another is holding. Two pools cannot do that to each other, and it also
    # makes a local run structurally the same as AWS instead of a special case
    # where that whole code path goes untested.
    ro_host = os.getenv("DB_HOST_RO") or SETTINGS.db_host
    _ro_pool = ConnectionPool(
        _conninfo(ro_host, port),
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1, max_size=10)
    log.info("pools opened on port %d (reads on %s%s)", port, ro_host,
             "" if os.getenv("DB_HOST_RO") else ", the primary -- no DB_HOST_RO set")


def close_pools() -> None:
    global _pool, _ro_pool
    if _ro_pool is not None:
        _ro_pool.close()
    _ro_pool = None
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection(readonly: bool = False,
                   timeout: float | None = None) -> Iterator[Any]:
    """A connection from the replica pool when `readonly`, the primary
    otherwise. `readonly` is about which instance answers, not about a
    transaction mode -- nothing here sets one. `timeout` is how long to wait
    for one, the pool's default (30 s) when None."""
    if _pool is None:
        open_pools()
    pool = _ro_pool if readonly else _pool
    assert pool is not None
    with pool.connection(timeout=timeout) as connection:
        yield connection
