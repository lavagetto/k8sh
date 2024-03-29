#!/usr/bin/env python3
"""
k8sh is an interactive shell for kubernetes clusters.

While helmsmen might steer the ship, it doesn't move without rowers. k8sh
allows you to drill down from cluster to namespace, to pods/deployments, and from there
to individual containers, allowing you to inspect them and execute processes in
their namespaces.
"""
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import attr
import yaml  # type: ignore
from colorama import Fore, Style, init  # type: ignore
from xdg import XDG_CONFIG_HOME  # type: ignore


@attr.s
class Config:
    kubectl_host: Optional[str] = attr.ib(default=None, kw_only=True)
    kubeconfig_format: str = attr.ib(default="KUBECONFIG=/etc/kubernetes/{namespace}-{cluster}.config", kw_only=True)
    ssh_opts: Optional[List] = attr.ib(default=None, kw_only=True)
    ssh_controlmaster_path: str = attr.ib(default="/run/user/%i/ssh-%C", kw_only=True)


@attr.s
class ConfigProfiles:
    """Class containing the configuration for your clusters"""

    default: Config = attr.ib()
    _profiles: Dict[str, Config] = attr.ib(default=None)

    def get(self, profile: str) -> Config:
        return self._profiles.get(profile, self.default)


def setup(configfile: Path) -> ConfigProfiles:
    """Load configfile. Setup execution"""
    # Initialize colorama
    init()
    cfg = {}
    # Load a yaml config file, else just return the default configuration.
    if configfile.exists():
        try:
            cfg = yaml.safe_load(configfile.read_text())
        except Exception:
            print(red("Bad configuration file, ignoring it."))
    profiles = {}
    if "profiles" in cfg:
        for name, conf in cfg["profiles"].items():
            profiles[name] = Config(**conf)
        del cfg["profiles"]

    return ConfigProfiles(Config(**cfg), profiles)


def red(txt: str):
    return Style.BRIGHT + Fore.RED + txt + Style.RESET_ALL


def blue(txt: str):
    return Style.BRIGHT + Fore.BLUE + txt + Style.RESET_ALL


class k8shError(RuntimeError):
    """Special exception for logical errors."""


def k8shConfigPath() -> Path:
    filename = "k8shrc.yaml"
    xdgpath = XDG_CONFIG_HOME.joinpath(filename)
    legacypath = Path.home().joinpath(f".{filename}")
    if xdgpath.is_file():
        return xdgpath
    elif legacypath.is_file():
        warnings.warn(f"Config path {legacypath} is deprecated, please use {xdgpath} instead")
        return legacypath
    return xdgpath
