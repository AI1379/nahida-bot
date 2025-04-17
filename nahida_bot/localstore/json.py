#
# Created by Renatus Madrigal on 4/17/2025
#

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, TypeVar, Union
import threading
from collections.abc import MutableMapping

T = TypeVar('T')

class JSONHandler(MutableMapping):
    def __init__(self, file_path: Union[str, Path], write_delay: float = 1.0):
        self.file_path = Path(file_path)
        self.write_delay = write_delay
        self._data: Dict[str, Any] = {}
        self._write_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        
        # Ensure directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing data if file exists
        if self.file_path.exists():
            with open(self.file_path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
    
    def _schedule_write(self):
        """Schedule a write operation after a delay"""
        if self._write_timer is not None:
            self._write_timer.cancel()
        
        self._write_timer = threading.Timer(self.write_delay, self._write_to_file)
        self._write_timer.start()
    
    def _write_to_file(self):
        """Write data to file"""
        with self._lock:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
    
    def __getitem__(self, key: str) -> Any:
        return self._data[key]
    
    def __setitem__(self, key: str, value: Any):
        with self._lock:
            self._data[key] = value
        self._schedule_write()
    
    def __delitem__(self, key: str):
        with self._lock:
            del self._data[key]
        self._schedule_write()
    
    def __iter__(self):
        return iter(self._data)
    
    def __len__(self) -> int:
        return len(self._data)
    
    def __contains__(self, key: str) -> bool:
        return key in self._data
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
    
    def update(self, *args, **kwargs):
        with self._lock:
            self._data.update(*args, **kwargs)
        self._schedule_write()
    
    def clear(self):
        with self._lock:
            self._data.clear()
        self._schedule_write()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._write_timer is not None:
            self._write_timer.cancel()
        self._write_to_file()

class JSONManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self._handlers: Dict[str, JSONHandler] = {}
        self._handlers_lock = threading.Lock()
    
    def get_handler(self, file_path: Union[str, Path], write_delay: float = 1.0) -> JSONHandler:
        """Get or create a JSONHandler for the specified file path"""
        file_path = str(Path(file_path))
        
        with self._handlers_lock:
            if file_path not in self._handlers:
                self._handlers[file_path] = JSONHandler(file_path, write_delay)
            return self._handlers[file_path]
    
    def close_all(self):
        """Close all handlers and write their data to disk"""
        with self._handlers_lock:
            for handler in self._handlers.values():
                handler._write_to_file()
            self._handlers.clear()


