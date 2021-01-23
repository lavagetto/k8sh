import subprocess
from typing import List
from unittest import mock

import k8sh
import pytest
from k8sh import exec as e
from k8sh import kubernetes as k


@pytest.fixture
def mockctl() -> e.Kubectl:
    return e.Kubectl(
        "cluster", "namespace", mock.MagicMock(autospec=e.RemoteCommand)  # cluster
    )


@pytest.fixture
def hierarchy(mockctl) -> List[k.KubeObject]:
    cl = k.Cluster("cluster", mockctl)
    ns = k.Namespace("namespace", mockctl, cl)
    p = k.Pod("pod", mockctl, ns)
    p._hostname = "hostname.example.com"
    c = k.Container("container", mockctl, p)
    c.ID = "123"
    return [cl, ns, p, c]


@pytest.fixture
def pod(mockctl) -> k.Pod:
    mockctl.remote.run.return_value = subprocess.CompletedProcess(
        ["kubectl", "get", "pods"],
        0,
        stdout=b"""
{
    "spec": {"nodeName": "test"},
    "status": {
        "containerStatuses": [
            {"name": "container1", "containerID": "docker://123"},
            {"name": "container2", "containerID": "docker://567"}
        ]
    }
}
""",
    )
    return k.Pod("apod", mockctl, None)


def test_all_init(hierarchy):
    """Test pod initialization"""
    for i, kubeobject in enumerate(hierarchy):
        if i != 0:
            assert kubeobject.parent == hierarchy[i - 1]
        else:
            assert kubeobject.parent is None
        # While the object *has* children, they shouldn't be initialized.
        assert kubeobject._children is None
        assert kubeobject.name == kubeobject.kind


def test_path(hierarchy):
    """Test path output at all levels of the chain"""
    assert hierarchy[0].path == "/"
    assert hierarchy[1].path == "/namespace"
    assert hierarchy[2].path == "/namespace/pod"
    assert hierarchy[3].path == "/namespace/pod/container"


# Pod-specific tests.
def test_pod_children(pod):
    """Test containers are correctly initialized"""
    containers = pod.children
    assert len(containers) == 2
    assert containers[0].name == "container1"
    assert containers[0].ID == "123"
    pod.kubectl.remote.run.assert_called_with(
        [
            "KUBECONFIG=/etc/kubernetes/namespace-cluster.config",
            "kubectl",
            "-n",
            "namespace",
            "get",
            "pods",
            "apod",
            "-o=json",
        ]
    )


def test_pod_children_failure(mockctl):
    mockctl.remote.run.return_value = subprocess.CompletedProcess([], 1, stderr=b"fail")
    p = k.Pod("apod", mockctl, None)
    with pytest.raises(k8sh.k8shError):
        p.children


def test_pod_hostname(pod):
    assert pod.hostname == "test"


# End pod
