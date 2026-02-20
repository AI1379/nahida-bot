#
# Created by Renatus Madrigal on 02/19/2026
#

from pathlib import Path

import polars as pl


class PolarsParquetDB:
    def __init__(self, base_path: str, plugin_name: str):
        self.base_path = Path(base_path) / plugin_name
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_table(self, table_name: str) -> pl.DataFrame:
        table_path = self.base_path / f"{table_name}.parquet"
        if table_path.exists():
            return pl.read_parquet(table_path)
        else:
            # Create the file if it doesn't exist
            table_path.touch()
            return pl.DataFrame()

    def save_table(self, table_name: str, df: pl.DataFrame):
        table_path = self.base_path / f"{table_name}.parquet"
        df.write_parquet(table_path)
