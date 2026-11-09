import os
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper
from django.db.models import Lookup, Transform
from django.db.models.fields import DateTimeField, Field

# Define valores padrão para variáveis de ambiente obrigatórias durante a execução de testes
os.environ.setdefault("SECRET_KEY", "test-secret-key-salao-conecta-local")
os.environ.setdefault("SYSTEM_NAME", "Salão Conecta (Test)")
os.environ.setdefault("DATABASE_ENGINE", "django.db.backends.sqlite3")
os.environ.setdefault("DATABASE_NAME", ":memory:")
os.environ.setdefault("DATABASE_USER", "")
os.environ.setdefault("DATABASE_PASSWORD", "")
os.environ.setdefault("DATABASE_HOST", "")
os.environ.setdefault("DATABASE_PORT", "")

from .settings import *  # noqa: F401, F403, E402

# Acelera a criação de usuários nos testes
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ─────────────────────────────────────────────────────────────────────────────
# Compatibilidade SQLite em memória para DateTimeRangeField, BtreeGistExtension
# e ExclusionConstraint quando DATABASE_ENGINE estiver usando sqlite3 nos testes
# ─────────────────────────────────────────────────────────────────────────────
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    import json
    import sqlite3
    from django.contrib.postgres.constraints import ExclusionConstraint
    from django.contrib.postgres.fields.ranges import DateTimeRangeField, RangeField
    from django.contrib.postgres.operations import BtreeGistExtension
    from psycopg2.extras import DateTimeTZRange

    # 1. No-op para BtreeGistExtension e ExclusionConstraint no SQLite
    BtreeGistExtension.database_forwards = lambda self, *args, **kwargs: None  # type: ignore[method-assign]
    BtreeGistExtension.database_backwards = lambda self, *args, **kwargs: None  # type: ignore[method-assign]
    ExclusionConstraint.constraint_sql = lambda self, *args, **kwargs: ""  # type: ignore[method-assign]
    ExclusionConstraint.create_sql = lambda self, *args, **kwargs: None  # type: ignore[method-assign]
    ExclusionConstraint.remove_sql = lambda self, *args, **kwargs: None  # type: ignore[method-assign]

    # 2. Registra adapter nativo no sqlite3 para DateTimeTZRange e mapeia DateTimeRangeField
    def _adapt_datetimetzrange(val: DateTimeTZRange) -> str:
        lower_str = val.lower.isoformat() if val.lower else None
        upper_str = val.upper.isoformat() if val.upper else None
        return json.dumps({"lower": lower_str, "upper": upper_str})

    sqlite3.register_adapter(DateTimeTZRange, _adapt_datetimetzrange)
    SQLiteDatabaseWrapper.data_types["DateTimeRangeField"] = "TEXT"
    RangeField.get_placeholder = lambda self, value, compiler, connection: "%s"  # type: ignore[method-assign]

    def _range_get_db_prep_value(
        self: DateTimeRangeField, value: Any, connection: Any, prepared: bool = False
    ) -> Any:
        if value is None:
            return None
        if isinstance(value, DateTimeTZRange):
            return _adapt_datetimetzrange(value)
        return value

    def _range_from_db_value(
        self: DateTimeRangeField, value: Any, expression: Any, connection: Any
    ) -> Any:
        if value is None or isinstance(value, DateTimeTZRange):
            return value
        if isinstance(value, str):
            data = json.loads(value)
            lower = datetime.fromisoformat(data["lower"]) if data.get("lower") else None
            upper = datetime.fromisoformat(data["upper"]) if data.get("upper") else None
            return DateTimeTZRange(lower=lower, upper=upper)
        return value

    DateTimeRangeField.get_db_prep_value = _range_get_db_prep_value  # type: ignore[method-assign]
    DateTimeRangeField.get_db_prep_save = lambda self, value, connection: _range_get_db_prep_value(  # type: ignore[method-assign]
        self, value, connection
    )
    DateTimeRangeField.from_db_value = _range_from_db_value  # type: ignore[assignment]
    DateTimeRangeField.get_db_converters = lambda self, connection: [  # type: ignore[method-assign]
        _range_from_db_value.__get__(self, DateTimeRangeField)
    ]

    # 3. Registra funções SQL customizadas no SQLite para os lookups `overlap` e `startswith`
    def _to_epoch(iso_str: str | None) -> float | None:
        if not iso_str:
            return None
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt.timestamp()

    def _sqlite_range_overlap(col_json: str | None, rhs_json: str | None) -> int:
        if not col_json or not rhs_json:
            return 0
        col = json.loads(col_json)
        rhs = json.loads(rhs_json)
        c_low, c_up = _to_epoch(col.get("lower")), _to_epoch(col.get("upper"))
        r_low, r_up = _to_epoch(rhs.get("lower")), _to_epoch(rhs.get("upper"))
        if c_low is None or c_up is None or r_low is None or r_up is None:
            return 0
        return 1 if (c_low < r_up and c_up > r_low) else 0

    def _sqlite_range_lower_iso(col_json: str | None) -> str | None:
        if not col_json:
            return None
        col = json.loads(col_json)
        if not col.get("lower"):
            return None
        dt = datetime.fromisoformat(col["lower"])
        if dt.tzinfo is not None:
            dt = dt.astimezone(dt_timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    _orig_get_new_connection = SQLiteDatabaseWrapper.get_new_connection

    def _patched_get_new_connection(self: SQLiteDatabaseWrapper, conn_params: Any) -> Any:
        conn = _orig_get_new_connection(self, conn_params)
        conn.create_function("django_range_overlap", 2, _sqlite_range_overlap)
        conn.create_function("django_range_lower_iso", 1, _sqlite_range_lower_iso)
        return conn

    SQLiteDatabaseWrapper.get_new_connection = _patched_get_new_connection  # type: ignore[method-assign]

    class SQLiteRangeOverlap(Lookup):
        lookup_name = "overlap"

        def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            return f"django_range_overlap({lhs}, {rhs}) = 1", lhs_params + rhs_params

    class SQLiteDateTimeRangeLowerTransform(Transform):
        lookup_name = "startswith"
        output_field = DateTimeField()

        def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
            lhs, params = compiler.compile(self.lhs)
            return f"django_range_lower_iso({lhs})", params

    DateTimeRangeField.register_lookup(SQLiteRangeOverlap)
    DateTimeRangeField.register_lookup(SQLiteDateTimeRangeLowerTransform)
