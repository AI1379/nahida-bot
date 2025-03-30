#
# Created by Renatus Madrigal on 03/28/2025
#

from abc import ABC, abstractmethod
from typing import Dict, Callable

"""
Base class for local storage.

A local store should implement the following methods:
- connect: Connect to the local store.
- close: Close the connection to the local store.
- create_table: Create a table in the local store.
- insert: Insert a record into the local store.
- update: Update a record in the local store.
- delete: Delete a record from the local store.
- select: Select records from the local store.

A local store database is a structured collection of data that is stored on the local machine.
The structure of the database contains `database`, `table` and `record`.
For example, a database can be a SQLite3 database.
In this case, the table is a table in the SQLite3 database and the record is a row in the table.
Another example is directory containing several JSON files.
In this case, the database is the directory, the table is the JSON file and the record is a key-value pair in the JSON file.
"""


class BaseLocalStore(ABC):

    @abstractmethod
    def connect(self):
        """
        Connect to the local store.
        """
        pass

    @abstractmethod
    def close(self):
        """
        Close the connection to the local store.
        """
        pass

    @abstractmethod
    def create_table(self, table_name: str):
        """
        Create a table in the local store.

        :param table_name: Name of the table to create.
        """
        pass

    @abstractmethod
    def insert(self, table_name: str, record: dict):
        """
        Insert a record into the local store.

        :param table_name: Name of the table to insert into.
        :param record: Record to insert.
        """
        pass

    @abstractmethod
    def update(self, table_name: str, record: dict, where: Callable[[dict], bool] = None):
        """
        Update a record in the local store.

        :param table_name: Name of the table to update.
        :param record: Record to update.
        :param where: Condition to update the record.
        """
        pass

    @abstractmethod
    def delete(self, table_name: str, where: Callable[[dict], bool] = None):
        """
        Delete a record from the local store.

        :param table_name: Name of the table to delete from.
        :param where: Condition to delete the record.
        """
        pass

    @abstractmethod
    def select(self, table_name: str, where: Callable[[dict], bool] = None) -> list:
        """
        Select records from the local store.

        :param table_name: Name of the table to select from.
        :param where: Condition to select the record.
        :return: List of records.
        """
        pass
