import os
from pathlib import Path
from typing import Optional

import cmd2  # type: ignore

from k8sh import blue, k8shConfigPath, k8shError, kubernetes, red, setup
from k8sh.exec import Kubectl, RemoteCommand

CAT_NAV = "Kubernetes navigation"
CAT_CONT = "Container-level debugging"
CAT_SERV = "Service information"


class KubeCmd(cmd2.Cmd):
    def __init__(self, remote: RemoteCommand, *args):
        self.remote: RemoteCommand = remote
        self.current: kubernetes.KubeObject = kubernetes.KubeObject("null", Kubectl("", "", self.remote), None)
        super().__init__(*args)

    def _check_current(self, desired_type: Optional[str] = None):
        """Check we have a valid current object"""
        if self.current.kind == "":
            raise k8shError("Please select a cluster with 'use' first")
        if desired_type is not None and self.current.kind != desired_type:
            raise k8shError("Invalid context: f{self.current.kind}, should be f{desired_type}")

    def cd(self, val: str):
        self._check_current()
        if not val:
            # Rewind to cluster level
            while self.current.parent is not None:
                self.current = self.current.parent
            return
        next_element = self.current
        while val != "":
            next_element, val = next_element.cd(val)
        self.current = next_element

    def _prompt(self):
        layer = self.current.kind
        # Root layer
        if layer == "":
            return "NONE (root) $ "
        # Find the cluster name
        c = self.current
        while c.kind != "cluster":
            if c.parent is None:
                raise k8shError(f"Found a {c.kind} object '{c.name}' without a parent. Something is very wrong.")
            c = c.parent
        cl = red(c.name)
        path = blue(self.current.path)
        return f"{cl}:{path} ({layer})$ "

    def preloop(self):
        self.prompt = self._prompt()

    def postcmd(self, stop, line):
        if not stop:
            self.prompt = self._prompt()
        return super().postcmd(stop, line)

    def postloop(self):
        print("")

    #
    #  Interactive shell commands.
    #
    @cmd2.with_category(CAT_NAV)
    def do_use(self, arg):
        """
        Usage: use <cluster>
        Select the cluster to operate on.
        """
        kubectl = Kubectl(arg, "", self.remote)
        self.current = kubernetes.Cluster(arg, kubectl)

    def do_exit(self, arg):
        """Exit the program"""
        print("Bye! Keep navigating!")
        return True

    def do_EOF(self, line):
        """Exit the program by typing Ctrl+D"""
        return True

    @cmd2.with_category(CAT_NAV)
    def do_cd(self, arg: str):
        """
        Usage: cd <what>
        Change layer of the kubernetes hierarchy.
        Only supports single-level changes of hierarchy, including "..".

        If <what> is omitted, the whole enviroment is reset to the cluster
        level.
        """
        try:
            self.cd(arg)
        except k8shError as e:
            print(red(str(e)))

    def complete_cd(self, text, line, start_index, end_index):
        try:
            self._check_current()
        except k8shError:
            return []
        if text == "":
            return [c.path_fragment() for c in self.current.children]
        elif text == "..":
            base = ".."
            to_suggest = text[3:]
        elif "/" in text:
            base, to_suggest = text.rsplit("/", 1)
        else:
            base = ""
            to_suggest = text
        # save the current object, then move to the base
        cur = self.current
        try:
            if base != "":
                self.cd(base)
            return [
                os.path.join(base, c.path_fragment())
                for c in self.current.children
                if c.path_fragment().startswith(to_suggest)
            ]
        except k8shError:
            return []
        finally:
            # reset the current object
            self.current = cur

    @cmd2.with_category(CAT_NAV)
    def do_ls(self, arg):
        """
        Usage: ls
        Context: All but container

        Lists all the properties at the current hierarchy level.

        For example, within a namespace, pods will be listed. In a pod,
        containers will be shown.
        """
        for el in self.current.children:
            print(el.path_fragment())

    @cmd2.with_category(CAT_CONT)
    def do_ps(self, arg):
        """
        Usage: ps
        Context: container

        Show processes running in the container
        """
        try:
            self._check_current("container")
            self.current.ps()
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_CONT)
    def do_tail(self, arg):
        """
        Usage: tail [-f]
        Context: container

        Gets the logs of the container from k8s
        """
        if arg != "-f":
            arg = ""
        try:
            self._check_current("container")
            self.current.tail(arg)
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_CONT)
    def do_nsenter(self, arg):
        """
        Usage: nsenter [FLAGS] <command>
        Context: container

        Runs command <command> within the namespaces of the container selected with FLAGS.

        Example:
          nsenter -n tcpdump
        """
        try:
            self._check_current("container")
            self.current.nsenter(arg)
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_CONT)
    def do_exec(self, arg):
        """
        Usage: exec <command>
        Context: container

        Runs a command **within** the container
        """
        # This needs to run with admin privileges.
        try:
            self._check_current("container")
            self.current.exec(arg)
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_SERV)
    def do_view(self, arg):
        """
        Usage: view
        Context: service

        Shows the ports/ips exposed by the service
        """
        try:
            self._check_current("service")
            data = self.current.get()
            for port in data["ports"]:
                print(f"{port['name']}:\t{port['nodeport']}->{port['target']}")
        except k8shError as e:
            print(red(str(e)))


def from_configfile(path: Path) -> KubeCmd:
    """Get a shell from a configuration file"""
    config = setup(path)
    kubectl_remote = RemoteCommand(config.kubectl_host, config.ssh_opts)
    Kubectl.kubeconfig_fmt = config.kubeconfig_format
    sh = KubeCmd(kubectl_remote)
    return sh


def main():
    configfile = k8shConfigPath()
    sh = from_configfile(configfile)
    sh.cmdloop()
