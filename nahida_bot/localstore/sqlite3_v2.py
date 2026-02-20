#
# Created by Renatus Madrigal on 02/19/2026
#


from typing import (
    Type,
    TypeVar,
    Generic,
    Optional,
    List,
    Dict,
    Any,
    Union,
    get_origin,
    Generator,
)
from dataclasses import fields as dataclass_fields, is_dataclass
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from sqlite3 import Connection, Cursor
from pydantic import BaseModel
from datetime import datetime
import json

# Type variables
T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=BaseModel)


class SQLiteTypeAdapter:
    """Convert Python types to SQLite types and vice versa"""

    @staticmethod
    def python_to_sql(python_type: Type | str) -> str:
        """Convert Python type to SQLite type definition"""
        # TODO: Check for python_type being a string (for forward references) and handle accordingly
        if python_type is int:
            return "INTEGER"
        elif python_type is float:
            return "REAL"
        elif python_type is bool:
            return "INTEGER"
        elif python_type is str:
            return "TEXT"
        elif python_type is bytes:
            return "BLOB"
        elif python_type is datetime:
            return "TEXT"
        else:
            # For complex types, store as JSON
            return "TEXT"

    @staticmethod
    def python_to_value(value: Any, python_type: Type | str) -> Any:
        """Convert Python value to SQLite storable value"""
        if value is None:
            return None
        if python_type is bool and isinstance(value, bool):
            return 1 if value else 0
        elif python_type is datetime and isinstance(value, datetime):
            return value.isoformat()
        elif python_type not in (int, float, str, bytes):
            # For complex types, store as JSON
            return json.dumps(value, default=str)
        return value

    @staticmethod
    def value_to_python(value: Any, python_type: Type | str) -> Any:
        """Convert SQLite value back to Python type"""
        if value is None:
            return None
        if python_type is bool:
            return bool(value)
        elif python_type is datetime and isinstance(value, str):
            return datetime.fromisoformat(value)
        elif python_type not in (int, float, str, bytes):
            # For complex types, parse from JSON
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value


class TableSchema:
    """Schema definition for a database table based on Pydantic model or dataclass"""

    def __init__(self, model: Union[Type[BaseModel], Type], table_name: str):
        self.model = model
        self.table_name = table_name
        self.columns: Dict[
            str, tuple[Type | str, bool]
        ] = {}  # column_name -> (type, is_primary_key)
        self.primary_key: Optional[str] = None

        self._parse_model()

    def _parse_model(self):
        """Parse Pydantic model or dataclass to extract columns"""
        if issubclass(self.model, BaseModel):
            self._parse_pydantic()
        elif is_dataclass(self.model):
            self._parse_dataclass()
        else:
            raise ValueError(f"Unsupported model type: {self.model}")

    def _parse_pydantic(self):
        """Parse Pydantic model fields"""
        model_fields = self.model.model_fields
        for field_name, field_info in model_fields.items():
            column_type = field_info.annotation

            if column_type is None:
                raise ValueError(
                    f"Field '{field_name}' in model '{self.model.__name__}' has no type annotation"
                )

            # Check if this is a primary key (by convention, 'id' field)
            is_pk = field_name == "id"
            if is_pk:
                self.primary_key = field_name

            self.columns[field_name] = (column_type, is_pk)

    def _parse_dataclass(self):
        """Parse dataclass fields"""
        for field in dataclass_fields(self.model):
            column_type = field.type
            is_pk = field.name == "id"
            if is_pk:
                self.primary_key = field.name

            self.columns[field.name] = (column_type, is_pk)

    def get_create_table_sql(self) -> str:
        """Generate CREATE TABLE SQL"""
        columns_sql = []

        for col_name, (col_type, is_pk) in self.columns.items():
            sql_type = SQLiteTypeAdapter.python_to_sql(col_type)

            if is_pk:
                columns_sql.append(f"{col_name} INTEGER PRIMARY KEY AUTOINCREMENT")
            else:
                # Check if field is Optional
                is_optional = get_origin(col_type) is Union
                nullable = "NULL" if is_optional else "NOT NULL"
                columns_sql.append(f"{col_name} {sql_type} {nullable}")

        columns_def = ",\n    ".join(columns_sql)
        return f"CREATE TABLE IF NOT EXISTS {self.table_name} (\n    {columns_def}\n)"


class SQLite3DBv2(Generic[ModelT]):
    """
    Modern SQLite3 wrapper with Pydantic support.

    Usage:
    ```python
    from pydantic import BaseModel
    from datetime import datetime

    class User(BaseModel):
        id: Optional[int] = None
        name: str
        email: str
        age: int
        created_at: datetime

    db = SQLite3DBv2(User, "data", "users")
    db.insert(User(name="Alice", email="alice@example.com", age=30, created_at=datetime.now()))

    users = db.query().filter_by(name="Alice").all()
    db.update(users[0].id, User(name="Bob", email="bob@example.com", age=31, created_at=datetime.now()))
    db.delete(users[0].id)
    ```
    """

    def __init__(
        self,
        model: Type[ModelT],
        db_path: str | Path,
        table_name: Optional[str] = None,
        auto_create: bool = True,
    ):
        """
        Initialize SQLite3DBv2.

        Args:
            model: Pydantic model or dataclass defining table schema
            db_path: Path to the database file
            table_name: Custom table name (defaults to model class name in snake_case)
            auto_create: Automatically create table if it doesn't exist
        """
        self.model = model
        self.table_name = table_name or self._get_default_table_name(model)

        self.db_path = db_path if isinstance(db_path, Path) else Path(db_path)
        self.schema = TableSchema(model, self.table_name)

        self._connection: Optional[Connection] = None

        if auto_create:
            self._ensure_table()

    @staticmethod
    def _get_default_table_name(model: Type) -> str:
        """Convert class name to snake_case for table name"""
        name = model.__name__
        # Simple snake_case conversion
        snake_case = "".join(
            ["_" + c.lower() if c.isupper() else c for c in name]
        ).lstrip("_")
        return snake_case

    @property
    def connection(self) -> Connection:
        """Get or create database connection"""
        if self._connection is None:
            self._connection = sqlite3.connect(str(self.db_path))
            self._connection.row_factory = sqlite3.Row
        return self._connection

    @contextmanager
    def _cursor(self) -> Generator[Cursor, Any, None]:
        """Context manager for database cursor"""
        cursor = self.connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    def _ensure_table(self):
        """Create table if it doesn't exist"""
        with self._cursor() as cursor:
            cursor.execute(self.schema.get_create_table_sql())
            self.connection.commit()

    def _model_to_dict(self, instance: ModelT) -> Dict[str, Any]:
        """Convert model instance to dictionary with SQLite-compatible values"""
        if isinstance(instance, BaseModel):
            # Use model_dump() without exclude_unset to include default values
            data = instance.model_dump()
        elif is_dataclass(instance):
            data = {
                f.name: getattr(instance, f.name) for f in dataclass_fields(instance)
            }
        else:
            raise ValueError(f"Unsupported instance type: {type(instance)}")

        # Convert values to SQLite-compatible format
        result = {}
        for col_name, col_value in data.items():
            if col_name in self.schema.columns:
                col_type = self.schema.columns[col_name][0]
                result[col_name] = SQLiteTypeAdapter.python_to_value(
                    col_value, col_type
                )

        return result

    def _dict_to_model(self, data: Dict[str, Any]) -> ModelT:
        """Convert dictionary from database to model instance"""
        # Convert values back from SQLite format
        converted = {}
        for col_name, col_value in data.items():
            if col_name in self.schema.columns:
                col_type = self.schema.columns[col_name][0]
                converted[col_name] = SQLiteTypeAdapter.value_to_python(
                    col_value, col_type
                )
            else:
                converted[col_name] = col_value

        if isinstance(self.model, type) and issubclass(self.model, BaseModel):
            return self.model(**converted)
        elif is_dataclass(self.model):
            return self.model(**converted)
        else:
            raise ValueError(f"Cannot instantiate model: {self.model}")

    def insert(self, instance: ModelT) -> int:
        """
        Insert a single record.

        Returns:
            The row ID of the inserted record
        """
        data = self._model_to_dict(instance)

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(data.values()))
            self.connection.commit()
            if cursor.lastrowid is None:
                raise ValueError("Failed to retrieve last inserted row ID")
            return cursor.lastrowid

    def insert_many(self, instances: List[ModelT]) -> List[int]:
        """
        Insert multiple records.

        Returns:
            List of row IDs
        """
        row_ids = []
        for instance in instances:
            row_id = self.insert(instance)
            row_ids.append(row_id)
        return row_ids

    def update(self, record_id: int, instance: ModelT) -> bool:
        """
        Update a record by ID.

        Returns:
            True if record was updated, False if not found
        """
        data = self._model_to_dict(instance)
        # Remove primary key from update data
        if self.schema.primary_key and self.schema.primary_key in data:
            del data[self.schema.primary_key]

        if not data:
            return False

        set_clause = ", ".join([f"{col} = ?" for col in data.keys()])
        sql = f"UPDATE {self.table_name} SET {set_clause} WHERE {self.schema.primary_key} = ?"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(data.values()) + (record_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def delete(self, record_id: int) -> bool:
        """
        Delete a record by ID.

        Returns:
            True if record was deleted, False if not found
        """
        sql = f"DELETE FROM {self.table_name} WHERE {self.schema.primary_key} = ?"

        with self._cursor() as cursor:
            cursor.execute(sql, (record_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def delete_where(self, **conditions) -> int:
        """
        Delete records matching conditions.

        Returns:
            Number of deleted records
        """
        if not conditions:
            raise ValueError("At least one condition required for delete_where")

        where_clause = " AND ".join([f"{col} = ?" for col in conditions.keys()])
        sql = f"DELETE FROM {self.table_name} WHERE {where_clause}"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(conditions.values()))
            self.connection.commit()
            return cursor.rowcount

    def delete_top_k(self, k: int, column: str, descending: bool = True) -> int:
        """
        Delete top K records ordered by a column.

        Returns:
            Number of deleted records
        """
        order = "DESC" if descending else "ASC"
        key = self.schema.primary_key if self.schema.primary_key else column
        sql = f"DELETE FROM {self.table_name} WHERE {key} IN (SELECT {key} FROM {self.table_name} ORDER BY {column} {order} LIMIT ?)"

        with self._cursor() as cursor:
            cursor.execute(sql, (k,))
            self.connection.commit()
            return cursor.rowcount

    def keep_top_k(self, k: int, column: str, descending: bool = True) -> int:
        """
        Keep only top K records ordered by a column, delete the rest.

        Returns:
            Number of deleted records
        """
        order = "DESC" if descending else "ASC"
        key = self.schema.primary_key if self.schema.primary_key else column
        sql = f"DELETE FROM {self.table_name} WHERE {key} NOT IN (SELECT {key} FROM {self.table_name} ORDER BY {column} {order} LIMIT ?)"

        with self._cursor() as cursor:
            cursor.execute(sql, (k,))
            self.connection.commit()
            return cursor.rowcount

    def get(self, record_id: int) -> Optional[ModelT]:
        """
        Get a single record by ID.

        Returns:
            Model instance or None if not found
        """
        sql = f"SELECT * FROM {self.table_name} WHERE {self.schema.primary_key} = ?"

        with self._cursor() as cursor:
            cursor.execute(sql, (record_id,))
            row = cursor.fetchone()
            return self._dict_to_model(dict(row)) if row else None

    def get_where(self, **conditions) -> Optional[ModelT]:
        """
        Get first record matching conditions.

        Returns:
            Model instance or None if not found
        """
        if not conditions:
            raise ValueError("At least one condition required")

        where_clause = " AND ".join([f"{col} = ?" for col in conditions.keys()])
        sql = f"SELECT * FROM {self.table_name} WHERE {where_clause} LIMIT 1"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(conditions.values()))
            row = cursor.fetchone()
            return self._dict_to_model(dict(row)) if row else None

    def all(self) -> List[ModelT]:
        """Get all records"""
        sql = f"SELECT * FROM {self.table_name}"

        with self._cursor() as cursor:
            cursor.execute(sql)
            return [self._dict_to_model(dict(row)) for row in cursor.fetchall()]

    def filter_where(self, **conditions) -> List[ModelT]:
        """
        Get all records matching conditions.

        Returns:
            List of model instances
        """
        if not conditions:
            return self.all()

        where_clause = " AND ".join([f"{col} = ?" for col in conditions.keys()])
        sql = f"SELECT * FROM {self.table_name} WHERE {where_clause}"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(conditions.values()))
            return [self._dict_to_model(dict(row)) for row in cursor.fetchall()]

    def filter_like(self, column: str, pattern: str) -> List[ModelT]:
        """
        Get records where column matches a LIKE pattern.

        Example:
            db.filter_like("name", "%alice%")
        """
        sql = f"SELECT * FROM {self.table_name} WHERE {column} LIKE ?"

        with self._cursor() as cursor:
            cursor.execute(sql, (pattern,))
            return [self._dict_to_model(dict(row)) for row in cursor.fetchall()]

    def top_k(self, k: int, column: str, descending: bool = True) -> List[ModelT]:
        """
        Get top K records ordered by a column.

        Returns:
            List of model instances
        """
        order = "DESC" if descending else "ASC"
        sql = f"SELECT * FROM {self.table_name} ORDER BY {column} {order} LIMIT ?"

        with self._cursor() as cursor:
            cursor.execute(sql, (k,))
            return [self._dict_to_model(dict(row)) for row in cursor.fetchall()]

    def count(self, **conditions) -> int:
        """Count records matching conditions"""
        if not conditions:
            sql = f"SELECT COUNT(*) FROM {self.table_name}"
            with self._cursor() as cursor:
                cursor.execute(sql)
                return cursor.fetchone()[0]

        where_clause = " AND ".join([f"{col} = ?" for col in conditions.keys()])
        sql = f"SELECT COUNT(*) FROM {self.table_name} WHERE {where_clause}"

        with self._cursor() as cursor:
            cursor.execute(sql, tuple(conditions.values()))
            return cursor.fetchone()[0]

    def exists(self, record_id: int) -> bool:
        """Check if record with given ID exists"""
        sql = f"SELECT 1 FROM {self.table_name} WHERE {self.schema.primary_key} = ? LIMIT 1"

        with self._cursor() as cursor:
            cursor.execute(sql, (record_id,))
            return cursor.fetchone() is not None

    def clear(self):
        """Delete all records from table"""
        sql = f"DELETE FROM {self.table_name}"

        with self._cursor() as cursor:
            cursor.execute(sql)
            self.connection.commit()

    def drop(self):
        """Drop the entire table"""
        sql = f"DROP TABLE IF EXISTS {self.table_name}"

        with self._cursor() as cursor:
            cursor.execute(sql)
            self.connection.commit()

    def reset(self):
        """Drop and recreate the table"""
        self.drop()
        self._ensure_table()

    def close(self):
        """Close database connection"""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

    def __del__(self):
        """Cleanup on deletion"""
        self.close()


class SQLite3DBv2Store:
    def __init__(self, data_path: str, plugin_name: str):
        self.db_path = Path(data_path) / f"{plugin_name}.db"

    def get_or_create_table(
        self,
        model: Type[ModelT],
        table_name: Optional[str] = None,
        auto_create: bool = True,
    ) -> SQLite3DBv2[ModelT]:
        return SQLite3DBv2(
            model, self.db_path, table_name=table_name, auto_create=auto_create
        )
