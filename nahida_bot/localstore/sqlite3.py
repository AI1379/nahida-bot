#
# Created by Renatus Madrigal on 03/28/2025
#

from nahida_bot.localstore.localstore import BaseLocalStore
from typing import Dict, Callable
import sqlite3


class SQLite3DB(BaseLocalStore):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection = None
        self.schema = {}

    def connect(self):
        self.connection = sqlite3.connect(self.db_path)

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def set_schema(self, schema: Dict[str, str]):
        """
        Set the schema for the database.

        :param schema: A dictionary where keys are column names and values are data types.
        """
        self.schema = schema

    def validate_data(self, data: Dict[str, str]) -> bool:
        """
        Validate the data against the schema.

        :param data: A dictionary where keys are column names and values are data to be inserted.
        :return: True if valid, False otherwise.
        """
        for key, value in data.items():
            if key not in self.schema:
                return False
            if not isinstance(value, self.schema[key]):
                return False
        return True

    def create_table(self, table_name: str):
        cursor = self.connection.cursor()
        columns = ', '.join(
            [f"{col} {dtype}" for col, dtype in self.schema.items()])
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
        self.connection.commit()

    def insert(self, table_name: str, record: dict):
        if not self.validate_data(record):
            raise ValueError("Data does not match schema")

        cursor = self.connection.cursor()
        columns = ', '.join(record.keys())
        placeholders = ', '.join(['?'] * len(record))
        cursor.execute(
            f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})", tuple(record.values()))
        self.connection.commit()
       
    # TODO: Check how to design a proper update function 
    def update(self, table_name: str, record: dict, where: Callable[[dict], bool] = None):
        if not self.validate_data(record):
            raise ValueError("Data does not match schema")

        cursor = self.connection.cursor()
        set_clause = ', '.join([f"{key} = ?" for key in record.keys()])
        where_clause = ' AND '.join([f"{key} = ?" for key in where.keys()]) if where else ''
        params = tuple(record.values()) + (tuple(where.values()) if where else ())
        cursor.execute(
            f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}", params)
        self.connection.commit()
