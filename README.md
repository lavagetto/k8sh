# K8sh

This is a simple interactive shell for interacting with applications running in a kubernetes cluster. It was mostly written over a long weekend to scratch a specific itch I had. I strongly suggest you read the disclaimer in the COPYING.md file before proceeding further.

It assumes you have one host which has full access to the kubernetes api via `kubectl`.
k8sh can be run from that host, or can use ssh to connect to it.
You will also need to be able to ssh into all of your kubernetes workers if you want to use the `nsenter` and `exec` commands of the shell.

## Installation
Just clone the repository and run `python3 setup.py install`, possibly in a virtualenv.

## Configuration

Configuration of the shell is pretty simple, and is done by writing a yaml file.


(Yes, the configuration file is in yaml. It's kubernetes, what did you expect?)

Anyways, the file, located at `~/.config/k8shrc.yaml` can contain any of the following
configuration keys:

* `kubectl_host`: The host to ssh into to run kubectl. If you can run kubectl
  locally, you can omit the setting
* `kubeconfig_format` a python format string that allows k8sh to pick a kubeconfig file to use with kubectl. Can depend on the cluster you're connecting to, and the namespace. So for example, if you want to use the same kubeconfig for all namespaces, just provide a constant string, like `/etc/kubeconfig`. If you prefer to use a specific configuration for every namespace/cluster, you need to provide a python format string including the terms, like `/etc/kube/{cluster}/{namespace}.kubeconfig`. Defaults to `/etc/kubernetes/{namespace}-{cluster}.config` because that's what I use in production.
* `ssh_opts` Options to add to all the ssh connections, as a list.

You can also add different configurations for different clusters by using the `profiles` configuration stanza and adding a key-value mapping of cluster names and
configuration profiles.

## Usage
Well, see the notice in the COPYING file. This
The shell allows to navigate a k8s cluster and dive into the applications.

It treats, quite simplistically, a cluster like a filesystem hierarchy where:
* cluster
* namespace
* pod
* service
* container

are consequent directories in the hierarchy. While the cluster needs to be chosen with `use <cluster>`, all the other levels can be navigated with `cd <what>` and `ls`. For example, let's say we need to inspect the logs of an application called `gatekeeper` running within the `production` cluster, in namespace `auth`

```bash
$ k8sh
# first thing we need to do is to select a cluster
NONE (root) $ use production
# let's see all the available namespaces
production:/ (cluster) $ ls
auth
foo
bar
# let's move to the auth namespace, and navigate to the container
production:/ (cluster) $ cd auth
production:/auth (service) $ ls
tiller-deploy-234321-dsdfs
gk-production-2381-sf84
# The "cd" command supports multiple layers, and autocomplete.
# The autocomplete is pretty limited, and most importantly cannot
# display completions.
production:/auth (service) $ cd gk-production-2381-sf84/main_app
# Now let's tail the logs!
production:/auth/gk-production-2381-sf84/main_app (container) $ tail -f
2020-08-10 09:00:00 Error: the developer was uninspired
2020-08-10 09:00:01 Info: running GC took 4ms
...
# Ctrl-C interrupts tail as you'd expect
# You can also pipe the commands through your own shell:
# The following will save a file to the disk of your machine with the logs
production:/auth/gk-production-2381-sf84/main_app (container) $ tail -f | grep -v uninspired > filtered_gk_logs
#Thanks to the builtin functions of python cmd2, you can run commands
# in your local shell by adding a "!" before the command.
... $ !ls -l filtered_gk_logs
-rw-r--r-- 1 user user 820 Aug 10 09:01  filtered_gk_logs
```

### Commands reference
All commands can be inspected within k8sh by running `help <command>`.
* `cd <k8s-path>` changes the current context to a specific k8s object. The hierarchy is ``<namespace>/<pod>/<container>``
* `exec <command>` Executes the command within the container, using `kubectl-exec`.
* `exit` Exits the application. `Ctrl+D` works too.
* `ls` lists the properties at this level of the hierarchy. Only works when you pick a cluster with `use`
* `nsenter <flags> <command>` Can only be used within a container. Allows to enter the container's namespaces (selected with the usual nsenter flags) and execute a command.
* `ps` Can only be used within a container. Returns the output of `docker top` for that container.
* `use <cluster>` Pick what kubernetes cluster you're connecting to. This will likely change what kubeconfig you're calling.


## FAQ

There is no FAQ. You should really not use this software, did I mention that?



