import json
import shlex
import subprocess
from typing import List, Optional

import attr

from k8sh import k8shError


@attr.s
class RemoteCommand:
    host: Optional[str] = attr.ib()
    ssh_opts: Optional[List[str]] = attr.ib(default=None)

    def _cmd(self, command):
        if self.host is None:
            return command
        else:
            if self.ssh_opts is None:
                opts = []
            else:
                opts = self.ssh_opts
            return ["ssh", "-T", self.host] + opts + command

    def run_sync(self, command: List[str]) -> int:
        """Runs a command on a remote host, and streams the output."""
        ssh = subprocess.Popen(
            self._cmd(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        rc = None
        while rc is None:
            try:
                if ssh.stdout is None:
                    continue
                output = ssh.stdout.readline().decode().rstrip()
                if output != "":
                    print(output)
                rc = ssh.poll()
            except KeyboardInterrupt:
                # Manage ctrl-c
                ssh.terminate()
                # We assume this is what the user intended, no reason to signal error.
                rc = 0
        return rc

    def run(self, command: List[str]) -> subprocess.CompletedProcess:
        """Run a command via ssh."""
        return subprocess.run(self._cmd(command), capture_output=True)


@attr.s
class Kubectl:
    cluster: str = attr.ib()
    namespace: Optional[str] = attr.ib()
    remote: RemoteCommand = attr.ib()
    kubeconfig_fmt = "KUBECONFIG=/etc/kubernetes/{namespace}-{cluster}.config"

    def _kubeconfig(self, admin: bool = False) -> str:
        """Returns the kubeconfig file path."""
        if admin:
            # If the command is to be run as admin, we search for the admin kubeconfig
            return "sudo " + self.kubeconfig_fmt.format(
                namespace="admin", cluster=self.cluster
            )
        else:
            return self.kubeconfig_fmt.format(
                namespace=self.namespace, cluster=self.cluster
            )

    def _kubectl(self, command: str, admin: bool = False) -> List[str]:
        """Returns the full command array for a kubectl invocation."""
        if self.namespace is not None:
            _cmd = "{} kubectl -n {} {}".format(
                self._kubeconfig(admin), self.namespace, command
            )
        else:
            _cmd = "{} kubectl {}".format(self._kubeconfig(admin), command)
        return shlex.split(_cmd)

    def run_sync(self, command: str, admin: bool = False):
        return self.remote.run_sync(self._kubectl(command, admin))

    def run(self, command: str, admin: bool = False) -> subprocess.CompletedProcess:
        return self.remote.run(self._kubectl(command, admin))

    def json(self, command: str, admin: bool = False):
        command += " -o=json"
        outcome = self.run(command, admin)
        if outcome.returncode != 0:
            raise k8shError(
                "Error running {}: process returned with retcode: {}, error: {}".format(
                    command, outcome.returncode, outcome.stderr.decode()
                )
            )
        try:
            return json.loads(outcome.stdout.decode())
        except Exception as e:
            raise k8shError("Error decoding json output: {}".format(str(e)))
