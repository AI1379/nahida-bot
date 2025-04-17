#
# Created by Renatus Madrigal on 4/17/2025
#
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class PluginFeature:
    """Represents a feature of a plugin"""
    name: str
    description: str
    commands: List[str] = field(default_factory=list)


@dataclass
class PluginInfo:
    """Represents a plugin and its features"""
    name: str
    description: str
    features: List[PluginFeature] = field(default_factory=list)


class PluginRegistry:
    """Registry for all plugins and their features"""
    _instance: Optional['PluginRegistry'] = None
    _plugins: Dict[str, PluginInfo] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register_plugin(cls, name: str, description: str) -> PluginInfo:
        """Register a new plugin"""
        if name not in cls._plugins:
            cls._plugins[name] = PluginInfo(name=name, description=description)
        return cls._plugins[name]

    @classmethod
    def add_feature(cls, plugin_name: str, feature_name: str, description: str, commands: List[str] = None) -> None:
        """Add a feature to a plugin"""
        if plugin_name not in cls._plugins:
            raise ValueError(f"Plugin {plugin_name} not registered")
        if commands is None:
            commands = []
        cls._plugins[plugin_name].features.append(
            PluginFeature(name=feature_name, description=description, commands=commands)
        )

    @classmethod
    def get_plugins(cls) -> Dict[str, PluginInfo]:
        """Get all registered plugins"""
        return cls._plugins


# Initialize the registry
plugin_registry = PluginRegistry()
