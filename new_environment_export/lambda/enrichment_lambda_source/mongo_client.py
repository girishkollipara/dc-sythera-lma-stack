"""Minimal DocumentDB/MongoDB connector for the enrichment Lambda.

Self-contained copy — this Lambda is deployed as its own bundle and cannot import
the main `com.dimcon.synthera` package. It mirrors exactly how the main app's
`utilities/connection.py` builds its URI (retryWrites=false, authSource=<user>),
so it talks to the same database the same way.

The client is created once per container and reused across invocations.

Required environment variables:
    DOCDB_HOST, DOCDB_PORT, DOCDB_USER, DOCDB_PASS, DOCDB_DB_NAME

Note: reaching the database requires this Lambda to run in the same VPC/subnets
with network access to DOCDB_HOST (same as the other Mongo-connected Lambdas).
"""

import os
import urllib.parse

from pymongo import MongoClient

_client = None
_db = None


def get_db():
    """Return the configured database, connecting once per container."""
    global _client, _db
    if _db is not None:
        return _db

    user    = os.environ["DOCDB_USER"]
    password = os.environ["DOCDB_PASS"]
    host    = os.environ["DOCDB_HOST"]
    port    = os.environ["DOCDB_PORT"]
    db_name = os.environ["DOCDB_DB_NAME"]

    safe_user = urllib.parse.quote_plus(user)
    safe_pass = urllib.parse.quote_plus(password)

    uri = (
        f"mongodb://{safe_user}:{safe_pass}"
        f"@{host}:{port}/"
        f"?retryWrites=false"
        f"&authSource={user}"
    )

    _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    _client.admin.command("ping")   # fail fast if unreachable
    _db = _client[db_name]
    print(f"mongo: connected — host={host}:{port} db={db_name}")
    return _db


def get_collection(name):
    """Return a collection from the configured database."""
    return get_db()[name]
