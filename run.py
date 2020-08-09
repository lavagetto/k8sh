import os

from k8sh import shell


def main():
    configfile = os.path.expanduser("~/.k8shrc.yaml")
    sh = shell.from_configfile(configfile)
    sh.cmdloop()


if __name__ == "__main__":
    main()
