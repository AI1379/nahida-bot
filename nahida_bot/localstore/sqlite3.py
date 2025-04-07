#
# Created by Renatus Madrigal on 03/28/2025
#

from typing import Dict
import sqlite3
import os

PRIMARY_KEY_TYPE = "INTEGER PRIMARY KEY AUTOINCREMENT"
TEXT = "TEXT NOT NULL"
REAL = "REAL NOT NULL"
INTEGER = "INTEGER NOT NULL"


class SQLite3DB:
    def __init__(self, path: str, name: str):
        self.db_path = os.path.join(path, f"{name}.db")
        self.connection = None
        self.connect()

    def connect(self):
        if not self.connection:
            self.connection = sqlite3.connect(self.db_path)

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
            
    def reset(self):
        self.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.connect()

    def create_table(self, table_name: str, schema: Dict[str, str]):
        cursor = self.connection.cursor()
        columns = ', '.join(
            [f"{col} {dtype}" for col, dtype in schema.items()])
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
        self.connection.commit()

    def insert(self, table_name: str, record: dict):
        cursor = self.connection.cursor()
        columns = ', '.join(record.keys())
        placeholders = ', '.join(['?'] * len(record))
        cursor.execute(
            f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})", tuple(record.values()))
        self.connection.commit()

    def update(self, table_name: str, record: dict, where: Dict[str, str] = None, where_format: str = "{} = ?"):
        cursor = self.connection.cursor()
        set_clause = ', '.join([f"{key} = ?" for key in record.keys()])
        where_clause = ("WHERE " + ' AND '.join(
            [where_format.format(key) for key in where.keys()]
        )) if where else ''
        params = tuple(record.values()) + \
            (tuple(where.values()) if where else ())
        cursor.execute(
            f"UPDATE {table_name} SET {set_clause} {where_clause}", params)
        self.connection.commit()

    def delete(self, table_name: str, where: Dict[str, str] = None, where_format: str = "{} = ?"):
        cursor = self.connection.cursor()
        where_clause = ("WHERE " + ' AND '.join(
            [where_format.format(key) for key in where.keys()]
        )) if where else ''
        params = tuple(where.values()) if where else ()
        query = f"DELETE FROM {table_name} {where_clause}"
        cursor.execute(query, params)
        self.connection.commit()

    def select(self, table_name: str, where: Dict[str, str] = None, where_format: str = "{} = ?"):
        cursor = self.connection.cursor()
        where_clause = ("WHERE " + ' AND '.join(
            [where_format.format(key) for key in where.keys()]
        )) if where else ''
        params = tuple(where.values()) if where else ()
        query = f"SELECT * FROM {table_name} {where_clause}"
        return cursor.execute(query, params).fetchall()
    
    def get_cursor(self):
        return self.connection.cursor()
    
    def commit(self):
        self.connection.commit()
