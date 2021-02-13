import os
import shlex
from typing import Dict, List, Optional, Tuple

import attr

from k8sh import k8shError
from k8sh.exec import Kubectl, RemoteCommand


@attr.s
class KubeObject:
    """Generic kubernetes object wrapper"""

    name: str = attr.ib()
    kubectl: Kubectl = attr.ib()
    parent: Optional["KubeObject"] = attr.ib()
    _children: Optional[List["KubeObject"]] = attr.ib(init=False, default=None)
    kind: str = ""

    @property
    def children(self) -> List["KubeObject"]:
        """List the children of this object"""
        raise NotImplementedError(
            "The children method needs to be implemented by subclasses."
        )

    def refresh(self):
        """Remove any response cache we might have saved"""
        raise NotImplementedError("refresh() needs to be implemented.")

    def path_fragment(self) -> str:
        """The path fragment for this object"""
        return self.name

    def cd(self, val) -> Tuple["KubeObject", str]:
        # doubledot support
        if val == "..":
            # If there is no parent, just return yourself.
            if self.parent is None:
                return (self, "")
            else:
                return (self.parent, "")
        if val.startswith("../"):
            if self.parent is None:
                raise k8shError("Could not change directory beyond root")
            else:
                return (self.parent, val[3:])

        # Normal cd support
        for el in self.children:
            frag = el.path_fragment()
            # Precise match, we're at the end of the hierarchy
            if val == frag:
                return (el, "")
            # If no precise match is found, let's try
            # as a prefix path.
            frag += "/"
            if val.startswith(frag):
                residual = val.replace(frag, "")
                return (el, residual)
        # No result was found. This is an error.
        raise k8shError(f"Could not find {val} in {self.path_fragment()}")

    @property
    def path(self) -> str:
        """The full path of the object"""
        hierarchy = [self.path_fragment()]
        cur_obj = self
        while cur_obj.parent is not None:
            cur_obj = cur_obj.parent
            hierarchy.append(cur_obj.path_fragment())

        hierarchy.reverse()
        return os.path.join(*hierarchy)


@attr.s
class Pod(KubeObject):
    kind: str = "pod"
    _hostname: Optional[str] = attr.ib(init=False, default=None)

    def _gather_data(self):
        self._children = []
        container_data = self.kubectl.json(f"get pods '{self.name}'", False)
        self._hostname = container_data["spec"]["nodeName"]
        for status in container_data["status"]["containerStatuses"]:
            container = Container(status["name"], self.kubectl, self)
            container.ID = status["containerID"].replace("docker://", "")
            container.set_remote(self._hostname)
            self._children.append(container)

    @property
    def children(self) -> List["KubeObject"]:
        if self._children is None:
            self._gather_data()
        return self._children  # type: ignore

    @property
    def hostname(self) -> str:
        if self._hostname is None:
            self._gather_data()
        if self._hostname is None:
            raise k8shError("Could not fetch the hostname.")
        return self._hostname

    def path_fragment(self):
        return f"pods/{self.name}"

    def refresh(self):
        self._children = None
        self._hostname = None


@attr.s
class Container(KubeObject):
    kind: str = "container"
    ID: str = attr.ib(init=False, default="")
    _remote: Optional[RemoteCommand] = attr.ib(init=False, default=None)

    def set_remote(self, hostname: str):
        self._remote = RemoteCommand(hostname, self.kubectl.remote.ssh_opts)

    @property
    def children(self) -> List["KubeObject"]:
        return []

    def refresh(self):
        # noop for containers
        pass

    def ps(self):
        """
        Shows all processes running inside the container
        """
        res = self._remote.run_sync(["sudo", "docker", "top", self.ID])
        if res != 0:
            raise k8shError(
                "Executing docker top on f{self.current.parent.hostname} exited with error code f{res}"
            )

    def nsenter(self, arg: str):
        """
        Allows you to execute command from the kubernetes worker
        inside the container's namespace. You will have to pass
        the namespaces you want to enter, and the command to execute.
        """
        if self._remote is None:
            raise k8shError("No remote host defined, impossible to execute.")
        # Find the main pid of the container
        res = self._remote.run(
            ["sudo", "docker", "inspect", "-f", "'{{.State.Pid}}'", self.ID]
        )
        if res.returncode != 0:
            raise k8shError(
                "Error finding the PID of the container: exitcode {res.returncode}: {res.stderr.decode()}"
            )
        pid = res.stdout.decode().rstrip()
        cmd = ["sudo", "nsenter", "-t", pid] + shlex.split(arg)
        rc = self._remote.run_sync(cmd)
        if rc != 0:
            raise k8shError("Command {} exited with return code {}".format(cmd, rc))

    def tail(self, arg: str):
        """Gets the logs of the container"""
        if self.parent is None:
            raise k8shError("Could not find a linked pod, container badly initialized.")
        tail_cmd = f"logs {arg} {self.parent.name} {self.name}"
        rc = self.kubectl.run_sync(tail_cmd)
        if rc != 0:
            raise k8shError("Could not read the logs")

    def exec(self, arg: str):
        """Runs a command within the container"""
        if self.parent is None:
            raise k8shError("Could not find a linked pod, container badly initialized.")
        # This needs to run with admin privileges
        rc = self.kubectl.run_sync(
            f"exec {self.parent.name} -c {self.name} -- {arg}", True
        )
        if rc != 0:
            raise k8shError(f"Execution of '{arg}' failed with status code {rc}")


class Namespace(KubeObject):
    kind: str = "namespace"

    @property
    def children(self) -> List["KubeObject"]:
        if self._children is None:
            self._children = []
            for r in self.kubectl.json("get pods", False)["items"]:
                name = r["metadata"]["name"]
                self._children.append(Pod(name=name, kubectl=self.kubectl, parent=self))
            for srv in self.kubectl.json("get services", False)["items"]:
                name = srv["metadata"]["name"]
                self._children.append(
                    Service(name=name, kubectl=self.kubectl, parent=self)
                )
        return self._children

    def refresh(self):
        self._children = None


class Service(KubeObject):
    kind: str = "service"

    def path_fragment(self):
        return f"services/{self.name}"

    @property
    def children(self) -> List["KubeObject"]:
        return []

    def get(self) -> Dict:
        """Get the data about the service"""
        service = self.kubectl.json(f"get services {self.name}")
        metadata = service["metadata"]
        ports = service["spec"]["ports"]
        return {
            "name": f"{metadata['namespace']}/services/{metadata['name']}",
            "ports": [
                {
                    "name": p["name"],
                    "target": p["targetPort"],
                    "nodeport": p["nodePort"],
                }
                for p in ports
            ],
        }


class Cluster(KubeObject):
    kind: str = "cluster"

    def __init__(self, name: str, kubectl: Kubectl):
        super().__init__(name, kubectl, None)
        self.kubectl.cluster = self.name
        self.kubectl.namespace = "admin"

    def path_fragment(self) -> str:
        return "/"

    @property
    def children(self) -> List["KubeObject"]:
        if self._children is None:
            self._children = []
            for r in self.kubectl.json("get namespaces", True)["items"]:
                name = r["metadata"]["name"]
                k = Kubectl(self.name, name, self.kubectl.remote)
                self._children.append(Namespace(name=name, kubectl=k, parent=self))
        return self._children

    def refresh(self):
        self._children = None
