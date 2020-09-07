import cmd2
import os
import shlex
import copy
from typing import Any, Dict, Optional, Tuple

from k8sh import k8shConfigPath, Ctx, KubeContext, blue, k8shError, red, setup
from k8sh.exec import Kubectl, RemoteCommand

CAT_NAV = "Kubernetes navigation"
CAT_CONT = "Container-level debugging"


class KubeCmd(cmd2.Cmd):
    def __init__(self, ctx: KubeContext, remote: RemoteCommand, *args):
        self.ctx = ctx
        self.remote = remote
        super().__init__(*args)

    def _from_ctx(self) -> Kubectl:
        return Kubectl(
            self.ctx.get(Ctx.CLUSTER), self.ctx.get(Ctx.SERVICE), self.remote
        )

    def _prompt(self):
        layer = self.ctx.current().name.lower()
        if self.ctx.current() == Ctx.ROOT:
            return "NONE ({}) $ ".format(layer)
        cl = red(self.ctx.get(Ctx.CLUSTER))
        path = blue("/" + "/".join(self.ctx.env[1:]))
        return "{}:{} ({})$ ".format(cl, path, layer)

    def preloop(self):
        self.prompt = self._prompt()

    def postcmd(self, stop, line):
        if not stop:
            self.prompt = self._prompt()
        return super().postcmd(stop, line)

    def context_ls(self, *args):
        layer = self.ctx.current()
        if layer == Ctx.ROOT:
            return []
        elif layer == Ctx.CLUSTER:
            return self.namespaces(*args)
        elif layer == Ctx.SERVICE:
            return self.pods(*args)
        elif layer == Ctx.POD:
            res = self.containers()
            return [el["name"] for el in res["containers"]]
        else:
            return []

    def namespaces(self, *args):
        cluster = self.ctx.get(Ctx.CLUSTER)
        ctl = Kubectl(cluster, "admin", self.remote)
        return [
            r["metadata"]["name"] for r in ctl.json("get namespaces", True)["items"]
        ]

    def pods(self, *args):
        ctl = self._from_ctx()
        res = ctl.json("get pods")
        return [r["metadata"]["name"] for r in res["items"]]

    def containers(self) -> Dict[str, Any]:
        if self.ctx.current().value < Ctx.POD:
            raise k8shError("Containers can only be listed at pod level.")
        ctl = self._from_ctx()
        res = ctl.json("get pods " + self.ctx.get(Ctx.POD))
        hostname = res["spec"]["nodeName"]
        return {
            "containers": [
                {"name": r["name"], "ID": r["containerID"]}
                for r in res["status"]["containerStatuses"]
            ],
            "host": hostname,
        }

    def cd(self, val: Optional[str] = None):
        if val is None:
            self.ctx.env = []
        else:
            # Just treat the cd command as a recursive one.
            for single_val in val.split("/"):
                self._cd(single_val)

    def _cd(self, val: str):
        if val == "..":
            try:
                self.ctx.pop()
            except k8shError:
                pass
        else:
            cur = self.ctx.current()
            available = self.context_ls()
            if val in available:
                self.ctx.push(val)
            else:
                raise k8shError(
                    "Could not find {} in {} {}".format(
                        val, cur.name.lower(), self.ctx.get(cur)
                    )
                )

    def _container_info(self) -> Tuple[str, int]:
        res = self.containers()
        remote_host = res["host"]
        for el in res["containers"]:
            if el["name"] == self.ctx.get(Ctx.CONTAINER):
                id = el["ID"].replace("docker://", "")
        return (remote_host, id)

    def ps(self):
        if self.ctx.current() != Ctx.CONTAINER:
            raise k8shError("ps can only be used within a container")
        remote_host, id = self._container_info()
        r = RemoteCommand(remote_host)
        res = r.run_sync(["sudo", "docker", "top", id])
        if res != 0:
            raise k8shError(
                "Executing docker top on {} exited with error code {}".format(
                    remote_host, res
                )
            )

    def nsenter(self, arg):
        """
        Allows you to execute command from the kubernetes worker
        inside the container's namespace. You will have to pass
        the namespaces you want to enter, and the command to execute.
        """
        if self.ctx.current() != Ctx.CONTAINER:
            raise k8shError("nsenter can only be used within a container")
        remote_host, id = self._container_info()
        r = RemoteCommand(remote_host)
        res = r.run(["sudo", "docker", "inspect", "-f", "'{{.State.Pid}}'", id])
        if res.returncode != 0:
            raise k8shError(
                "Error finding the PID of the container: exitcode {}: {}".format(
                    res.returncode, res.stderr.decode()
                )
            )
        else:
            pid = res.stdout.decode().rstrip()
            cmd = ["sudo", "nsenter", "-t", pid] + shlex.split(arg)
            res = r.run_sync(cmd)
            if res != 0:
                raise k8shError(
                    "Command {} exited with return code {}".format(cmd, res)
                )

    def tail(self, arg):
        if self.ctx.current() != Ctx.CONTAINER:
            raise k8shError("nsenter can only be used within a container")
        ctl = self._from_ctx()
        rc = ctl.run_sync(
            "logs {} {} {}".format(
                arg, self.ctx.get(Ctx.POD), self.ctx.get(Ctx.CONTAINER),
            )
        )
        if rc != 0:
            raise k8shError("Could not read the logs")

    #
    #  Interactive shell commands.
    #
    @cmd2.with_category(CAT_NAV)
    def do_use(self, arg):
        """
        Usage: use <cluster>
        Select the cluster to operate on.
        """
        self.ctx.env = [arg]

    def do_exit(self, arg):
        """Exit the program"""
        print("Bye! Keep navigating!")
        return True

    def do_EOF(self, line):
        """Exit the program by typing Ctrl+D"""
        print("")
        return self.do_exit(line)

    @cmd2.with_category(CAT_NAV)
    def do_cd(self, arg):
        """
        Usage: cd <what>
        Change layer of the kubernetes hierarchy.
        Only supports single-level changes of hierarchy, including "..".

        If <what> is omitted, the whole enviroment is reset to the cluster
        level.
        """
        if not arg:
            self.ctx.reset()
            return

        try:
            self.cd(arg)
        except k8shError as e:
            print(red(str(e)))

    def complete_cd(self, text, line, start_index, end_index):
        if text == "":
            res = self.context_ls()
            return res
        if "/" in text:
            base, to_suggest = text.rsplit("/", 1)
        else:
            base = ""
            to_suggest = text
        # Create a copy of our current context we'll re-inject after we're done.
        ctx = KubeContext()
        ctx.env = copy.copy(self.ctx.env)
        try:
            for el in base.split("/"):
                if el == "":
                    break
                if el == "..":
                    self.ctx.pop()
                else:
                    self.ctx.push(el)
            available = self.context_ls()
            if base != "":
                return [
                    base + "/" + el for el in available if el.startswith(to_suggest)
                ]
            else:
                return [el for el in available if el.startswith(text)]
        except Exception:
            # No autocomplete.
            return []
        finally:
            self.ctx.env = ctx.env

    @cmd2.with_category(CAT_NAV)
    def do_ls(self, arg):
        """
        Usage: ls
        Context: All but container

        Lists all the properties at the current hierarchy level.

        For example, within a namespace, pods will be listed. In a pod,
        containers will be shown.
        """
        for el in self.context_ls():
            print(el)

    @cmd2.with_category(CAT_CONT)
    def do_ps(self, arg):
        """
        Usage: ps
        Context: container

        Show processes running in the container
        """
        try:
            self.ps()
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
            self.tail(arg)
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
            self.nsenter(arg)
        except k8shError as e:
            print(red(str(e)))

    @cmd2.with_category(CAT_CONT)
    def do_exec(self, arg):
        """
        Usage: exec <command>
        Context: container

        Runs a command **within** the container
        """
        if self.ctx.current() != Ctx.CONTAINER:
            raise k8shError("nsenter can only be used within a container")
        ctl = self._from_ctx()
        # This needs to run with admin privileges.
        ctl.run_sync(
            "exec {} -c {} -- {}".format(
                self.ctx.get(Ctx.POD), self.ctx.get(Ctx.CONTAINER), arg
            ),
            True,
        )


def from_configfile(path: str) -> KubeCmd:
    """Get a shell from a configuration file"""
    config = setup(path)
    kubectl_remote = RemoteCommand(config.kubectl_host, config.ssh_opts)
    Kubectl.kubeconfig_fmt = config.kubeconfig_format
    ctx = KubeContext()
    sh = KubeCmd(ctx, kubectl_remote)
    return sh


def main():
    configfile = k8shConfigPath()
    sh = from_configfile(configfile)
    sh.cmdloop()
