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
    s = k.Service("service", mockctl, ns)
    p._hostname = "hostname.example.com"
    c = k.Container("container", mockctl, p)
    c.ID = "123"
    return [cl, ns, p, c, s]


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
        if i == 0:
            assert kubeobject.parent is None
        elif i < 4:
            assert kubeobject.parent == hierarchy[i - 1]
        else:
            # the service object is a child of the namespace
            assert kubeobject.parent == hierarchy[1]
        # While the object *has* children, they shouldn't be initialized.
        assert kubeobject._children is None
        assert kubeobject.name == kubeobject.kind


def test_path(hierarchy):
    """Test path output at all levels of the chain"""
    assert hierarchy[0].path == "/"
    assert hierarchy[1].path == "/namespace"
    assert hierarchy[2].path == "/namespace/pods/pod"
    assert hierarchy[3].path == "/namespace/pods/pod/container"


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

# Begin service
def test_service_basics(hierarchy):
    s = hierarchy[4]
    assert s.name == "service"
    assert s.parent == hierarchy[1]
    assert s.children == []
    s.kubectl.remote.run.asset_not_called()
    assert s.path_fragment() == "services/service"
    assert s.path == "/namespace/services/service"


def test_service_cd(hierarchy):
    s = hierarchy[4]
    with pytest.raises(k8sh.k8shError):
        s.cd("something")
    assert s.cd("../..") == (hierarchy[1], "..")


def test_service_get(mockctl):
    s = k.Service("service", mockctl, None)
    # Simplified output of a typical
    # `kubectl -n $namespace get service $service -o=json`
    mockctl.remote.run.return_value = subprocess.CompletedProcess(
        ["kubectl", "get", "pods"],
        0,
        stdout=b"""
{
  "apiVersion": "v1",
  "kind": "Service",
  "metadata": {
    "labels": {
      "app": "test"
    },
     "name": "service",
    "namespace": "namespace",
    "resourceVersion": "7",
    "selfLink": "/api/v1/namespaces/namespace/services/service",
    "uid": "test"
  },
  "spec": {
    "clusterIP": "192.168.0.100",
    "externalTrafficPolicy": "Cluster",
    "ports": [
      {
        "name": "http",
        "nodePort": 3000,
        "port": 3030,
        "protocol": "TCP",
        "targetPort": 3030
      }
    ],
    "selector": {
      "app": "test",
      "release": "release"
    },
    "sessionAffinity": "None",
    "type": "NodePort"
  },
  "status": {
    "loadBalancer": {}
  }
}
""",
    )
    assert s.get() == {
        "name": "namespace/services/service",
        "ports": [{"name": "http", "target": 3030, "nodeport": 3000}],
    }


def test_service_get_fail(mockctl):
    s = k.Service("service", mockctl, None)
    mockctl.remote.run.return_value = subprocess.CompletedProcess(
        ["something"], 1, stdout=b"32@$@36132q", stderr=b"meh."
    )
    with pytest.raises(k8sh.k8shError):
        s.get()
