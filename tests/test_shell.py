import subprocess
from pathlib import Path
from typing import Optional
from unittest import mock

import cmd2_ext_test
import pytest
from cmd2 import CommandResult, Statement

from k8sh import Config, ConfigProfiles
from k8sh import exec as ex
from k8sh import kubernetes, red, shell
from k8sh.shell import KubeCmd


class K8shTester(cmd2_ext_test.ExternalTestMixin, KubeCmd):
    """Class to test kubecmd"""

    def __init__(self, *args, **kwargs):
        # gotta have this or neither the plugin or cmd2 will initialize
        super().__init__(*args, **kwargs)


@pytest.fixture
def minikube():
    """Provide a KubeCmd that connects to minikube"""
    kubeconfig_path = Path.home() / ".kube" / "config"
    config = ConfigProfiles(
        Config(kubectl_host=None, kubeconfig_format=f"KUBECONFIG={kubeconfig_path}", ssh_controlmaster_path=""), {}
    )
    remote = mock.MagicMock(spec=ex.RemoteCommand)
    remote.host = config.default.kubectl_host
    remote.ssh_opts = config.default.ssh_opts
    ex.Kubectl.kubeconfig_fmt = config.default.kubeconfig_format
    app = K8shTester(remote, config)
    app.fixture_setup()
    yield app
    app.fixture_teardown()


def _getobj(kind: str, name: str, parent: Optional[kubernetes.KubeObject] = None):
    cls = {
        "cluster": kubernetes.Cluster,
        "namespace": kubernetes.Namespace,
        "pod": kubernetes.Pod,
        "container": kubernetes.Container,
    }
    kubectl = ex.Kubectl("minikube", "default", mock.MagicMock(spec=ex.RemoteCommand))
    if parent is not None:
        obj = cls[kind](name, kubectl, parent)
    else:
        obj = cls[kind](name, kubectl)
    obj._children = []
    if parent is not None:
        parent._children.append(obj)
    if obj.kind == "container":
        # This allows us to later test calls to obj.kubectl.remote in
        # container context
        obj._remote = kubectl.remote
        obj.ID = 123
    return obj


@pytest.fixture
def objtree(minikube):
    cluster = _getobj("cluster", "minikube")
    namespaces = {v: _getobj("namespace", v, cluster) for v in ["default", "kube-system"]}
    default_pods = {v: _getobj("pod", v, namespaces["default"]) for v in ["failoid", "pinkunicorn"]}
    system_pods = {v: _getobj("pod", v, namespaces["kube-system"]) for v in ["coredns", "coretcd"]}
    _ = {v: _getobj("container", v, default_pods["failoid"]) for v in ["http", "envoy"]}
    _ = {v: _getobj("container", v, default_pods["pinkunicorn"]) for v in ["foobar"]}
    _ = {v: _getobj("container", v, system_pods["coredns"]) for v in ["coredns", "prom-dns-exporter"]}
    _ = {v: _getobj("container", v, system_pods["coretcd"]) for v in ["etcd", "nginx"]}
    minikube.current = cluster
    return minikube


def test_use(minikube):
    """Test the use command works as expected"""
    assert minikube.prompt == "NONE (root) $ "
    minikube.app_cmd("set debug true")
    out = minikube.app_cmd("use minikube")
    assert isinstance(out, CommandResult)
    assert out.stderr == ""
    assert isinstance(minikube.current, kubernetes.Cluster)
    assert minikube.current.name == "minikube"
    assert "minikube" in minikube.prompt


def test_cd(objtree):
    """Test the cd command."""
    objtree.app_cmd("set debug true")
    out = objtree.app_cmd("cd default")
    assert isinstance(out, CommandResult)
    assert objtree.current.kind == "namespace"
    assert objtree.current.name == "default"
    assert "/default" in objtree.prompt
    # Relative cd
    out = objtree.app_cmd("cd ../kube-system/pod.coredns/prom-dns-exporter")
    assert objtree.current.name == "prom-dns-exporter"
    assert objtree.current.kind == "container"
    # cd to an inexistent property will go nowhere
    objtree.app_cmd("cd pinkunicorn")
    assert objtree.current.name == "prom-dns-exporter"
    # cd without arguments brings us back to the cluster level
    objtree.app_cmd("cd")
    assert objtree.current.kind == "cluster"


@pytest.mark.parametrize(
    "arg,expected",
    [
        ("", "default\nkube-system"),
        ("test", ""),
        ("default/pod.f*", "default/pod.failoid"),
        ("default/../kube-system/*etcd/*", "kube-system/pod.coretcd/etcd\nkube-system/pod.coretcd/nginx"),
        ("default/..", "/"),
        ("default/../", "default\nkube-system"),
    ],
)
def test_ls(arg, expected, objtree):
    """Test the ls functionality"""
    out = objtree.app_cmd(f"ls {arg}".rstrip())
    assert out.stdout.rstrip() == expected


def test_switch_profile(minikube):
    """Test that switching profile will switch the remote object if needed."""
    minikube.config.get = mock.MagicMock(return_value=Config(kubectl_host="test"))
    mocker = minikube.remote
    with mock.patch("k8sh.shell.RemoteCommand.open") as canopener:
        minikube.app_cmd("use test")
        # The original remote has no kubectl_host, we're now passing a configuration containing a remote host
        mocker.close.assert_called_with()
        # The new one has been "opened"
        canopener.assert_called_with()


def test_cmd_before_use(minikube):
    """Test that if we invoke any command before 'use' a warning message will be emitted."""
    minikube.app_cmd("set debug true")
    for cmd in ["exec ls /", "view", "tail", "cd /test", "ls"]:
        out = minikube.app_cmd(cmd)
        assert out.stderr == ""
        assert out.stdout.rstrip() == red("Please select a cluster with 'use' first")


def test_exec_bad_context(objtree):
    """Test that context-dependent commands error out if used in the wrong context"""
    objtree.app_cmd("set debug true")
    out = objtree.app_cmd("exec ls /")
    assert "Invalid context:" in out.stdout.rstrip()


def test_complete_cd(objtree):
    """Test cd autocompletion"""
    # Case 1: no text
    assert ["default", "kube-system"] == objtree.complete_cd("", 0, 0, 0)
    # Case 2: .. in the path
    objtree.app_cmd("cd default")
    assert ["../kube-system"] == objtree.complete_cd("../ku", 0, 0, 0)
    # Case 3: / in the path
    assert ["/default"] == objtree.complete_cd("/d", 0, 0, 0)
    # Case 4: default
    assert ["pod.failoid"] == objtree.complete_cd("pod.f", 0, 0, 0)
    # Case 5: non-existent completion
    assert [] == objtree.complete_cd("pink", 0, 0, 0)


def test_ls_max_queries(objtree):
    """Test ls query protections"""
    orig_q_length = shell.MAX_QUERY_LENGTH
    shell.MAX_QUERY_LENGTH = 1
    arg = Statement("*/pod.*", "ls */pod.*", "ls")
    assert objtree.ls(arg) == []
    shell.MAX_QUERY_LENGTH = orig_q_length


def test_ps(objtree):
    """Test the ps command."""
    # Check context is protected
    out = objtree.app_cmd("ps")
    assert "Invalid context:" in out.stdout
    objtree.app_cmd("cd default/pod.failoid/http")
    # Now run ps, verify it's doing the right thing
    out = objtree.app_cmd("ps")
    assert "Invalid context:" not in out.stdout
    objtree.current._remote.run_sync.assert_called_with(["sudo", "docker", "top", 123])


def test_tail(objtree):
    """Test the tail command."""
    # Check context is protected
    out = objtree.app_cmd("tail")
    assert "Invalid context:" in out.stdout
    objtree.app_cmd("cd default/pod.failoid/http")
    objtree.app_cmd("tail -f")
    objtree.current.kubectl.remote.run_sync.assert_called_with(
        [
            objtree.current.kubectl.kubeconfig_fmt.format("minikube", "default"),
            "kubectl",
            "-n",
            "default",
            "logs",
            "-f",
            "failoid",
            "http",
        ]
    )


def test_nsenter(objtree):
    """Test the nsenter command"""
    out = objtree.app_cmd("nsenter -n telnet localhost 25")
    assert "Invalid context:" in out.stdout
    objtree.app_cmd("cd default/pod.failoid/http")
    # We need to account for two calls to the remote:
    # The first to get the PID of the container,
    # the second to execute nsenter.
    objtree.current.kubectl.remote.run.return_value = subprocess.CompletedProcess("test", 0, b"456")
    objtree.current.kubectl.remote.run_sync.return_value = subprocess.CompletedProcess("test", 0, b"test output")
    # This also verifies the pipe is interpreted by cmd2
    objtree.app_cmd("nsenter -n telnet localhost 25 | grep pinkunicorn")
    objtree.current.kubectl.remote.run.assert_called_with(
        ["sudo", "docker", "inspect", "-f", "'{{.State.Pid}}'", 123],
    )
    objtree.current.kubectl.remote.run_sync.assert_called_with(
        ["sudo", "nsenter", "-t", "456", "-n", "telnet", "localhost", "25"],
    )


def test_exec(objtree):
    """Test the exec command"""
    # Check context is protected
    out = objtree.app_cmd("exec ls")
    assert "Invalid context:" in out.stdout
    objtree.app_cmd("cd default/pod.failoid/http")
    objtree.app_cmd("exec /bin/bash -c 'for i in ls /srv/app/*.jar; do sha256sum $i; done'")
    objtree.current.kubectl.remote.run_sync.assert_called_with(
        [
            "sudo",
            objtree.current.kubectl.kubeconfig_fmt.format("minikube", "default"),
            "kubectl",
            "-n",
            "default",
            "exec",
            "failoid",
            "-c",
            "http",
            "--",
            "/bin/bash",
            "-c",
            "for i in ls /srv/app/*.jar; do sha256sum $i; done",
        ]
    )


def test_eventlog(objtree):
    """Test fetching the eventlog"""
    # Test 1: in a namespace context we only get events for that ns
    objtree.app_cmd("cd default")
    objtree.current.kubectl.run_sync = mock.MagicMock(return_value=subprocess.CompletedProcess("test", 0))
    objtree.app_cmd("eventlog")
    objtree.current.kubectl.run_sync.assert_called_with("get events --sortBy='.metadata.creationTimestamp'")
    # Test 2: in cluster context, the call should add -A and be an admin call.
    objtree.app_cmd("cd ..")
    assert objtree.current.kind == "cluster"
    objtree.current.kubectl.run_sync = mock.MagicMock(return_value=subprocess.CompletedProcess("test", 0))
    objtree.app_cmd("eventlog")
    objtree.current.kubectl.run_sync.assert_called_with("get events --sortBy='.metadata.creationTimestamp' -A", True)


def test_delete(objtree):
    """Test deleting a pod."""
    mocker = mock.MagicMock(return_value=subprocess.CompletedProcess("test", 0))
    objtree.app_cmd("cd default/pod.failoid")
    objtree.current.kubectl.run = mocker
    objtree.app_cmd("cd ..")
    objtree.app_cmd("rm pod.failoid")
    mocker.assert_called_with("delete pod failoid", True)
    # verify the pod isn't in the output of ls anymore
    out = objtree.app_cmd("ls")
    assert "pod.failoid" not in out.stdout
    output = objtree.app_cmd("rm pod.nothere")
    assert "pod.nothere: no such object" in output.stdout
