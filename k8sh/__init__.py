#!/usr/bin/env python3
"""
k8sh is an interactive shell for kubernetes clusters.

While helmsmen might steer the ship, it doesn't move without rowers. k8sh
allows you to drill down from cluster to namespace, to pods/deployments, and from there
to individual containers, allowing you to inspect them and execute processes in
their namespaces.
"""
from typing import Optional, List
import enum

import yaml
import os

from colorama import Fore, Style, init
import attr


@attr.s
class Config:
    kubectl_host: Optional[str] = attr.ib(default=None, kw_only=True)
    kubeconfig_format: str = attr.ib(
        default="KUBECONFIG=/etc/kubernetes/{namespace}-{cluster}.config", kw_only=True
    )
    ssh_opts: Optional[List] = attr.ib(default=None, kw_only=True)


def setup(configfile: str) -> Config:
    # Initialize colorama
    init()
    cfg = {}
    # Load a yaml config file, else just return the default configuration.
    if os.path.isfile(configfile):
        try:
            with open(configfile, "r") as fh:
                cfg = yaml.safe_load(fh)
        except Exception:
            print(red("Bad configuration file, ignoring it."))
    return Config(**cfg)


def red(txt: str):
    return Style.BRIGHT + Fore.RED + txt + Style.RESET_ALL


def blue(txt: str):
    return Style.BRIGHT + Fore.BLUE + txt + Style.RESET_ALL


class k8shError(RuntimeError):
    """Special exception for logical errors."""


class Ctx(enum.IntEnum):
    ROOT = 0
    CLUSTER = 1
    SERVICE = 2
    POD = 3
    CONTAINER = 4


class KubeContext:
    ctx_order = [Ctx.ROOT, Ctx.CLUSTER, Ctx.SERVICE, Ctx.POD, Ctx.CONTAINER]

    def __init__(self, **kwargs):
        self.env = []

        for k in self.ctx_order[1:]:
            key = k.name.lower()
            if key in kwargs:
                self.env.append(kwargs[key])
            else:
                # I know, I know. This could be better. Oh well :P
                break

    def current(self) -> Ctx:
        return self.ctx_order[len(self.env)]

    def pop(self):
        if self.env == []:
            raise k8shError("Cannot go back further than the root context.")
        self.env.pop()

    def push(self, val):
        self.env.append(val)

    def reset(self):
        """
        Reset the context to just the first element
        """
        cluster = self.get(Ctx.CLUSTER)
        self.env = [cluster]

    def get(self, attr: Ctx):
        idx = self.ctx_order.index(attr)
        # Why would someone want to get the root context? still.
        if idx == 0:
            return ""
        try:
            return self.env[idx - 1]
        except IndexError:
            return None
