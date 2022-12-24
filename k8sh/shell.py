import os
from pathlib import Path
from typing import List, Optional

import cmd2  # type: ignore

from k8sh import blue, k8shConfigPath, k8shError, kubernetes, red, setup, ConfigProfiles, Config
from k8sh.exec import Kubectl, RemoteCommand

from wmflib.interactive import ask_input

CAT_NAV = "Kubernetes navigation"
CAT_CONT = "Container-level debugging"
CAT_SERV = "Service information"
CAT_ADMIN = "Kubernetes administration"

# Maximum number of queries to perform for any command.
MAX_QUERY_LENGTH = 15


class KubeCmd(cmd2.Cmd):
    def __init__(self, remote: RemoteCommand, config: ConfigProfiles, *args):
        self.remote: RemoteCommand = remote
        self.current: kubernetes.KubeObject = kubernetes.KubeObject("null", Kubectl("", "", self.remote), None)
        self.config: ConfigProfiles = config
        super().__init__(*args)

    def _switch_profile(self, config: Config):
        # If the configuration changes, set up a new remote
        if self.remote.host != config.kubectl_host or self.remote.ssh_opts != config.ssh_opts:
            self.remote.close()
            self.remote = RemoteCommand(config.kubectl_host, config.ssh_opts, config.ssh_controlmaster_path)
            # Ensure the master path has an active connection to funnel our commands through
            self.remote.open()
        Kubectl.kubeconfig_fmt = config.kubeconfig_format

    def _check_current(self, desired_type: Optional[str] = None):
        """Check we have a valid current object"""
        if self.current.kind == "":
            raise k8shError("Please select a cluster with 'use' first")
        if desired_type is not None and self.current.kind != desired_type:
            raise k8shError(f"Invalid context: {self.current.kind}, should be {desired_type}")

    def cd(self, val: str):
        self._check_current()
        if not val:
            # Rewind to cluster level
            while self.current.parent is not None:
                self.current = self.current.parent
                print(self.current)
            return
        next_element = self.current
        while val != "":
            next_element, val = next_element.cd(val)
        self.current = next_element

    def ls(self, arg: cmd2.Statement) -> List[kubernetes.KubeObject]:
        self._check_current()
        queries_performed = 0
        # Simple case: no arguments
        if arg.args == "":
            return self.current.children
        # If we have an argument, we have various cases to consider:
        # 1 - is this a glob?
        # 2 - is this a multi-level search?
        search = arg.args
        ptr = [self.current]
        # Split the path in multiple
        for part in search.split("/"):
            matches: List[kubernetes.KubeObject] = []
            # We are listing a directory, just return all elements
            if part == "":
                for obj in ptr:
                    matches.extend(obj.children)
            elif part == "..":
                for obj in ptr:
                    if obj.parent is not None:
                        matches.append(obj.parent)
            else:
                # Non-empty fragment
                for obj in ptr:
                    queries_performed += 1
                    for child in obj.children:
                        if child.match(part):
                            matches.append(child)

            # If we found no matches, stop
            if not matches:
                return []
            # If we performed more queries than the limit, warn the user, return
            if queries_performed > MAX_QUERY_LENGTH:
                print(red("Your request is too wide; to avoid disruptions to the API, you should narrow your pattern."))
                return []
            # Finished finding matches, move the pointer
            ptr = matches
        return ptr

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
        # Switch to the correct config profile
        config = self.config.get(str(arg))
        self._switch_profile(config)
        # Now initialize the first cluster object.
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

    def complete_cd(self, text: str, line, start_index, end_index):
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
            # Special case: the "/" is at the start of the path
            if base == "":
                base = "/"
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
        Context: All but container, service

        Lists all the properties at the current hierarchy level.

        For example, within a namespace, pods will be listed. In a pod,
        containers will be shown.
        """
        try:
            listed = self.ls(arg)
        except k8shError as e:
            print(red(str(e)))
            return

        # Now print the results.
        to_remove = self.current.path
        if not to_remove.endswith("/"):
            to_remove += "/"
        for obj in listed:
            if obj.kind == "cluster":
                print("/")
            else:
                print(obj.path.replace(to_remove, "", 1))

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

    @cmd2.with_category(CAT_CONT)
    def do_sudo(self, arg):
        """
        Usage: sudo <command>
        Context: container

        Runs a command within the container, as root
        """
        try:
            self._check_current("container")
            self.current.rootexec(arg)
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

    @cmd2.with_category(CAT_ADMIN)
    def do_eventlog(self, arg: cmd2.Statement):
        """
        Usage: eventlog
        Context: any

        Shows the events for the pertinent context,
        sorted by event time.
        """
        try:
            self._check_current()
            # TODO: make this an argument when we don't want to support old k8s versions
            self.current.eventlog(".metadata.creationTimestamp")
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_NAV)
    def do_rm(self, arg: cmd2.Statement):
        """
        Usage: rm <path>...
        Context: any, but only objects with a namespace or a cluster as a parent can be deleted

        Removes an object (or multiple ones) from k8s.
        For pods that are part of a Deployment, they will be substituted with another one.
        """
        try:
            self._check_current()
            to_delete: List[kubernetes.KubeObject] = []
            interactive = False
            for argument in arg.arg_list:
                # fat fingers protection: for an "all" glob, we switch to interactive mode automatically
                if argument == "*":
                    interactive = True
                if argument == "-i":
                    interactive = True
                    continue
                matching = self.ls(cmd2.Statement(argument, f"ls {argument}", "ls", [argument]))
                # check we're matching something, and that we can delete that something
                if len(matching) == 0:
                    print(red(f"{argument}: no such object."))
                    continue
                for obj in matching:
                    if obj.parent is None or obj.parent.kind not in ["cluster", "namespace"]:
                        print(red(f"Cannot remove object {obj.path} (from {argument}"))
                    else:
                        to_delete.append(obj)

            ask = interactive and len(to_delete) > 1
            for obj in to_delete:
                if ask:
                    resp = ask_input("Should object {obj.path} be deleted? (y/n)", ["y", "n", "Y", "N", "Yes", "No"])
                    if resp.lower().startswith("n"):
                        continue
                obj.delete()
                print(f"{obj.path} removed")
        except k8shError as e:
            print(red(str(e)))
            return 1
        finally:
            self.current.refresh()


def from_configfile(path: Path) -> KubeCmd:
    """Get a shell from a configuration file"""
    config = setup(path)
    # Configure our remote with the defaults.
    kubectl_remote = RemoteCommand(
        config.default.kubectl_host, config.default.ssh_opts, config.default.ssh_controlmaster_path
    )
    kubectl_remote.open()
    Kubectl.kubeconfig_fmt = config.default.kubeconfig_format
    sh = KubeCmd(kubectl_remote, config)
    return sh


def main():
    configfile = k8shConfigPath()
    sh = from_configfile(configfile)
    sh.cmdloop()
    sh.remote.close()
