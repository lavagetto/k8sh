from pathlib import Path
from unittest import mock

from typing import Optional

import cmd2_ext_test
import pytest
from cmd2 import CommandResult

from k8sh import Config, ConfigProfiles, exec as ex, kubernetes
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
    config = ConfigProfiles(Config(kubectl_host=None, kubeconfig_format=f"KUBECONFIG={kubeconfig_path}"), {})
    remote = mock.MagicMock(spec=ex.RemoteCommand)
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
    out = minikube.app_cmd("use minikube")
    assert isinstance(out, CommandResult)
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
