"""
db.py
-----
Connection helper for Azure SQL Database.

Uses pyodbc with the ODBC Driver 18 for SQL Server. Supports two auth modes,
controlled by env vars (set in Function App -> Configuration):

1. SQL auth (dev/test):        SQL_CONNECTION_STRING
2. Managed Identity (prod):     SQL_SERVER, SQL_DATABASE, USE_MANAGED_IDENTITY=true

Managed Identity is the recommended approach in production so no SQL
username/password needs to be stored anywhere.
"""

import os
import pyodbc
import struct
from azure.identity import DefaultAzureCredential

_SQL_COPT_SS_ACCESS_TOKEN = 1256  # pyodbc-specific attribute for AAD token auth


def _get_connection_string() -> str:
    conn_str = os.environ.get("SQL_CONNECTION_STRING")
    if conn_str:
        return conn_str

    server = os.environ["SQL_SERVER"]        # e.g. feemgmt-sql.database.windows.net
    database = os.environ["SQL_DATABASE"]    # e.g. FeeManagementDB
    return (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def get_connection() -> pyodbc.Connection:
    """Returns a live pyodbc connection, using Managed Identity when configured."""
    use_mi = os.environ.get("USE_MANAGED_IDENTITY", "false").lower() == "true"
    conn_str = _get_connection_string()

    if use_mi:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://database.windows.net/.default")
        token_bytes = token.token.encode("utf-16-le")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})

    return pyodbc.connect(conn_str)


def row_to_dict(cursor, row) -> dict:
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))
